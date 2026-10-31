"""Mirror the debts of the accounting program (Phenix) into the debts table.

The accounting program is the only place debts are paid, so each run receives the
complete list of its open invoices and makes the table agree with it:

- an invoice that is new and belongs to a registered customer is added;
- an invoice already here gets the program's amount, remaining amount and date;
- an invoice that is open here but no longer in the list was settled there: closed;
- an invoice of an unknown (or only similarly named) customer is refused and reported,
  with the same rules as the Excel import.

Every applied run stores what it changed (the debt as it was before), so it can be undone.
A run that would close an unusual share of the open debts is held back unless forced: a
truncated export must not wipe the table.
"""
import json
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.debt_sync import DebtSyncChanges, DebtSyncRuns
from models.debts import Debts
from models.payment_details import PaymentDetails
from services.customer_names import normalize_name, pharmacy_index

SYNC_USER_ID = "phenix_sync"
_EPS = 0.005
_LIST_CAP = 500
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
    """Create the two sync tables on first use (idempotent); never touches the debts table."""
    global _tables_ready
    if _tables_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: DebtSyncRuns.__table__.create(c, checkfirst=True))
    await conn.run_sync(lambda c: DebtSyncChanges.__table__.create(c, checkfirst=True))
    await db.commit()
    _tables_ready = True


@dataclass
class Plan:
    inserts: List[dict] = field(default_factory=list)
    updates: List[dict] = field(default_factory=list)  # {"debt": Debts, "to": {...}, "before": {...}}
    closes: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)
    unchanged: int = 0
    ignored_returns: int = 0
    valid_rows: int = 0
    file_remaining_total: float = 0.0
    open_before: int = 0
    remaining_before: float = 0.0
    blocked_reason: Optional[str] = None


def _snapshot(d: Debts) -> dict:
    return {
        "customer_name": d.customer_name,
        "amount": d.amount,
        "remaining_amount": d.remaining_amount,
        "invoice_date": d.invoice_date,
        "status": d.status,
    }


async def build_plan(db: AsyncSession, rows: List[dict], ignored_returns: int = 0) -> Plan:
    plan = Plan(ignored_returns=ignored_returns)
    existing = {invoice_key(d.invoice_number): d for d in (await db.execute(select(Debts))).scalars().all()}
    index = await pharmacy_index(db)
    plan.open_before = sum(1 for d in existing.values() if d.remaining_amount > _EPS)
    plan.remaining_before = round(sum(d.remaining_amount for d in existing.values() if d.remaining_amount > _EPS), 2)

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
                continue
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
            continue
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

    plan.file_remaining_total = round(plan.file_remaining_total, 2)
    n_close = len(plan.closes)
    if plan.valid_rows == 0:
        plan.blocked_reason = "الملف لا يحتوي أي قائمة صالحة"
    elif n_close > _CLOSE_FLOOR and n_close > _CLOSE_SHARE * max(plan.open_before, 1):
        plan.blocked_reason = (
            f"سيُقفل {n_close} قائمة من أصل {plan.open_before} قائمة مفتوحة (أكثر من {int(_CLOSE_SHARE * 100)}%)؛ "
            "قد يكون الملف ناقصاً. راجع التقرير ثم أعد الإرسال بتأكيد (force)."
        )
    return plan


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
        },
        "totals": {
            "open_debts_before": plan.open_before,
            "remaining_before": plan.remaining_before,
            "remaining_in_file": plan.file_remaining_total,
            "remaining_after": round(remaining_after, 2),
        },
        "blocked_reason": plan.blocked_reason,
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
    }


async def apply_plan(db: AsyncSession, plan: Plan, source: str) -> int:
    """Write the plan in one transaction and record every change. Returns the run id."""
    await ensure_tables(db)
    try:
        summary = report(plan)
        run = DebtSyncRuns(source=source, status="applied", summary=json.dumps({"counts": summary["counts"], "totals": summary["totals"]}))
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
        run.status = "undone"
        run.undone_at = func.now()
        await db.commit()
        return {"run_id": run_id, "restored": restored, "deleted": deleted}
    except Exception:
        await db.rollback()
        raise
