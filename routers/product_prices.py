"""أسعار الفروع: each item's price in each price list, and the prices a customer gets.

- GET /api/v1/product-prices                         every price list and its prices {list_id: {product_id: price}}
- PUT /api/v1/product-prices/{list_id}               (admin) {items: [{product_id, price}]}: set prices in a list;
                                                     price null removes it (the item's own price applies)
- GET /api/v1/product-prices/for-pharmacy/{id}       the price list of a customer (by their area in the
                                                     branch tree, inherited from the levels above) and its prices

An item with no price in a list sells at its own price (products.price), so «سعر بغداد» needs nothing
entered and «سعر أربيل» only the items that differ.
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.area_tree import Area_nodes, Price_lists
from models.areas import Areas
from models.pharmacies import Pharmacies
from models.product_prices import Product_prices
from models.products import Products
from schemas.auth import UserResponse
from services.access import require_admin, require_view_any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/product-prices", tags=["product_prices"])


class PriceItem(BaseModel):
    product_id: int
    price: Optional[float] = None


class PricesBody(BaseModel):
    items: List[PriceItem] = []


async def price_list_of_area(db: AsyncSession, area_id: Optional[int]) -> Optional[int]:
    """The price list an area's customers get: its own, else the nearest level above that has one."""
    if area_id is None:
        return None
    parents = dict((await db.execute(select(Areas.id, Areas.parent_area_id))).all())
    own = dict((await db.execute(select(Area_nodes.area_id, Area_nodes.price_list_id))).all())
    cur, seen = area_id, set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        if own.get(cur):
            return own[cur]
        cur = parents.get(cur)
    return None


async def _prices(db: AsyncSession, list_id: int) -> Dict[int, float]:
    rows = await db.execute(select(Product_prices.product_id, Product_prices.price).where(Product_prices.price_list_id == list_id))
    return {pid: float(price) for pid, price in rows.all()}


@router.get("")
async def all_prices(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_view_any(db, current_user, ["items", "orders"])
    lists = (await db.execute(select(Price_lists).order_by(Price_lists.sort, Price_lists.id))).scalars().all()
    prices: Dict[int, Dict[int, float]] = {pl.id: {} for pl in lists}
    for list_id, pid, price in (await db.execute(select(Product_prices.price_list_id, Product_prices.product_id, Product_prices.price))).all():
        prices.setdefault(list_id, {})[pid] = float(price)
    return {"price_lists": [{"id": pl.id, "name": pl.name, "code": pl.code} for pl in lists], "prices": prices}


@router.put("/{list_id}")
async def set_prices(list_id: int, body: PricesBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)  # branch prices: the admin only
    if not await db.get(Price_lists, list_id):
        raise HTTPException(status_code=404, detail="قائمة الأسعار غير موجودة")
    wanted = {i.product_id: i.price for i in body.items}
    if not wanted:
        return {"updated": 0, "removed": 0}
    known = set((await db.execute(select(Products.id).where(Products.id.in_(list(wanted))))).scalars().all())
    missing = [pid for pid in wanted if pid not in known]
    if missing:
        raise HTTPException(status_code=400, detail=f"صنف غير موجود: {missing[:5]}")
    if any(p is not None and p < 0 for p in wanted.values()):
        raise HTTPException(status_code=400, detail="السعر لا يكون سالباً")
    existing = {r.product_id: r for r in (await db.execute(select(Product_prices).where(Product_prices.price_list_id == list_id, Product_prices.product_id.in_(list(wanted))))).scalars().all()}
    updated = removed = 0
    for pid, price in wanted.items():
        row = existing.get(pid)
        if price is None:
            if row is not None:
                await db.delete(row)
                removed += 1
        elif row is None:
            db.add(Product_prices(price_list_id=list_id, product_id=pid, price=float(price)))
            updated += 1
        elif float(row.price) != float(price):
            row.price = float(price)
            updated += 1
    await db.commit()
    logger.info("Prices of list %s: %s set, %s removed by %s", list_id, updated, removed, current_user.id)
    return {"updated": updated, "removed": removed}


@router.get("/for-pharmacy/{pharmacy_id}")
async def prices_for_pharmacy(pharmacy_id: int, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_view_any(db, current_user, ["orders", "customers"])
    ph = await db.get(Pharmacies, pharmacy_id)
    if not ph:
        raise HTTPException(status_code=404, detail="الزبون غير موجود")
    list_id = await price_list_of_area(db, ph.area_id)
    if list_id is None:
        return {"price_list": None, "prices": {}}
    pl = await db.get(Price_lists, list_id)
    return {"price_list": {"id": pl.id, "name": pl.name, "code": pl.code} if pl else None, "prices": await _prices(db, list_id)}
