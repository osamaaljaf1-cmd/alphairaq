"""Which items a person may order: a person linked to companies orders only those companies' items."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.companies import Companies, Company_products, Company_staff


async def my_companies(db: AsyncSession, user_id: str) -> list:
    """The active companies this person works for (empty: not linked to any)."""
    rows = await db.execute(
        select(Companies)
        .join(Company_staff, Company_staff.company_id == Companies.id)
        .where(Company_staff.user_id == str(user_id), Companies.is_active.is_(True))
        .order_by(Companies.name)
    )
    return list(rows.scalars().all())


async def allowed_product_ids(db: AsyncSession, user_id: str) -> Optional[set[int]]:
    """The items this person may order, or None when they are not linked to any company (all items)."""
    companies = await my_companies(db, user_id)
    if not companies:
        return None
    rows = await db.execute(select(Company_products.product_id).where(Company_products.company_id.in_([c.id for c in companies])))
    return {row[0] for row in rows.all()}
