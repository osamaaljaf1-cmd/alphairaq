import io
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.debts import Debts
from models.payments import Payments
from models.payment_details import PaymentDetails
from models.payment_handovers import PaymentHandovers
from models.payment_receipt_notes import PaymentReceiptNotes
from schemas.auth import UserResponse
from services.debts import DebtsService
from services.permission_check import require_permission, resolve_user_role
from services.area_scope import get_customer_scope, get_rep_scope, get_scoped_pharmacy_names
from services.customer_names import PharmacyIndex, name_sql, normalize_name, pharmacy_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/debts", tags=["debts"])


# ---------- Pydantic Schemas ----------
class DebtData(BaseModel):
    customer_name: str
    invoice_number: str
    amount: float
    invoice_date: Optional[str] = None
    notes: Optional[str] = None


class DebtResponse(BaseModel):
    id: int
    user_id: str
    customer_name: str
    invoice_number: str
    amount: float
    remaining_amount: float
    invoice_date: Optional[str] = None
    status: str
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DebtListResponse(BaseModel):
    items: List[DebtResponse]
    total: int
    skip: int
    limit: int


class PaymentDebtItem(BaseModel):
    debt_id: int
    amount_applied: float


class PaymentRequest(BaseModel):
    customer_name: str
    debts: List[PaymentDebtItem] = []
    manual_amount: Optional[float] = None  # used when debts is empty: a payment not tied to a specific invoice
    discount: float = 0
    returns_amount: float = 0
    notes: Optional[str] = None  # internal, company only
    receipt_notes: Optional[str] = None  # printed on the customer's receipt


class PaymentResponse(BaseModel):
    id: int
    user_id: str
    customer_name: str
    total_selected: float
    discount: float
    returns_amount: float
    net_received: float
    notes: Optional[str] = None
    receipt_notes: Optional[str] = None
    created_at: Optional[datetime] = None
    rep_name: Optional[str] = None
    receipt_captured_at: Optional[datetime] = None
    status: Optional[str] = None
    canceled_at: Optional[datetime] = None
    canceled_by: Optional[str] = None
    cancel_reason: Optional[str] = None
    handed_over: Optional[bool] = None
    handed_over_at: Optional[datetime] = None
    handed_over_by: Optional[str] = None
    applied_invoices: List[str] = []

    class Config:
        from_attributes = True


class ConfirmHandoverRequest(BaseModel):
    payment_ids: List[int]
    receipt_number: str  # accounting's manual receipt number written on the handover


class HandoverBatch(BaseModel):
    """One signed handover: the payments a rep handed to accounting under a
    single manual receipt number."""
    batch_id: str
    receipt_number: str
    rep_user_id: str
    rep_name: str
    confirmed_at: Optional[datetime] = None
    confirmed_by_name: Optional[str] = None
    payments_count: int
    total_selected: float
    total_discount: float
    total_net: float
    payments: List["PaymentResponse"]


class HandoverArchiveResponse(BaseModel):
    batches: List[HandoverBatch]
    payments_count: int
    total_net: float


class HandoverRepSummary(BaseModel):
    user_id: str
    rep_name: str
    pending_total: float
    payments: List[PaymentResponse]


class HandoverSummaryResponse(BaseModel):
    reps: List[HandoverRepSummary]
    pending_total: float
    confirmed_total: float


class ReceiptImageUpload(BaseModel):
    image_base64: str


MAX_RECEIPT_IMAGE_CHARS = 6_000_000  # ~4.3MB decoded; generous cap for a compressed JPEG


class CancelPaymentRequest(BaseModel):
    reason: Optional[str] = None


class PaymentListResponse(BaseModel):
    items: List[PaymentResponse]
    total: int
    skip: int
    limit: int


class RejectedRow(BaseModel):
    row: int  # Excel row number
    customer_name: str = ""
    invoice_number: str = ""
    amount: str = ""
    invoice_date: str = ""
    reason: str
    similar: str = ""  # registered customer(s) with a similar name, to correct the file


class ImportResult(BaseModel):
    total_rows: int
    imported: int
    skipped: int
    errors: List[str]
    rejected: List[RejectedRow] = []  # every row that was not imported, with its reason


# ---------- Customer Debt Summary ----------
class CustomerDebtSummary(BaseModel):
    customer_name: str
    total_debts: int
    unpaid_count: int
    partial_count: int
    paid_count: int
    total_amount: float
    total_remaining: float
    status: str  # clean, has_debt, overdue


async def _visible_customer_names(db: AsyncSession, current_user: UserResponse):
    """Customer names (trimmed + lowercased) the caller may see debts for, or
    None for everything. Same rep/manager area scoping as the debts list."""
    scope = await get_customer_scope(db, current_user)
    return await get_scoped_pharmacy_names(db, scope)


async def _names_for_request(db: AsyncSession, current_user: UserResponse, rep_id: Optional[int]):
    """Customer names the caller may list. Without ``rep_id`` that is the
    caller's own scope. With ``rep_id`` it is that one representative's scope
    (their areas + customers) — allowed for admin/accounting and other
    unrestricted roles, for a manager only on their own reps, and for a rep
    only on themself."""
    caller = await get_customer_scope(db, current_user)
    if rep_id is None:
        return await get_scoped_pharmacy_names(db, caller)
    if not caller.unrestricted and rep_id not in caller.rep_ids:
        raise HTTPException(status_code=403, detail="هذا المندوب خارج نطاق صلاحياتك")
    return await get_scoped_pharmacy_names(db, await get_rep_scope(db, rep_id))


def _unknown_customer_detail(index: PharmacyIndex, name: str) -> str:
    similar = index.similar(name)
    hint = f" — الاسم المسجّل المشابه: {'، '.join(similar[:3])}" if similar else ""
    return f"الزبون «{name}» غير موجود في قائمة الزبائن{hint}"


async def _ensure_customer_visible(db: AsyncSession, current_user: UserResponse, customer_name: str) -> None:
    names = await _visible_customer_names(db, current_user)
    if names is not None and normalize_name(customer_name) not in names:
        raise HTTPException(status_code=403, detail="هذا الزبون خارج نطاق صلاحياتك")


@router.get("/customer-summary/{customer_name}", response_model=CustomerDebtSummary)
async def get_customer_debt_summary(
    customer_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get debt summary for a specific customer name (customers outside the
    caller's rep/manager scope are refused)."""
    from sqlalchemy import select, func

    await _ensure_customer_visible(db, current_user, customer_name)

    # Get counts by status
    result = await db.execute(
        select(
            func.count(Debts.id).label("total"),
            func.coalesce(func.sum(Debts.amount), 0).label("total_amount"),
            func.coalesce(func.sum(Debts.remaining_amount), 0).label("total_remaining"),
            func.count(Debts.id).filter(Debts.status == "unpaid").label("unpaid_count"),
            func.count(Debts.id).filter(Debts.status == "partial").label("partial_count"),
            func.count(Debts.id).filter(Debts.status == "paid").label("paid_count"),
        ).where(name_sql(Debts.customer_name) == normalize_name(customer_name))
    )
    row = result.one()

    total_remaining = float(row.total_remaining or 0)
    unpaid_count = int(row.unpaid_count or 0)
    partial_count = int(row.partial_count or 0)

    if total_remaining <= 0 and int(row.total or 0) == 0:
        status = "clean"
    elif unpaid_count > 0 or partial_count > 0:
        status = "has_debt"
    else:
        status = "clean"

    return CustomerDebtSummary(
        customer_name=customer_name,
        total_debts=int(row.total or 0),
        unpaid_count=unpaid_count,
        partial_count=partial_count,
        paid_count=int(row.paid_count or 0),
        total_amount=float(row.total_amount or 0),
        total_remaining=total_remaining,
        status=status,
    )


@router.get("/customer-summaries", response_model=List[CustomerDebtSummary])
async def get_all_customer_summaries(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get debt summaries for all customers that have any debts — only the
    caller's own scope for a rep/manager (their areas / their reps' customers),
    everything for admin/accounting and the other roles."""
    from sqlalchemy import select, func, false

    query = (
        select(
            Debts.customer_name,
            func.count(Debts.id).label("total"),
            func.coalesce(func.sum(Debts.amount), 0).label("total_amount"),
            func.coalesce(func.sum(Debts.remaining_amount), 0).label("total_remaining"),
            func.count(Debts.id).filter(Debts.status == "unpaid").label("unpaid_count"),
            func.count(Debts.id).filter(Debts.status == "partial").label("partial_count"),
            func.count(Debts.id).filter(Debts.status == "paid").label("paid_count"),
        )
        .group_by(Debts.customer_name)
        .order_by(Debts.customer_name)
    )
    scoped_names = await _visible_customer_names(db, current_user)
    if scoped_names is not None:
        if scoped_names:
            query = query.where(name_sql(Debts.customer_name).in_(scoped_names))
        else:
            query = query.where(false())
    result = await db.execute(query)
    rows = result.all()

    summaries = []
    for row in rows:
        total_remaining = float(row.total_remaining or 0)
        unpaid_count = int(row.unpaid_count or 0)
        partial_count = int(row.partial_count or 0)

        if total_remaining <= 0:
            status = "clean"
        elif unpaid_count > 0 or partial_count > 0:
            status = "has_debt"
        else:
            status = "clean"

        summaries.append(CustomerDebtSummary(
            customer_name=row.customer_name,
            total_debts=int(row.total or 0),
            unpaid_count=unpaid_count,
            partial_count=partial_count,
            paid_count=int(row.paid_count or 0),
            total_amount=float(row.total_amount or 0),
            total_remaining=total_remaining,
            status=status,
        ))

    return summaries


# ---------- Debt CRUD Routes ----------
@router.get("", response_model=DebtListResponse)
async def list_debts(
    query: str = Query(None),
    customer_search: str = Query(None, description="Partial, case-insensitive search on customer name"),
    sort: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=2000),
    rep_id: Optional[int] = Query(None, description="Only this representative's customers (areas + assigned)"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """List all debts with filtering and pagination.

    Restricted to the caller's own scoped customers when they're a rep or
    manager (matches the same area/rep scoping used for the customers
    list) — admin/accounting and other roles see everything, unrestricted."""
    import json as json_mod

    service = DebtsService(db)
    query_dict = None
    if query:
        try:
            query_dict = json_mod.loads(query)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid query JSON")

    scoped_names = await _names_for_request(db, current_user, rep_id)

    result = await service.get_list(
        skip=skip,
        limit=limit,
        query_dict=query_dict,
        sort=sort,
        customer_search=customer_search,
        scoped_names=scoped_names,
    )
    return result


@router.get("/customers", response_model=List[str])
async def get_customer_names(
    customer_search: str = Query(None, description="Partial, case-insensitive search on customer name"),
    rep_id: Optional[int] = Query(None, description="Only this representative's customers"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get distinct customer names with debts, scoped the same way as the
    debts list itself (see list_debts)."""
    service = DebtsService(db)
    scoped_names = await _names_for_request(db, current_user, rep_id)
    return await service.get_customer_names(customer_search=customer_search, scoped_names=scoped_names)


@router.get("/unpaid/{customer_name}", response_model=List[DebtResponse])
async def get_unpaid_debts(
    customer_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get all unpaid/partial debts for a customer (within the caller's scope)"""
    await _ensure_customer_visible(db, current_user, customer_name)
    service = DebtsService(db)
    return await service.get_unpaid_by_customer(customer_name)


# ---------- Template Download (MUST be before /{debt_id} to avoid route conflict) ----------
@router.get("/template")
async def download_template():
    """Download the Excel template for debt import"""
    # Generate template dynamically using openpyxl
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        wb = Workbook()
        ws = wb.active
        ws.title = "قالب الديون"
        ws.sheet_view.rightToLeft = True

        # Headers
        headers = ["اسم الزبون", "رقم القائمة", "مبلغ الدين", "تاريخ القائمة"]
        header_font = Font(bold=True, size=12, color="FFFFFF")
        header_fill = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        # Sample data rows
        sample_data = [
            ["صيدلية النور", "INV-001", 500000, "2025-01-15"],
            ["صيدلية الأمل", "INV-002", 750000, "2025-02-01"],
            ["صيدلية الشفاء", "INV-003", 320000, "2025-03-10"],
        ]
        for row_idx, row_data in enumerate(sample_data, 2):
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center", vertical="center")

        # Set column widths
        ws.column_dimensions["A"].width = 25
        ws.column_dimensions["B"].width = 18
        ws.column_dimensions["C"].width = 18
        ws.column_dimensions["D"].width = 18

        # Save to buffer
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=debt_template.xlsx",
                "Cache-Control": "no-cache",
            },
        )
    except ImportError:
        # Fallback: generate with pandas
        df = pd.DataFrame({
            "اسم الزبون": ["صيدلية النور", "صيدلية الأمل", "صيدلية الشفاء"],
            "رقم القائمة": ["INV-001", "INV-002", "INV-003"],
            "مبلغ الدين": [500000, 750000, 320000],
            "تاريخ القائمة": ["2025-01-15", "2025-02-01", "2025-03-10"],
        })
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, sheet_name="قالب الديون")
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=debt_template.xlsx",
                "Cache-Control": "no-cache",
            },
        )


def _clean_cell(raw) -> str:
    """Excel cell -> trimmed text. Empty / NaN becomes '' (not the string 'nan'),
    and a whole-number float loses the '.0' pandas adds when the column has a
    blank cell (82907.0 -> 82907)."""
    if raw is None:
        return ""
    try:
        if pd.isna(raw):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    text = str(raw).strip()
    return "" if text.lower() == "nan" else text


def _invoice_key(value: str) -> str:
    """Comparison key for an invoice number: trimmed, lower-cased, without a
    trailing '.0' and, for all-digit numbers, without leading zeros (so '82907',
    ' 82907 ', '82907.0', '082907' and 'INV-1'/'inv-1' all match)."""
    key = (value or "").strip().lower()
    if re.fullmatch(r"\d+\.0", key):
        key = key[:-2]
    if key.isdigit():
        key = key.lstrip("0") or "0"  # Excel drops leading zeros: 082907 == 82907
    return key


@router.post("", response_model=DebtResponse, status_code=201)
async def create_debt(
    data: DebtData,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Create a single debt record (needs add on the debts page)"""
    from sqlalchemy import select

    await require_permission(db, current_user, "debts", "add")
    service = DebtsService(db)
    key = _invoice_key(data.invoice_number)
    normalized = func.lower(func.trim(Debts.invoice_number))
    same = normalized.in_([key, f"{key}.0"])
    if key.isdigit():
        same = or_(same, func.ltrim(normalized, "0").in_([key, f"{key}.0"]))
    existing = (await db.execute(select(Debts.id).where(same))).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"رقم القائمة {data.invoice_number} موجود مسبقاً")

    payload = data.model_dump()
    registered = (await pharmacy_index(db)).registered(payload["customer_name"])
    if not registered:
        raise HTTPException(status_code=400, detail=_unknown_customer_detail(await pharmacy_index(db), payload["customer_name"]))
    payload["customer_name"] = registered
    result = await service.create(payload, user_id=str(current_user.id))
    return result


@router.delete("/{debt_id}")
async def delete_debt(
    debt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete a debt record (requires can_delete permission on the debts page)"""
    await require_permission(db, current_user, "debts", "delete")
    service = DebtsService(db)
    success = await service.delete(debt_id)
    if not success:
        raise HTTPException(status_code=404, detail="الدين غير موجود")
    return {"message": "تم حذف الدين بنجاح"}


@router.delete("")
async def delete_all_debts(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete every debt record. Admin-only, one-off cleanup operation."""
    await require_permission(db, current_user, "debts", "delete")
    role = await resolve_user_role(db, current_user)
    if role != "admin":
        raise HTTPException(status_code=403, detail="هذا الإجراء مقصور على المسؤول")

    from sqlalchemy import delete as sql_delete

    result = await db.execute(sql_delete(Debts))
    await db.commit()
    return {"message": f"تم حذف جميع الديون بنجاح ({result.rowcount} سجل)"}


# ---------- Excel Import ----------
@router.post("/import-excel", response_model=ImportResult)
async def import_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Import debts from Excel file (needs add on the debts page).
    Expected columns: اسم الزبون, رقم القائمة, مبلغ الدين, تاريخ القائمة

    An invoice number that already exists (ignoring case, spaces and a
    trailing '.0') is skipped and reported — it is never added to the
    customer's balance a second time, whether it is unpaid, partly paid or
    already settled, and whether the repeat is in the database or inside
    the same file.
    """
    from sqlalchemy import select

    await require_permission(db, current_user, "debts", "add")
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel (.xlsx أو .xls)")

    try:
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"خطأ في قراءة الملف: {str(e)}")

    # Normalize column names (strip whitespace)
    df.columns = [str(c).strip() for c in df.columns]

    # Map expected Arabic column names
    col_map = {
        "اسم الزبون": "customer_name",
        "رقم القائمة": "invoice_number",
        "مبلغ الدين": "amount",
        "تاريخ القائمة": "invoice_date",
    }

    # Check required columns
    missing = [k for k in ["اسم الزبون", "رقم القائمة", "مبلغ الدين"] if k not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"أعمدة مفقودة في الملف: {', '.join(missing)}. الأعمدة المطلوبة: اسم الزبون، رقم القائمة، مبلغ الدين، تاريخ القائمة",
        )

    df = df.rename(columns=col_map)

    service = DebtsService(db)
    imported = 0
    skipped = 0
    errors: List[str] = []
    seen_invoices = {_invoice_key(inv) for (inv,) in (await db.execute(select(Debts.invoice_number))).all()}
    pharmacies = await pharmacy_index(db)
    rejected: List[RejectedRow] = []

    def reject(row_num, row, reason, customer_name="", invoice_number="", similar=""):
        nonlocal skipped
        skipped += 1
        date_cell = row.get("invoice_date")
        rejected.append(
            RejectedRow(
                row=row_num,
                customer_name=customer_name or _clean_cell(row.get("customer_name")),
                invoice_number=invoice_number or _clean_cell(row.get("invoice_number")),
                amount=_clean_cell(row.get("amount")),
                invoice_date=("" if date_cell is None or pd.isna(date_cell) else str(date_cell).strip()),
                reason=reason,
                similar=similar,
            )
        )

    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row (1-indexed + header)
        try:
            customer_name = _clean_cell(row.get("customer_name"))
            invoice_number = _clean_cell(row.get("invoice_number"))
            amount_raw = row.get("amount", 0)

            if not customer_name or not invoice_number:
                errors.append(f"صف {row_num}: اسم الزبون أو رقم القائمة فارغ")
                reject(row_num, row, "اسم الزبون أو رقم القائمة فارغ")
                continue

            try:
                amount = float(amount_raw)
            except (ValueError, TypeError):
                errors.append(f"صف {row_num}: مبلغ الدين غير صالح ({amount_raw})")
                reject(row_num, row, "مبلغ الدين غير صالح")
                continue

            if math.isnan(amount) or amount <= 0:
                errors.append(f"صف {row_num}: مبلغ الدين يجب أن يكون أكبر من صفر")
                reject(row_num, row, "مبلغ الدين يجب أن يكون أكبر من صفر")
                continue

            # Check for duplicate invoice number
            if _invoice_key(invoice_number) in seen_invoices:
                errors.append(f"صف {row_num}: رقم القائمة {invoice_number} موجود مسبقاً - تم تخطيه")
                reject(row_num, row, "رقم القائمة موجود مسبقاً")
                continue

            # The name must be a registered customer (ignoring only case and
            # spacing) — otherwise no rep would ever see the debt. A similar
            # but different name ("X" for "صيدلية X") is refused, not guessed.
            registered = pharmacies.registered(customer_name)
            if not registered:
                similar = pharmacies.similar(customer_name)
                if similar:
                    errors.append(f"صف {row_num}: «{customer_name}» غير مطابق للزبون المسجّل «{similar[0]}» - لم يُرفع")
                    reject(row_num, row, "الاسم غير مطابق للزبون المسجّل (اسم مشابه)", similar=" ، ".join(similar[:3]))
                else:
                    errors.append(f"صف {row_num}: الزبون «{customer_name}» غير موجود في قائمة الزبائن - لم يُرفع")
                    reject(row_num, row, "الزبون غير موجود في قائمة الزبائن")
                continue

            # Parse date
            invoice_date = None
            date_raw = row.get("invoice_date")
            if pd.notna(date_raw):
                if isinstance(date_raw, datetime):
                    invoice_date = date_raw.strftime("%Y-%m-%d")
                else:
                    invoice_date = str(date_raw).strip()

            customer_name = registered  # the registered spelling (spacing/case cleaned)

            await service.create(
                {
                    "customer_name": customer_name,
                    "invoice_number": invoice_number,
                    "amount": amount,
                    "remaining_amount": amount,
                    "invoice_date": invoice_date,
                    "status": "unpaid",
                },
                user_id=str(current_user.id),
            )
            seen_invoices.add(_invoice_key(invoice_number))
            imported += 1

        except Exception as e:
            errors.append(f"صف {row_num}: خطأ - {str(e)}")
            reject(row_num, row, f"خطأ: {str(e)}")

    return ImportResult(
        total_rows=len(df),
        imported=imported,
        skipped=skipped,
        errors=errors[:20],  # Limit error messages
        rejected=rejected,
    )


# ---------- Payment Routes ----------
@router.post("/pay", response_model=PaymentResponse)
async def process_payment(
    data: PaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Process a payment for selected debts.
    Calculates: net_received = total_selected - discount - returns_amount
    Updates debt statuses accordingly.
    """
    # The note for the customer's receipt lives in its own table (created on first
    # use). Make sure it exists before anything is written: creating it commits.
    receipt_note = (data.receipt_notes or "").strip() or None
    if receipt_note:
        await _ensure_receipt_notes_table(db)

    service = DebtsService(db)

    debt_objects = []
    if data.debts:
        # Validate all debts exist and belong to the customer
        total_selected = 0.0
        for item in data.debts:
            debt = await service.get_by_id(item.debt_id)
            if not debt:
                raise HTTPException(status_code=404, detail=f"الدين رقم {item.debt_id} غير موجود")
            if normalize_name(debt.customer_name) != normalize_name(data.customer_name):
                raise HTTPException(status_code=400, detail=f"الدين رقم {item.debt_id} لا يخص هذا الزبون")
            if debt.status == "paid":
                raise HTTPException(status_code=400, detail=f"القائمة {debt.invoice_number} مسددة بالكامل مسبقاً")
            if item.amount_applied <= 0:
                raise HTTPException(status_code=400, detail="المبلغ المطبق يجب أن يكون أكبر من صفر")
            if item.amount_applied > debt.remaining_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"المبلغ المطبق ({item.amount_applied}) أكبر من المبلغ المتبقي ({debt.remaining_amount}) للقائمة {debt.invoice_number}",
                )
            total_selected += item.amount_applied
            debt_objects.append((debt, item.amount_applied))
    else:
        # No invoice selected: a manual/unallocated payment on account.
        if not data.manual_amount or data.manual_amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="يرجى اختيار قائمة واحدة على الأقل أو إدخال مبلغ يدوي أكبر من صفر",
            )
        total_selected = data.manual_amount

    # Calculate net received
    net_received = total_selected - data.discount - data.returns_amount
    if net_received < 0:
        raise HTTPException(status_code=400, detail="المبلغ الصافي لا يمكن أن يكون سالباً")

    # A rep collecting cash out in the field holds it until accounting
    # confirms receipt (see /payments/confirm-handover); anyone else
    # recording a payment is assumed to already be at the accounting side.
    role = await resolve_user_role(db, current_user)
    is_rep = role == "rep"
    now = datetime.now(timezone.utc)

    # Create payment record
    payment = Payments(
        user_id=str(current_user.id),
        customer_name=data.customer_name,
        total_selected=total_selected,
        discount=data.discount,
        returns_amount=data.returns_amount,
        net_received=net_received,
        notes=(data.notes or "").strip() or None,  # internal, never printed on the receipt
        rep_name=current_user.name or current_user.email,
        status="active",
        handed_over=not is_rep,
        handed_over_at=None if is_rep else now,
        handed_over_by=None if is_rep else str(current_user.id),
    )
    db.add(payment)
    await db.flush()  # Get payment.id
    if receipt_note:
        db.add(PaymentReceiptNotes(payment_id=payment.id, note=receipt_note))
    payment.receipt_notes = receipt_note  # not a column: only carried to the response

    # Create payment details and update debts
    for debt, amount_applied in debt_objects:
        # Create detail record
        detail = PaymentDetails(
            user_id=str(current_user.id),
            payment_id=payment.id,
            debt_id=debt.id,
            amount_applied=amount_applied,
        )
        db.add(detail)

        # Update debt
        new_remaining = debt.remaining_amount - amount_applied
        if new_remaining <= 0.01:  # Float tolerance
            debt.remaining_amount = 0
            debt.status = "paid"
        else:
            debt.remaining_amount = round(new_remaining, 2)
            debt.status = "partial"

    await db.commit()
    await db.refresh(payment)

    logger.info(f"Payment {payment.id} processed: {total_selected} - {data.discount} - {data.returns_amount} = {net_received}")
    return payment


@router.post("/payments/{payment_id}/cancel", response_model=PaymentResponse)
async def cancel_payment(
    payment_id: int,
    data: CancelPaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Cancel a previously recorded payment, restoring any debts it was applied to.
    Requires can_delete permission on the debts page (matches the frontend's
    canDelete('debts') gate on this action)."""
    from sqlalchemy import select

    await require_permission(db, current_user, "debts", "delete")

    result = await db.execute(select(Payments).where(Payments.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="الدفعة غير موجودة")
    if payment.status == "canceled":
        raise HTTPException(status_code=400, detail="هذه الدفعة ملغاة مسبقاً")

    details_result = await db.execute(
        select(PaymentDetails).where(PaymentDetails.payment_id == payment_id)
    )
    details = details_result.scalars().all()

    service = DebtsService(db)
    for detail in details:
        debt = await service.get_by_id(detail.debt_id)
        if not debt:
            # Debt was deleted separately; nothing to restore for this portion.
            logger.warning(f"Cancel payment {payment_id}: debt {detail.debt_id} no longer exists")
            continue
        new_remaining = min(debt.remaining_amount + detail.amount_applied, debt.amount)
        new_remaining = max(new_remaining, 0)
        debt.remaining_amount = round(new_remaining, 2)
        if debt.remaining_amount >= debt.amount - 0.01:
            debt.status = "unpaid"
        elif debt.remaining_amount <= 0.01:
            debt.status = "paid"
        else:
            debt.status = "partial"

    payment.status = "canceled"
    payment.canceled_at = datetime.now(timezone.utc)
    payment.canceled_by = current_user.name or current_user.email
    payment.cancel_reason = data.reason

    await db.commit()
    await db.refresh(payment)

    logger.info(f"Payment {payment_id} canceled by {payment.canceled_by}")
    return payment


@router.get("/payments", response_model=PaymentListResponse)
async def list_payments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=2000),
    customer_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """List payment transactions — only those of the caller's own customers
    for a rep/manager (same scope as the debts list)."""
    from sqlalchemy import select, func, false

    query = select(Payments)
    count_query = select(func.count(Payments.id))

    scoped_names = await _visible_customer_names(db, current_user)
    if scoped_names is not None:
        if scoped_names:
            in_scope = name_sql(Payments.customer_name).in_(scoped_names)
        else:
            in_scope = false()
        query = query.where(in_scope)
        count_query = count_query.where(in_scope)

    if customer_name:
        query = query.where(Payments.customer_name == customer_name)
        count_query = count_query.where(Payments.customer_name == customer_name)

    count_result = await db.execute(count_query)
    total = count_result.scalar()

    result = await db.execute(query.order_by(Payments.id.desc()).offset(skip).limit(limit))
    items = result.scalars().all()

    # Attach the receipt notes (a separate table) to the payments being returned
    if items:
        await _ensure_receipt_notes_table(db)
        notes = await db.execute(
            select(PaymentReceiptNotes.payment_id, PaymentReceiptNotes.note).where(
                PaymentReceiptNotes.payment_id.in_([p.id for p in items])
            )
        )
        by_payment = {pid: note for pid, note in notes.all()}
        for p in items:
            p.receipt_notes = by_payment.get(p.id)

    return {"items": items, "total": total, "skip": skip, "limit": limit}


# ---------- Cash Handover (rep -> accounting) ----------
@router.get("/payments/handover-summary", response_model=HandoverSummaryResponse)
async def get_handover_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Breaks settled payments down into 'still with the rep' (collected but
    not yet handed to accounting) vs 'confirmed received by accounting',
    grouped by rep. Cancelled payments are excluded entirely. Each pending
    payment carries the invoice number(s) it was applied to, so accounting
    can see whether it's a full or partial settlement of a specific invoice
    (or an unallocated manual payment, if the list is empty)."""
    from sqlalchemy import select
    from services.access import visible_user_ids

    handover_query = select(Payments).order_by(Payments.created_at.desc())
    visible_users = await visible_user_ids(db, current_user)
    if visible_users is not None:
        handover_query = handover_query.where(Payments.user_id.in_(visible_users))
    result = await db.execute(handover_query)
    all_payments = result.scalars().all()

    pending_ids = [p.id for p in all_payments if p.status != "canceled" and not p.handed_over]
    invoices_by_payment: dict[int, list[str]] = {}
    if pending_ids:
        detail_rows = await db.execute(
            select(PaymentDetails.payment_id, Debts.invoice_number)
            .join(Debts, Debts.id == PaymentDetails.debt_id)
            .where(PaymentDetails.payment_id.in_(pending_ids))
        )
        for payment_id, invoice_number in detail_rows.all():
            invoices_by_payment.setdefault(payment_id, []).append(invoice_number)

    pending_total = 0.0
    confirmed_total = 0.0
    reps: dict[str, HandoverRepSummary] = {}

    for p in all_payments:
        if p.status == "canceled":
            continue
        if p.handed_over:
            confirmed_total += p.net_received
            continue
        pending_total += p.net_received
        key = p.user_id
        if key not in reps:
            reps[key] = HandoverRepSummary(
                user_id=p.user_id,
                rep_name=p.rep_name or "غير معروف",
                pending_total=0.0,
                payments=[],
            )
        reps[key].pending_total += p.net_received
        payment_resp = PaymentResponse.model_validate(p).model_copy(
            update={"applied_invoices": invoices_by_payment.get(p.id, [])}
        )
        reps[key].payments.append(payment_resp)

    return HandoverSummaryResponse(
        reps=sorted(reps.values(), key=lambda r: -r.pending_total),
        pending_total=pending_total,
        confirmed_total=confirmed_total,
    )


_handover_table_ready = False


async def _ensure_handover_table(db: AsyncSession) -> None:
    """Create the handover-archive table on first use (idempotent). Keeps the
    feature independent of the startup table creation, and — being a separate
    table — never touches the payments table every other query depends on."""
    global _handover_table_ready
    if _handover_table_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: PaymentHandovers.__table__.create(c, checkfirst=True))
    await db.commit()
    _handover_table_ready = True


_receipt_notes_table_ready = False


async def _ensure_receipt_notes_table(db: AsyncSession) -> None:
    """Create the receipt-notes table on first use (idempotent), same pattern as
    the handover archive: no startup/migration step, payments table untouched."""
    global _receipt_notes_table_ready
    if _receipt_notes_table_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: PaymentReceiptNotes.__table__.create(c, checkfirst=True))
    await db.commit()
    _receipt_notes_table_ready = True


async def _display_name(db: AsyncSession, user_id: str) -> Optional[str]:
    from sqlalchemy import select
    from models.representatives import Representatives
    from models.app_users import App_users

    rep = (await db.execute(select(Representatives.name).where(Representatives.user_id == user_id))).scalar_one_or_none()
    if rep:
        return rep
    return (await db.execute(select(App_users.name).where(App_users.user_id == user_id))).scalar_one_or_none()


@router.post("/payments/confirm-handover")
async def confirm_handover(
    request: ConfirmHandoverRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Accounting confirms it physically received the cash a rep collected and
    records the manual receipt number written on that handover. The selected
    payments are marked handed_over (zeroing them out of the rep's pending
    total) and archived together under the receipt number. One handover covers
    one rep. Requires can_delete on the debts page, same gate as cancel_payment."""
    await require_permission(db, current_user, "debts", "delete")

    receipt_number = (request.receipt_number or "").strip()
    if not receipt_number:
        raise HTTPException(status_code=400, detail="رقم الإيصال اليدوي مطلوب")
    if len(receipt_number) > 60:
        raise HTTPException(status_code=400, detail="رقم الإيصال طويل جداً")
    if not request.payment_ids:
        raise HTTPException(status_code=400, detail="يرجى تحديد دفعة واحدة على الأقل")

    from sqlalchemy import select

    await _ensure_handover_table(db)

    result = await db.execute(select(Payments).where(Payments.id.in_(request.payment_ids)))
    payments = [p for p in result.scalars().all() if p.status != "canceled" and not p.handed_over]
    if not payments:
        raise HTTPException(status_code=400, detail="الدفعات المحددة مُسلَّمة أو ملغاة مسبقاً")
    if len({p.user_id for p in payments}) > 1:
        raise HTTPException(status_code=400, detail="يتم التسليم لكل مندوب بإيصال مستقل — حدد دفعات مندوب واحد")

    now = datetime.now(timezone.utc)
    batch_id = uuid.uuid4().hex
    confirmed_by_name = await _display_name(db, str(current_user.id))
    for p in payments:
        p.handed_over = True
        p.handed_over_at = now
        p.handed_over_by = str(current_user.id)
        db.add(PaymentHandovers(
            payment_id=p.id,
            batch_id=batch_id,
            receipt_number=receipt_number,
            rep_user_id=p.user_id,
            confirmed_by=str(current_user.id),
            confirmed_by_name=confirmed_by_name,
            confirmed_at=now,
        ))

    await db.commit()
    logger.info(f"Handover {receipt_number}: {len(payments)} payments confirmed by {current_user.id}")
    return {
        "success": True,
        "confirmed_count": len(payments),
        "batch_id": batch_id,
        "receipt_number": receipt_number,
    }


@router.get("/payments/handover-archive", response_model=HandoverArchiveResponse)
async def get_handover_archive(
    rep_user_id: Optional[str] = None,
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (handover date, inclusive)"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD (handover date, inclusive)"),
    search: Optional[str] = Query(None, description="Receipt number or pharmacy name"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """The archive of confirmed handovers, newest first: one entry per manual
    receipt number (per rep), with the payments it covers. A rep sees their
    own, a manager their team's, accounting/admin everything."""
    from datetime import date as date_cls, timedelta
    from sqlalchemy import select
    from services.access import visible_user_ids

    await _ensure_handover_table(db)

    query = select(PaymentHandovers, Payments).join(Payments, Payments.id == PaymentHandovers.payment_id)
    query = query.where(or_(Payments.status.is_(None), Payments.status != "canceled"))
    visible = await visible_user_ids(db, current_user)
    if visible is not None:
        query = query.where(PaymentHandovers.rep_user_id.in_(visible))
    if rep_user_id:
        query = query.where(PaymentHandovers.rep_user_id == rep_user_id)
    try:
        if date_from:
            query = query.where(PaymentHandovers.confirmed_at >= datetime.combine(date_cls.fromisoformat(date_from), datetime.min.time(), tzinfo=timezone.utc))
        if date_to:
            query = query.where(PaymentHandovers.confirmed_at < datetime.combine(date_cls.fromisoformat(date_to) + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    except ValueError:
        raise HTTPException(status_code=400, detail="تنسيق التاريخ غير صحيح")
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        query = query.where(or_(func.lower(PaymentHandovers.receipt_number).like(like), func.lower(Payments.customer_name).like(like)))

    rows = (await db.execute(query.order_by(PaymentHandovers.confirmed_at.desc(), Payments.id.asc()))).all()

    payment_ids = [pay.id for _, pay in rows]
    invoices_by_payment: dict[int, list[str]] = {}
    if payment_ids:
        detail_rows = await db.execute(
            select(PaymentDetails.payment_id, Debts.invoice_number)
            .join(Debts, Debts.id == PaymentDetails.debt_id)
            .where(PaymentDetails.payment_id.in_(payment_ids))
        )
        for payment_id, invoice_number in detail_rows.all():
            invoices_by_payment.setdefault(payment_id, []).append(invoice_number)

    batches: dict[str, HandoverBatch] = {}
    for handover, pay in rows:
        batch = batches.get(handover.batch_id)
        if batch is None:
            batch = batches[handover.batch_id] = HandoverBatch(
                batch_id=handover.batch_id,
                receipt_number=handover.receipt_number,
                rep_user_id=handover.rep_user_id,
                rep_name=pay.rep_name or "غير معروف",
                confirmed_at=handover.confirmed_at,
                confirmed_by_name=handover.confirmed_by_name,
                payments_count=0,
                total_selected=0.0,
                total_discount=0.0,
                total_net=0.0,
                payments=[],
            )
        batch.payments_count += 1
        batch.total_selected += pay.total_selected or 0
        batch.total_discount += pay.discount or 0
        batch.total_net += pay.net_received or 0
        batch.payments.append(
            PaymentResponse.model_validate(pay).model_copy(update={"applied_invoices": invoices_by_payment.get(pay.id, [])})
        )

    ordered = list(batches.values())
    return HandoverArchiveResponse(
        batches=ordered,
        payments_count=sum(b.payments_count for b in ordered),
        total_net=sum(b.total_net for b in ordered),
    )


# ---------- Payment Receipt Photo (archive) ----------
@router.post("/payments/{payment_id}/receipt-image")
async def upload_receipt_image(
    payment_id: int,
    data: ReceiptImageUpload,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Attach a captured (pharmacy-stamped) receipt photo to a payment record."""
    from sqlalchemy import select

    if not data.image_base64:
        raise HTTPException(status_code=400, detail="الصورة مطلوبة")
    if len(data.image_base64) > MAX_RECEIPT_IMAGE_CHARS:
        raise HTTPException(status_code=400, detail="حجم الصورة كبير جداً")

    result = await db.execute(select(Payments).where(Payments.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="الدفعة غير موجودة")

    payment.receipt_image = data.image_base64
    payment.receipt_captured_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(f"Receipt image saved for payment {payment_id}")
    return {"success": True}


@router.get("/payments/{payment_id}/receipt-image")
async def get_receipt_image(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Fetch the archived receipt photo for a payment, if one was captured."""
    from sqlalchemy import select

    result = await db.execute(select(Payments).where(Payments.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment or not payment.receipt_image:
        raise HTTPException(status_code=404, detail="لا توجد صورة وصل لهذه الدفعة")

    return {
        "payment_id": payment.id,
        "image_base64": payment.receipt_image,
        "captured_at": payment.receipt_captured_at,
    }