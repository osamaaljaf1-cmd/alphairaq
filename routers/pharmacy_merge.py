"""Merge a duplicate pharmacy / doctor record into the one that stays."""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, func, or_, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.activity_logs import Activity_logs
from models.agreements import Agreements
from models.debts import Debts
from models.doctors import Doctors
from models.messages import Messages
from models.orders import Orders
from models.payments import Payments
from models.pharmacies import Pharmacies
from models.returns import Returns
from models.visits import Visits
from schemas.auth import UserResponse
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


class MergeCustomersRequest(BaseModel):
    keep_id: int
    remove_id: int
    dry_run: bool = False


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


@router.post("/merge-pharmacies")
async def merge_pharmacies(
    data: MergeCustomersRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Move everything recorded against `remove_id` (activity/visit reports,
    orders, returns, messages, agreements, debts, payments) onto `keep_id`,
    fill in any contact details the kept record is missing from the removed
    one, then delete the removed record. All in one transaction. With
    `dry_run` it only reports what would be moved."""
    await require_permission(db, current_user, "customers", "delete")

    if data.keep_id == data.remove_id:
        raise HTTPException(status_code=400, detail="لا يمكن دمج الزبون مع نفسه")

    keep = (await db.execute(select(Pharmacies).where(Pharmacies.id == data.keep_id))).scalar_one_or_none()
    remove = (await db.execute(select(Pharmacies).where(Pharmacies.id == data.remove_id))).scalar_one_or_none()
    if not keep or not remove:
        raise HTTPException(status_code=404, detail="أحد الزبونين غير موجود")

    remove_name = _norm(remove.name)

    # Records tied to the pharmacy by id…
    by_id = {
        "orders": (Orders, Orders.pharmacy_id),
        "returns": (Returns, Returns.pharmacy_id),
        "messages": (Messages, Messages.pharmacy_id),
        "agreements": (Agreements, Agreements.pharmacy_id),
    }
    # …and by (free-text) customer name.
    by_name = {
        "debts": (Debts, Debts.customer_name),
        "payments": (Payments, Payments.customer_name),
        "visits": (Visits, Visits.customer_name),
    }
    activity_match = or_(
        Activity_logs.pharmacy_id == remove.id,
        func.lower(func.trim(Activity_logs.pharmacy_name)) == remove_name,
    )

    counts: Dict[str, int] = {}
    counts["activity_logs"] = (
        await db.execute(select(func.count()).select_from(Activity_logs).where(activity_match))
    ).scalar_one()
    for label, (model, col) in by_id.items():
        counts[label] = (
            await db.execute(select(func.count()).select_from(model).where(col == remove.id))
        ).scalar_one()
    for label, (model, col) in by_name.items():
        counts[label] = (
            await db.execute(select(func.count()).select_from(model).where(func.lower(func.trim(col)) == remove_name))
        ).scalar_one()

    if data.dry_run:
        return {"dry_run": True, "keep": keep.name, "remove": remove.name, "moved": counts}

    await db.execute(
        sql_update(Activity_logs)
        .where(activity_match)
        .values(pharmacy_id=keep.id, pharmacy_name=keep.name)
    )
    for _label, (model, col) in by_id.items():
        await db.execute(sql_update(model).where(col == remove.id).values({col.key: keep.id}))
    for _label, (model, col) in by_name.items():
        await db.execute(
            sql_update(model).where(func.lower(func.trim(col)) == remove_name).values({col.key: keep.name})
        )

    # Keep the surviving record's own values; only fill what it's missing.
    for field in ("customer_number", "address", "phone", "area", "area_id", "contact_person", "representative_id"):
        current = getattr(keep, field)
        if current is None or (isinstance(current, str) and not current.strip()):
            incoming = getattr(remove, field)
            if incoming is not None and not (isinstance(incoming, str) and not incoming.strip()):
                setattr(keep, field, incoming)

    await db.execute(sql_delete(Pharmacies).where(Pharmacies.id == remove.id))
    await db.commit()
    logger.info(
        "Merged pharmacy %s (%s) into %s (%s) by user %s: %s",
        remove.id, remove.name, keep.id, keep.name, current_user.id, counts,
    )
    return {"dry_run": False, "keep": keep.name, "remove": remove.name, "moved": counts}


@router.post("/merge-doctors")
async def merge_doctors(
    data: MergeCustomersRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Same as merge-pharmacies, for doctors: move everything recorded against
    `remove_id` onto `keep_id`, fill missing details, delete the duplicate.
    With `dry_run` it only reports what would be moved."""
    await require_permission(db, current_user, "customers", "delete")

    if data.keep_id == data.remove_id:
        raise HTTPException(status_code=400, detail="لا يمكن دمج الطبيب مع نفسه")

    keep = (await db.execute(select(Doctors).where(Doctors.id == data.keep_id))).scalar_one_or_none()
    remove = (await db.execute(select(Doctors).where(Doctors.id == data.remove_id))).scalar_one_or_none()
    if not keep or not remove:
        raise HTTPException(status_code=404, detail="أحد الطبيبين غير موجود")

    remove_name = _norm(remove.name)

    by_id = {
        "orders": (Orders, Orders.doctor_id),
        "returns": (Returns, Returns.doctor_id),
        "messages": (Messages, Messages.doctor_id),
        "agreements": (Agreements, Agreements.doctor_id),
    }
    by_name = {
        "debts": (Debts, Debts.customer_name),
        "payments": (Payments, Payments.customer_name),
        "visits": (Visits, Visits.customer_name),
    }
    activity_match = or_(
        Activity_logs.doctor_id == remove.id,
        func.lower(func.trim(Activity_logs.doctor_name)) == remove_name,
    )

    counts: Dict[str, int] = {}
    counts["activity_logs"] = (
        await db.execute(select(func.count()).select_from(Activity_logs).where(activity_match))
    ).scalar_one()
    for label, (model, col) in by_id.items():
        counts[label] = (
            await db.execute(select(func.count()).select_from(model).where(col == remove.id))
        ).scalar_one()
    for label, (model, col) in by_name.items():
        counts[label] = (
            await db.execute(select(func.count()).select_from(model).where(func.lower(func.trim(col)) == remove_name))
        ).scalar_one()

    if data.dry_run:
        return {"dry_run": True, "keep": keep.name, "remove": remove.name, "moved": counts}

    await db.execute(
        sql_update(Activity_logs)
        .where(activity_match)
        .values(doctor_id=keep.id, doctor_name=keep.name)
    )
    for _label, (model, col) in by_id.items():
        await db.execute(sql_update(model).where(col == remove.id).values({col.key: keep.id}))
    for _label, (model, col) in by_name.items():
        await db.execute(
            sql_update(model).where(func.lower(func.trim(col)) == remove_name).values({col.key: keep.name})
        )

    for field in ("customer_number", "specialty", "phone", "hospital", "area", "area_id", "representative_id", "notes"):
        current = getattr(keep, field)
        if current is None or (isinstance(current, str) and not current.strip()):
            incoming = getattr(remove, field)
            if incoming is not None and not (isinstance(incoming, str) and not incoming.strip()):
                setattr(keep, field, incoming)

    await db.execute(sql_delete(Doctors).where(Doctors.id == remove.id))
    await db.commit()
    logger.info(
        "Merged doctor %s (%s) into %s (%s) by user %s: %s",
        remove.id, remove.name, keep.id, keep.name, current_user.id, counts,
    )
    return {"dry_run": False, "keep": keep.name, "remove": remove.name, "moved": counts}
