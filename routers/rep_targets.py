import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.rep_targets import RepTargets
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rep-targets", tags=["rep-targets"])


class RepTargetsResponse(BaseModel):
    representative_id: int
    year: int
    month: int
    overall_target_amount: Optional[float] = None
    product_targets: Dict[int, float] = {}


class SetRepTargetsRequest(BaseModel):
    representative_id: int
    year: int
    month: int
    overall_target_amount: Optional[float] = None
    product_targets: Dict[int, float] = {}


async def _load_targets(db: AsyncSession, representative_id: int, year: int, month: int) -> RepTargetsResponse:
    result = await db.execute(
        select(RepTargets).where(
            RepTargets.representative_id == representative_id,
            RepTargets.year == year,
            RepTargets.month == month,
        )
    )
    overall = None
    products: Dict[int, float] = {}
    for row in result.scalars().all():
        if row.product_id is None:
            overall = row.target_amount
        else:
            products[row.product_id] = row.target_qty or 0
    return RepTargetsResponse(
        representative_id=representative_id, year=year, month=month,
        overall_target_amount=overall, product_targets=products,
    )


@router.get("", response_model=RepTargetsResponse)
async def get_rep_targets(
    representative_id: int = Query(...),
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """The saved sales target (overall amount + per-product quantities) for one
    representative in one calendar month."""
    return await _load_targets(db, representative_id, year, month)


@router.put("", response_model=RepTargetsResponse)
async def set_rep_targets(
    data: SetRepTargetsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Replace the target for one rep/month: deletes whatever was saved for
    that rep+year+month and inserts the new overall amount + product rows.
    Requires can_edit permission on the target page — this endpoint
    previously had no permission check at all."""
    await require_permission(db, current_user, "target", "edit")

    await db.execute(
        delete(RepTargets).where(
            RepTargets.representative_id == data.representative_id,
            RepTargets.year == data.year,
            RepTargets.month == data.month,
        )
    )

    if data.overall_target_amount is not None:
        db.add(RepTargets(
            representative_id=data.representative_id,
            year=data.year,
            month=data.month,
            product_id=None,
            target_amount=data.overall_target_amount,
        ))

    for product_id, qty in data.product_targets.items():
        if qty and qty > 0:
            db.add(RepTargets(
                representative_id=data.representative_id,
                year=data.year,
                month=data.month,
                product_id=int(product_id),
                target_qty=qty,
            ))

    await db.commit()
    logger.info(f"Targets saved for rep {data.representative_id} {data.year}-{data.month}")
    return await _load_targets(db, data.representative_id, data.year, data.month)


class BulkTargetItem(BaseModel):
    representative_id: int
    product_id: int
    target_qty: float


class BulkSetTargetsRequest(BaseModel):
    year: int
    month: int
    items: List[BulkTargetItem]


class BulkSetTargetsResponse(BaseModel):
    inserted: int
    updated: int


@router.put("/bulk", response_model=BulkSetTargetsResponse)
async def bulk_set_rep_targets(
    data: BulkSetTargetsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Upsert per-rep per-product target quantities for many reps at once
    (e.g. an Excel import with rep name + product name + quantity columns
    covering several representatives). Unlike PUT /rep-targets, this only
    touches the (rep, product) rows given here and leaves every other
    product target already saved for those reps/month untouched. Requires
    can_import permission on the target page, matching the frontend's
    canImport('target') gate on the Excel-import trigger."""
    await require_permission(db, current_user, "target", "import")

    inserted = 0
    updated = 0
    for item in data.items:
        result = await db.execute(
            select(RepTargets).where(
                RepTargets.representative_id == item.representative_id,
                RepTargets.year == data.year,
                RepTargets.month == data.month,
                RepTargets.product_id == item.product_id,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.target_qty = item.target_qty
            updated += 1
        else:
            db.add(RepTargets(
                representative_id=item.representative_id,
                year=data.year,
                month=data.month,
                product_id=item.product_id,
                target_qty=item.target_qty,
            ))
            inserted += 1

    await db.commit()
    logger.info(
        f"Bulk rep targets for {data.year}-{data.month}: {inserted} inserted, {updated} updated"
    )
    return BulkSetTargetsResponse(inserted=inserted, updated=updated)
