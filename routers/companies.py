"""الشركات: the companies we represent, their items and the staff who work for them.

- GET    /api/v1/companies                    every company with its items and staff
- POST   /api/v1/companies                    (admin) {name}
- PUT    /api/v1/companies/{id}               (admin) {name?, is_active?}
- DELETE /api/v1/companies/{id}               (admin) the company and its links (items and staff stay)
- PUT    /api/v1/companies/{id}/products      (admin) {product_ids}: exactly these items are this company's
                                              (an item of another company moves here)
- PUT    /api/v1/companies/{id}/staff         (admin) {user_ids}: exactly these people work for it
- GET    /api/v1/companies/me                 the caller's companies and the items they may order
                                              (product_ids null: not linked, every item)

A person linked to companies orders only those companies' items (checked again when the order is
submitted); a person with no company link orders any item, as before.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.companies import Companies, Company_products, Company_staff
from models.products import Products
from schemas.auth import UserResponse
from services.access import require_admin, require_view_any
from services.company_scope import allowed_product_ids, my_companies
from services.customer_names import clean_name, normalize_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/companies", tags=["companies"])


class CompanyBody(BaseModel):
    name: str


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


class ProductsBody(BaseModel):
    product_ids: List[int] = []


class StaffBody(BaseModel):
    user_ids: List[str] = []


async def _get(db: AsyncSession, company_id: int) -> Companies:
    c = await db.get(Companies, company_id)
    if not c:
        raise HTTPException(status_code=404, detail="الشركة غير موجودة")
    return c


async def _check_name(db: AsyncSession, name: str, own_id: Optional[int] = None) -> str:
    name = clean_name(name)
    if not name:
        raise HTTPException(status_code=400, detail="اسم الشركة مطلوب")
    for c in (await db.execute(select(Companies))).scalars().all():
        if c.id != own_id and normalize_name(c.name) == normalize_name(name):
            raise HTTPException(status_code=400, detail="توجد شركة بهذا الاسم")
    return name


@router.get("")
async def list_companies(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_view_any(db, current_user, ["permissions", "items"])
    companies = (await db.execute(select(Companies).order_by(Companies.name))).scalars().all()
    products: dict = {}
    for cid, pid in (await db.execute(select(Company_products.company_id, Company_products.product_id))).all():
        products.setdefault(cid, []).append(pid)
    staff: dict = {}
    for cid, uid in (await db.execute(select(Company_staff.company_id, Company_staff.user_id))).all():
        staff.setdefault(cid, []).append(uid)
    return {
        "companies": [
            {"id": c.id, "name": c.name, "is_active": bool(c.is_active), "product_ids": sorted(products.get(c.id, [])), "user_ids": sorted(staff.get(c.id, []))}
            for c in companies
        ]
    }


@router.get("/me")
async def my_company_scope(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    companies = await my_companies(db, str(current_user.id))
    allowed = await allowed_product_ids(db, str(current_user.id))
    return {
        "companies": [{"id": c.id, "name": c.name} for c in companies],
        "product_ids": sorted(allowed) if allowed is not None else None,
    }


@router.post("")
async def add_company(body: CompanyBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    c = Companies(name=await _check_name(db, body.name), is_active=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name}


@router.put("/{company_id}")
async def update_company(company_id: int, body: CompanyUpdate, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    c = await _get(db, company_id)
    if body.name is not None:
        c.name = await _check_name(db, body.name, c.id)
    if body.is_active is not None:
        c.is_active = body.is_active
    await db.commit()
    return {"id": c.id, "name": c.name, "is_active": bool(c.is_active)}


@router.delete("/{company_id}")
async def delete_company(company_id: int, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    c = await _get(db, company_id)
    await db.execute(delete(Company_products).where(Company_products.company_id == c.id))
    await db.execute(delete(Company_staff).where(Company_staff.company_id == c.id))
    await db.delete(c)
    await db.commit()
    logger.info("Company %s deleted by %s", company_id, current_user.id)
    return {"deleted": company_id}


@router.put("/{company_id}/products")
async def set_products(company_id: int, body: ProductsBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    c = await _get(db, company_id)
    wanted = sorted(set(body.product_ids))
    if wanted:
        found = (await db.execute(select(func.count(Products.id)).where(Products.id.in_(wanted)))).scalar() or 0
        if found != len(wanted):
            raise HTTPException(status_code=400, detail="صنف غير موجود")
    # the items no longer this company's, and these items under any other company
    await db.execute(delete(Company_products).where(Company_products.company_id == c.id))
    if wanted:
        await db.execute(delete(Company_products).where(Company_products.product_id.in_(wanted)))
    for pid in wanted:
        db.add(Company_products(company_id=c.id, product_id=pid))
    await db.commit()
    return {"id": c.id, "product_ids": wanted}


@router.put("/{company_id}/staff")
async def set_staff(company_id: int, body: StaffBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    c = await _get(db, company_id)
    wanted = sorted({u.strip() for u in body.user_ids if u and u.strip()})
    await db.execute(delete(Company_staff).where(Company_staff.company_id == c.id))
    for uid in wanted:
        db.add(Company_staff(company_id=c.id, user_id=uid))
    await db.commit()
    return {"id": c.id, "user_ids": wanted}
