"""Mirror the debts of the accounting program (Phenix) into the debts table.

The accounting program is the only place debts are paid, so each run receives the
complete list of its open invoices and makes the table agree with it:

- an invoice that is new and belongs to a registered customer is added;
- an invoice already here gets the program's amount, remaining amount and date;
- an invoice that is open here but no longer in the list was settled there: closed;
- an invoice of an unknown (or only similarly named) customer is refused and reported,
  with the same rules as the Excel import.

A rep's collection recorded in the app that accounting has not confirmed yet (not handed
over, not cancelled) is not in the program either, so it is taken off the program's remaining:
the invoice stays as collected here until accounting confirms it and enters it there.
A collection confirmed (or recorded by accounting itself) AFTER the last applied sync gets one more
sync of grace: the program may not show it yet, so the invoice stays collected here until the next
sync, which trusts the program. If the program already shows the payment, nothing is taken off twice.

Every applied run stores what it changed (the debt as it was before), so it can be undone.
A run that would close an unusual share of the open debts is held back unless forced: a
truncated export must not wipe the table.
"""
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.debt_credits import CustomerCredits, DebtSyncCreditChanges
from models.debt_sync import DebtSyncChanges, DebtSyncRuns
from models.debts import Debts
from models.payment_details import PaymentDetails
from models.payments import Payments
from models.representatives import Representatives
from services.area_scope import get_rep_scope, get_scoped_pharmacy_names
from services.customer_names import normalize_name, pharmacy_index

SYNC_USER_ID = "phenix_sync"
_EPS = 0.005
_LIST_CAP = 5000
# held back unless forced: closing more than this share of the open debts (and more than the floor)
_CLOSE_SHARE = 0.30
_CLOSE_FLOOR = 20

_tables_ready = False


def invoice_key(value: str) -> str:
    """Same comparison key as the Excel import (case, spaces, trailing '.0', leading zeros)."""
    key = (value or "").strip().lower()
    if re.fullmatch(r"\d+\.0", key):
        key = key[:-2]
    if key.isdigit():
        key = key.lstrip("0") or "0"
    return key


def status_for(amount: float, remaining: float) -> str:
    if remaining <= _EPS:
        return "paid"
    if remaining < amount - _EPS:
        return "partial"
    return "unpaid"


def _date(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else text


async def ensure_tables(db: AsyncSession) -> None:
    """Create the sync tables (runs, changes, credits) on first use (idempotent); never touches the debts table."""
    global _tables_ready
    if _tables_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: DebtSyncRuns.__table__.create(c, checkfirst=True))
    await conn.run_sync(lambda c: DebtSyncChanges.__table__.create(c, checkfirst=True))
    await conn.run_sync(lambda c: CustomerCredits.__table__.create(c, checkfirst=True))
    await conn.run_sync(lambda c: DebtSyncCreditChanges.__table__.create(c, checkfirst=True))
    await db.commit()
    _tables_ready = True


@dataclass
class Plan:
    inserts: List[dict] = field(default_factory=list)
    updates: List[dict] = field(default_factory=list)  # {"debt": Debts, "to": {...}, "before": {...}}
    closes: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)
    unchanged: int = 0
    pending: List[dict] = field(default_factory=list)  # invoices reduced by collections not yet handed over
    pending_total: float = 0.0
    ignored_returns: int = 0
    valid_rows: int = 0
    file_remaining_total: float = 0.0
    open_before: int = 0
    remaining_before: float = 0.0
    blocked_reason: Optional[str] = None
    # returns / credit notes (negative figures), mirrored only when the sender includes them
    credits_given: bool = False
    credit_inserts: List[dict] = field(default_factory=list)
    credit_updates: List[dict] = field(default_factory=list)  # {"credit": CustomerCredits, "to": {...}, "before": {...}}
    credit_closes: List[dict] = field(default_factory=list)
    credit_rejected: List[dict] = field(default_factory=list)
    credit_unchanged: int = 0
    credits_valid: int = 0
    credits_file_total: float = 0.0
    # the figures per customer (normalized name), for the per-representative comparison
    before_by: Dict[str, float] = field(default_factory=dict)
    file_by: Dict[str, float] = field(default_factory=dict)
    held_by: Dict[str, float] = field(default_factory=dict)
    rejected_by: Dict[str, float] = field(default_factory=dict)
    credits_before_by: Dict[str, float] = field(default_factory=dict)
    credits_file_by: Dict[str, float] = field(default_factory=dict)


def _snapshot(d: Debts) -> dict:
    return {
        "customer_name": d.customer_name,
        "amount": d.amount,
        "remaining_amount": d.remaining_amount,
        "invoice_date": d.invoice_date,
        "status": d.status,
    }


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def pending_collections(db: AsyncSession) -> Tuple[Dict[int, float], Dict[int, float]]:
    """(not confirmed, confirmed since the last applied sync): debt id -> amount collected in the app.

    The first: collected by reps and not handed over to accounting yet. The second: handed over (or
    recorded by accounting) after the last applied sync, so the program may not show it yet; it is
    protected for that one sync only. Without an applied sync yet nothing counts as the second kind
    (the first sync reconciles with the program)."""
    await ensure_tables(db)
    last_run = (await db.execute(select(func.max(DebtSyncRuns.created_at)).where(DebtSyncRuns.status == "applied"))).scalar_one_or_none()
    last_run = _as_utc(last_run) if last_run else None
    stmt = (
        select(PaymentDetails.debt_id, PaymentDetails.amount_applied, Payments.handed_over, Payments.handed_over_at)
        .join(Payments, Payments.id == PaymentDetails.payment_id)
        .where(func.coalesce(Payments.status, "active") != "canceled")
    )
    if last_run is None:
        stmt = stmt.where(Payments.handed_over.is_(False))
    else:  # the exact comparison with the last run is made below, on aware datetimes
        stmt = stmt.where((Payments.handed_over.is_(False)) | (Payments.handed_over_at.isnot(None)))
    not_confirmed: Dict[int, float] = {}
    recent: Dict[int, float] = {}
    for debt_id, amount, handed, handed_at in (await db.execute(stmt)).all():
        if not amount:
            continue
        if handed is False:
            not_confirmed[debt_id] = not_confirmed.get(debt_id, 0.0) + float(amount)
        elif last_run is not None and handed_at is not None and _as_utc(handed_at) > last_run:
            recent[debt_id] = recent.get(debt_id, 0.0) + float(amount)
    return not_confirmed, recent


async def build_plan(db: AsyncSession, rows: List[dict], ignored_returns: int = 0, credits: Optional[List[dict]] = None) -> Plan:
    plan = Plan(ignored_returns=ignored_returns, credits_given=credits is not None)
    pending, recent = await pending_collections(db)
    existing = {invoice_key(d.invoice_number): d for d in (await db.execute(select(Debts))).scalars().all()}
    index = await pharmacy_index(db)
    plan.open_before = sum(1 for d in existing.values() if d.remaining_amount > _EPS)
    plan.remaining_before = round(sum(d.remaining_amount for d in existing.values() if d.remaining_amount > _EPS), 2)
    for d in existing.values():
        if d.remaining_amount > _EPS:
            k = normalize_name(d.customer_name)
            plan.before_by[k] = plan.before_by.get(k, 0.0) + d.remaining_amount

    def add(target: Dict[str, float], name: str, value: float) -> None:
        k = normalize_name(name)
        target[k] = target.get(k, 0.0) + value

    seen: Dict[str, int] = {}
    file_keys = set()

    def reject(i, row, reason, similar=""):
        plan.rejected.append(
            {
                "row": i,
                "customer_name": row.get("customer_name", ""),
                "invoice_number": row.get("invoice_number", ""),
                "amount": row.get("amount"),
                "remaining": row.get("remaining"),
                "invoice_date": row.get("invoice_date") or "",
                "reason": reason,
                "similar": similar,
            }
        )

    for i, row in enumerate(rows, start=1):
        inv = str(row.get("invoice_number") or "").strip()
        name = str(row.get("customer_name") or "").strip()
        amount, remaining = row.get("amount"), row.get("remaining")
        if not inv or not name:
            reject(i, row, "اسم الزبون أو رقم القائمة فارغ")
            continue
        try:
            amount, remaining = float(amount), float(remaining)
        except (TypeError, ValueError):
            reject(i, row, "مبلغ غير صالح")
            continue
        if math.isnan(amount) or math.isnan(remaining):
            reject(i, row, "مبلغ غير صالح")
            continue
        if amount <= _EPS:  # returns / credit notes are ignored (they are counted by the caller)
            plan.ignored_returns += 1
            continue
        if remaining < -_EPS or remaining > amount + _EPS:
            reject(i, row, "الباقي أكبر من المبلغ الكلي أو سالب")
            continue
        key = invoice_key(inv)
        if key in seen:
            reject(i, row, f"رقم القائمة مكرر داخل الملف (الصف {seen[key]})")
            continue
        seen[key] = i
        file_keys.add(key)
        remaining = max(remaining, 0.0)
        date = _date(row.get("invoice_date"))
        plan.valid_rows += 1
        plan.file_remaining_total += remaining

        debt = existing.get(key)
        if debt is not None:
            if normalize_name(debt.customer_name) != normalize_name(name):
                reject(i, row, f"القائمة مسجّلة على زبون آخر بالتطبيق («{debt.customer_name}») - لم تتغير")
                add(plan.rejected_by, name, remaining)
                continue
            held = min(pending.get(debt.id, 0.0), remaining)
            program_remaining = remaining
            remaining = max(remaining - held, 0.0)
            # a collection confirmed since the last sync: the program may not show it yet. What the app
            # holds (its remaining) already has it off, so the program shows it if the program's figure
            # is not higher than the app's; only the difference is taken off, never the amount twice.
            held_recent = max(0.0, min(recent.get(debt.id, 0.0), remaining - debt.remaining_amount))
            remaining = max(remaining - held_recent, 0.0)
            add(plan.file_by, debt.customer_name, program_remaining)
            if held + held_recent > _EPS:
                add(plan.held_by, debt.customer_name, held + held_recent)
            if held > _EPS or held_recent > _EPS:
                plan.pending.append(
                    {
                        "invoice_number": debt.invoice_number,
                        "customer_name": debt.customer_name,
                        "program_remaining": program_remaining,
                        "collected_in_app": round(held + held_recent, 2),
                        "remaining": round(remaining, 2),
                        "confirmed": held_recent > _EPS and held <= _EPS,
                    }
                )
                plan.pending_total += held + held_recent
            to = {"amount": amount, "remaining_amount": remaining, "status": status_for(amount, remaining)}
            if date:
                to["invoice_date"] = date
            changed = (
                abs(debt.amount - amount) > _EPS
                or abs(debt.remaining_amount - remaining) > _EPS
                or (date is not None and (debt.invoice_date or "")[:10] != date)
                or debt.status != to["status"]
            )
            if changed:
                plan.updates.append({"debt": debt, "to": to, "before": _snapshot(debt)})
            else:
                plan.unchanged += 1
            continue

        if remaining <= _EPS:  # already settled and never was here: nothing to add
            plan.unchanged += 1
            continue
        registered = index.registered(name)
        if not registered:
            similar = index.similar(name)
            if similar:
                reject(i, row, "الاسم غير مطابق للزبون المسجّل (اسم مشابه)", similar=" ، ".join(similar[:3]))
            else:
                reject(i, row, "الزبون غير موجود في قائمة الزبائن")
            add(plan.rejected_by, name, remaining)
            continue
        add(plan.file_by, registered, remaining)
        plan.inserts.append(
            {
                "customer_name": registered,
                "invoice_number": inv,
                "amount": amount,
                "remaining_amount": remaining,
                "invoice_date": date,
                "status": status_for(amount, remaining),
            }
        )

    # open here, absent from the program's list: settled there
    for key, debt in existing.items():
        if key not in file_keys and debt.remaining_amount > _EPS:
            plan.closes.append({"debt": debt, "before": _snapshot(debt)})

    if credits is not None:
        await _plan_credits(db, plan, credits, index)

    plan.file_remaining_total = round(plan.file_remaining_total, 2)
    plan.pending_total = round(plan.pending_total, 2)
    n_close = len(plan.closes)
    if plan.valid_rows == 0:
        plan.blocked_reason = "الملف لا يحتوي أي قائمة صالحة"
    elif n_close > _CLOSE_FLOOR and n_close > _CLOSE_SHARE * max(plan.open_before, 1):
        plan.blocked_reason = (
            f"سيُقفل {n_close} قائمة من أصل {plan.open_before} قائمة مفتوحة (أكثر من {int(_CLOSE_SHARE * 100)}%)؛ "
            "قد يكون الملف ناقصاً. راجع التقرير ثم أعد الإرسال بتأكيد (force)."
        )
    return plan


def _credit_snapshot(c: CustomerCredits) -> dict:
    return {
        "customer_name": c.customer_name,
        "amount": c.amount,
        "remaining_amount": c.remaining_amount,
        "invoice_date": c.invoice_date,
        "status": c.status,
    }


async def _plan_credits(db: AsyncSession, plan: Plan, credits: List[dict], index) -> None:
    """Mirror the returns of the program: the same rules as the invoices (new -> added, known -> updated,
    gone from the file -> closed, unknown customer -> refused and listed)."""
    existing = {c.credit_key: c for c in (await db.execute(select(CustomerCredits))).scalars().all()}
    for c in existing.values():
        if c.status == "open" and c.remaining_amount < -_EPS:
            k = normalize_name(c.customer_name)
            plan.credits_before_by[k] = plan.credits_before_by.get(k, 0.0) + c.remaining_amount
    seen = set()

    def reject(i, row, reason, similar=""):
        plan.credit_rejected.append(
            {
                "row": i,
                "customer_name": row.get("customer_name", ""),
                "invoice_number": row.get("invoice_number", ""),
                "remaining": row.get("remaining"),
                "reason": reason,
                "similar": similar,
            }
        )

    for i, row in enumerate(credits, start=1):
        inv = str(row.get("invoice_number") or "").strip()
        name = str(row.get("customer_name") or "").strip()
        try:
            amount, remaining = -abs(float(row.get("amount"))), -abs(float(row.get("remaining")))
        except (TypeError, ValueError):
            reject(i, row, "مبلغ غير صالح")
            continue
        if not inv or not name or math.isnan(amount) or math.isnan(remaining):
            reject(i, row, "اسم الزبون أو رقم المستند فارغ أو المبلغ غير صالح")
            continue
        key = invoice_key(inv)
        if key in seen:
            reject(i, row, "رقم المستند مكرر داخل الملف")
            continue
        seen.add(key)
        registered = index.registered(name)
        if not registered:
            similar = index.similar(name)
            reject(
                i,
                row,
                "الاسم غير مطابق للزبون المسجّل (اسم مشابه)" if similar else "الزبون غير موجود في قائمة الزبائن",
                similar=" ، ".join(similar[:3]) if similar else "",
            )
            continue
        date = _date(row.get("invoice_date"))
        plan.credits_valid += 1
        plan.credits_file_total += remaining
        k = normalize_name(registered)
        plan.credits_file_by[k] = plan.credits_file_by.get(k, 0.0) + remaining
        cr = existing.get(key)
        if cr is None:
            plan.credit_inserts.append(
                {
                    "customer_name": registered,
                    "credit_key": key,
                    "invoice_number": inv,
                    "amount": amount,
                    "remaining_amount": remaining,
                    "invoice_date": date,
                    "status": "open",
                }
            )
            continue
        if normalize_name(cr.customer_name) != k:
            reject(i, row, f"المستند مسجّل على زبون آخر بالتطبيق («{cr.customer_name}») - لم يتغير")
            continue
        to = {"amount": amount, "remaining_amount": remaining, "status": "open"}
        if date:
            to["invoice_date"] = date
        changed = (
            abs(cr.amount - amount) > _EPS
            or abs(cr.remaining_amount - remaining) > _EPS
            or cr.status != "open"
            or (date is not None and (cr.invoice_date or "")[:10] != date)
        )
        if changed:
            plan.credit_updates.append({"credit": cr, "to": to, "before": _credit_snapshot(cr)})
        else:
            plan.credit_unchanged += 1
    for key, cr in existing.items():
        if key not in seen and cr.status == "open" and cr.remaining_amount < -_EPS:
            plan.credit_closes.append({"credit": cr, "before": _credit_snapshot(cr)})
    plan.credits_file_total = round(plan.credits_file_total, 2)


def after_by(plan: Plan) -> Dict[str, float]:
    """The open debts per customer once the plan is applied."""
    out = dict(plan.before_by)

    def add(name: str, value: float) -> None:
        k = normalize_name(name)
        out[k] = out.get(k, 0.0) + value

    for i in plan.inserts:
        add(i["customer_name"], i["remaining_amount"])
    for u in plan.updates:
        add(u["debt"].customer_name, u["to"]["remaining_amount"] - u["before"]["remaining_amount"])
    for c in plan.closes:
        add(c["debt"].customer_name, -c["before"]["remaining_amount"])
    return out


def credits_after_by(plan: Plan) -> Dict[str, float]:
    """The open credits (negative) per customer once the plan is applied."""
    out = dict(plan.credits_before_by)

    def add(name: str, value: float) -> None:
        k = normalize_name(name)
        out[k] = out.get(k, 0.0) + value

    for i in plan.credit_inserts:
        add(i["customer_name"], i["remaining_amount"])
    for u in plan.credit_updates:
        was = u["before"]["remaining_amount"] if u["before"]["status"] == "open" else 0.0
        add(u["credit"].customer_name, u["to"]["remaining_amount"] - was)
    for c in plan.credit_closes:
        add(c["credit"].customer_name, -c["before"]["remaining_amount"])
    return out


async def rep_breakdown(db: AsyncSession, plan: Plan) -> dict:
    """Per representative: what the program shows for the representative's customers, what the app
    holds now and what it will hold after the sync (a customer is the representative's when it is in
    the representative's areas or assigned to them, the same rule as the debts page filter). What is
    left out of the comparison is explained: collections kept at the rep (``held``) and lines of the
    file that are not applied (``rejected``)."""
    after = after_by(plan)
    credits_after = credits_after_by(plan)
    keys = (
        set(plan.before_by) | set(plan.file_by) | set(plan.rejected_by) | set(plan.credits_file_by)
        | set(plan.credits_before_by) | set(after) | set(credits_after)
    )

    def figures(selected) -> dict:
        ks = [k for k in selected if k in keys]
        phenix = sum(plan.file_by.get(k, 0.0) + plan.rejected_by.get(k, 0.0) for k in ks)
        return {
            "customers": sum(1 for k in ks if plan.file_by.get(k) or plan.before_by.get(k) or after.get(k)),
            "phenix": round(phenix, 2),
            "app_before": round(sum(plan.before_by.get(k, 0.0) for k in ks), 2),
            "app_after": round(sum(after.get(k, 0.0) for k in ks), 2),
            "held": round(sum(plan.held_by.get(k, 0.0) for k in ks), 2),
            "rejected": round(sum(plan.rejected_by.get(k, 0.0) for k in ks), 2),
            "credits_phenix": round(sum(plan.credits_file_by.get(k, 0.0) for k in ks), 2),
            "credits_app_before": round(sum(plan.credits_before_by.get(k, 0.0) for k in ks), 2),
            "credits_app_after": round(sum(credits_after.get(k, 0.0) for k in ks), 2),
        }

    reps = (
        await db.execute(select(Representatives.id, Representatives.name).where(Representatives.role == "rep").order_by(Representatives.id))
    ).all()
    rows, assigned, overlap = [], set(), 0
    for rid, name in reps:
        names = await get_scoped_pharmacy_names(db, await get_rep_scope(db, rid))
        selected = (names or set()) & keys
        overlap += len(selected & assigned)
        assigned |= selected
        row = figures(selected)
        if row["customers"] or any(abs(row[k]) > 0.005 for k in ("phenix", "app_before", "app_after", "credits_phenix", "credits_app_after")):
            rows.append({"rep_id": rid, "rep_name": name or f"#{rid}", **row})
    return {
        "reps": rows,
        "unassigned": figures(keys - assigned),  # customers of no representative (or not registered)
        "total": figures(keys),
        "shared_customers": overlap,  # in more than one representative's list: counted for each
    }


def report(plan: Plan) -> dict:
    def short(d: Debts) -> dict:
        return {"invoice_number": d.invoice_number, "customer_name": d.customer_name}

    remaining_after = plan.remaining_before
    remaining_after += sum(i["remaining_amount"] for i in plan.inserts)
    remaining_after += sum(u["to"]["remaining_amount"] - u["before"]["remaining_amount"] for u in plan.updates)
    remaining_after -= sum(c["before"]["remaining_amount"] for c in plan.closes)
    return {
        "counts": {
            "file_rows_valid": plan.valid_rows,
            "ignored_returns": plan.ignored_returns,
            "to_insert": len(plan.inserts),
            "to_update": len(plan.updates),
            "to_close": len(plan.closes),
            "unchanged": plan.unchanged,
            "rejected": len(plan.rejected),
            "kept_for_pending_collections": len(plan.pending),
        },
        "totals": {
            "open_debts_before": plan.open_before,
            "remaining_before": plan.remaining_before,
            "remaining_in_file": plan.file_remaining_total,
            "remaining_after": round(remaining_after, 2),
            "pending_collections_total": plan.pending_total,
        },
        "blocked_reason": plan.blocked_reason,
        "pending_collections": plan.pending[:_LIST_CAP],
        "inserted": [
            {k: i[k] for k in ("invoice_number", "customer_name", "amount", "remaining_amount")} for i in plan.inserts[:_LIST_CAP]
        ],
        "updated": [
            {
                **short(u["debt"]),
                "remaining_from": u["before"]["remaining_amount"],
                "remaining_to": u["to"]["remaining_amount"],
                "amount_from": u["before"]["amount"],
                "amount_to": u["to"]["amount"],
            }
            for u in plan.updates[:_LIST_CAP]
        ],
        "closed": [{**short(c["debt"]), "remaining_was": c["before"]["remaining_amount"]} for c in plan.closes[:_LIST_CAP]],
        "rejected": plan.rejected[:2000],
        "lists_truncated": any(len(x) > _LIST_CAP for x in (plan.inserts, plan.updates, plan.closes)),
        "credits": _credits_report(plan),
    }


def _credits_report(plan: Plan) -> Optional[dict]:
    if not plan.credits_given:
        return None
    before = round(sum(plan.credits_before_by.values()), 2)
    after = round(sum(credits_after_by(plan).values()), 2)
    return {
        "counts": {
            "file": plan.credits_valid,
            "to_insert": len(plan.credit_inserts),
            "to_update": len(plan.credit_updates),
            "to_close": len(plan.credit_closes),
            "unchanged": plan.credit_unchanged,
            "rejected": len(plan.credit_rejected),
        },
        "totals": {"in_file": plan.credits_file_total, "app_before": before, "app_after": after},
        "inserted": [
            {"invoice_number": i["invoice_number"], "customer_name": i["customer_name"], "remaining": i["remaining_amount"]}
            for i in plan.credit_inserts[:_LIST_CAP]
        ],
        "updated": [
            {
                "invoice_number": u["credit"].invoice_number,
                "customer_name": u["credit"].customer_name,
                "remaining_from": u["before"]["remaining_amount"],
                "remaining_to": u["to"]["remaining_amount"],
            }
            for u in plan.credit_updates[:_LIST_CAP]
        ],
        "closed": [
            {"invoice_number": c["credit"].invoice_number, "customer_name": c["credit"].customer_name, "remaining_was": c["before"]["remaining_amount"]}
            for c in plan.credit_closes[:_LIST_CAP]
        ],
        "rejected": plan.credit_rejected[:2000],
    }


async def apply_plan(db: AsyncSession, plan: Plan, source: str) -> int:
    """Write the plan in one transaction and record every change. Returns the run id."""
    await ensure_tables(db)
    try:
        summary = report(plan)
        run = DebtSyncRuns(
            source=source,
            status="applied",
            summary=json.dumps({"counts": summary["counts"], "totals": summary["totals"], "credits": (summary["credits"] or {}).get("counts")}),
        )
        db.add(run)
        await db.flush()
        for item in plan.inserts:
            debt = Debts(user_id=SYNC_USER_ID, **item)
            db.add(debt)
            await db.flush()
            db.add(DebtSyncChanges(run_id=run.id, debt_id=debt.id, action="insert", before=None))
        for u in plan.updates:
            for k, v in u["to"].items():
                setattr(u["debt"], k, v)
            db.add(DebtSyncChanges(run_id=run.id, debt_id=u["debt"].id, action="update", before=json.dumps(u["before"])))
        for c in plan.closes:
            c["debt"].remaining_amount = 0.0
            c["debt"].status = "paid"
            db.add(DebtSyncChanges(run_id=run.id, debt_id=c["debt"].id, action="close", before=json.dumps(c["before"])))
        for item in plan.credit_inserts:
            credit = CustomerCredits(**item)
            db.add(credit)
            await db.flush()
            db.add(DebtSyncCreditChanges(run_id=run.id, credit_id=credit.id, action="insert", before=None))
        for u in plan.credit_updates:
            for k, v in u["to"].items():
                setattr(u["credit"], k, v)
            db.add(DebtSyncCreditChanges(run_id=run.id, credit_id=u["credit"].id, action="update", before=json.dumps(u["before"])))
        for c in plan.credit_closes:
            c["credit"].remaining_amount = 0.0
            c["credit"].status = "closed"
            db.add(DebtSyncCreditChanges(run_id=run.id, credit_id=c["credit"].id, action="close", before=json.dumps(c["before"])))
        await db.commit()
        return run.id
    except Exception:
        await db.rollback()
        raise


class UndoError(Exception):
    pass


async def undo_run(db: AsyncSession, run_id: int) -> dict:
    """Put every debt of the run back as it was. Only the latest applied run can be undone
    (an older one would overwrite what later runs changed)."""
    await ensure_tables(db)
    run = (await db.execute(select(DebtSyncRuns).where(DebtSyncRuns.id == run_id))).scalar_one_or_none()
    if run is None:
        raise UndoError("المزامنة غير موجودة")
    if run.status == "undone":
        raise UndoError("هذه المزامنة تم التراجع عنها مسبقاً")
    latest = (
        await db.execute(select(func.max(DebtSyncRuns.id)).where(DebtSyncRuns.status == "applied"))
    ).scalar_one()
    if latest != run.id:
        raise UndoError(f"يمكن التراجع عن آخر مزامنة فقط (رقم {latest})")
    changes = (await db.execute(select(DebtSyncChanges).where(DebtSyncChanges.run_id == run_id))).scalars().all()
    inserted = [c.debt_id for c in changes if c.action == "insert"]
    if inserted:
        paid_on = (
            await db.execute(select(func.count()).select_from(PaymentDetails).where(PaymentDetails.debt_id.in_(inserted)))
        ).scalar_one()
        if paid_on:
            raise UndoError(f"لا يمكن التراجع: {paid_on} دفعة سُجّلت على قوائم أضافتها هذه المزامنة")
    try:
        restored = deleted = 0
        for c in changes:
            debt = (await db.execute(select(Debts).where(Debts.id == c.debt_id))).scalar_one_or_none()
            if c.action == "insert":
                if debt is not None:
                    await db.delete(debt)
                    deleted += 1
            elif debt is not None and c.before:
                for k, v in json.loads(c.before).items():
                    setattr(debt, k, v)
                restored += 1
        credit_changes = (await db.execute(select(DebtSyncCreditChanges).where(DebtSyncCreditChanges.run_id == run_id))).scalars().all()
        for c in credit_changes:
            credit = (await db.execute(select(CustomerCredits).where(CustomerCredits.id == c.credit_id))).scalar_one_or_none()
            if c.action == "insert":
                if credit is not None:
                    await db.delete(credit)
            elif credit is not None and c.before:
                for k, v in json.loads(c.before).items():
                    setattr(credit, k, v)
        run.status = "undone"
        run.undone_at = func.now()
        await db.commit()
        return {"run_id": run_id, "restored": restored, "deleted": deleted}
    except Exception:
        await db.rollback()
        raise
