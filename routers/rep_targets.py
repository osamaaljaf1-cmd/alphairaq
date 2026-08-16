import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.rep_targets import RepTargets

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
    that rep+year+month and inserts the new overall amount + product rows."""
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
