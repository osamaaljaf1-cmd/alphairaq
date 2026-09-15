import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, false
from sqlalchemy.ext.asyncio import AsyncSession

from models.debts import Debts

logger = logging.getLogger(__name__)


class DebtsService:
    """Service layer for Debts operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Debts]:
        try:
            if user_id:
                data["user_id"] = user_id
            if "remaining_amount" not in data:
                data["remaining_amount"] = data.get("amount", 0)
            obj = Debts(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating debt: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Debts]:
        try:
            result = await self.db.execute(select(Debts).where(Debts.id == obj_id))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching debt {obj_id}: {str(e)}")
            raise

    async def get_by_invoice_number(self, invoice_number: str) -> Optional[Debts]:
        try:
            result = await self.db.execute(
                select(Debts).where(Debts.invoice_number == invoice_number)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching debt by invoice {invoice_number}: {str(e)}")
            raise

    async def get_unpaid_by_customer(self, customer_name: str) -> List[Debts]:
        try:
            result = await self.db.execute(
                select(Debts)
                .where(Debts.customer_name == customer_name)
                .where(Debts.status.in_(["unpaid", "partial"]))
                .order_by(Debts.id.asc())
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"Error fetching unpaid debts for {customer_name}: {str(e)}")
            raise

    async def get_list(
        self,
        skip: int = 0,
        limit: int = 50,
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
        customer_search: Optional[str] = None,
        scoped_names: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        """
        customer_search: partial, case-insensitive match on customer_name
            (the free-text "type to search" filter on the Debts page).
        scoped_names: pharmacy names (trimmed+lowercased) the caller is
            restricted to (see services/area_scope.get_scoped_pharmacy_names).
            None = unrestricted; an empty set = restricted with nothing in
            scope (show nothing), matching the CustomerScope convention.
        """
        try:
            query = select(Debts)
            count_query = select(func.count(Debts.id))

            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Debts, field):
                        query = query.where(getattr(Debts, field) == value)
                        count_query = count_query.where(getattr(Debts, field) == value)

            if customer_search:
                pattern = f"%{customer_search.strip()}%"
                query = query.where(Debts.customer_name.ilike(pattern))
                count_query = count_query.where(Debts.customer_name.ilike(pattern))

            if scoped_names is not None:
                if not scoped_names:
                    query = query.where(false())
                    count_query = count_query.where(false())
                else:
                    name_filter = func.lower(func.trim(Debts.customer_name)).in_(scoped_names)
                    query = query.where(name_filter)
                    count_query = count_query.where(name_filter)

            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith("-"):
                    field_name = sort[1:]
                    if hasattr(Debts, field_name):
                        query = query.order_by(getattr(Debts, field_name).desc())
                else:
                    if hasattr(Debts, sort):
                        query = query.order_by(getattr(Debts, sort))
            else:
                query = query.order_by(Debts.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {"items": items, "total": total, "skip": skip, "limit": limit}
        except Exception as e:
            logger.error(f"Error fetching debts list: {str(e)}")
            raise

    async def get_customer_names(
        self,
        customer_search: Optional[str] = None,
        scoped_names: Optional[set[str]] = None,
    ) -> List[str]:
        """Get distinct customer names that have debts, optionally filtered
        by a partial search and/or restricted to a scoped set of pharmacy
        names (see get_list for the meaning of both params)."""
        try:
            query = select(Debts.customer_name).distinct()

            if customer_search:
                query = query.where(Debts.customer_name.ilike(f"%{customer_search.strip()}%"))

            if scoped_names is not None:
                if not scoped_names:
                    query = query.where(false())
                else:
                    query = query.where(func.lower(func.trim(Debts.customer_name)).in_(scoped_names))

            query = query.order_by(Debts.customer_name)
            result = await self.db.execute(query)
            return [row[0] for row in result.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching customer names: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Debts]:
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                return None
            for key, value in update_data.items():
                if hasattr(obj, key) and key not in ("id", "user_id"):
                    setattr(obj, key, value)
            await self.db.commit()
            await self.db.refresh(obj)
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating debt {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                return False
            await self.db.delete(obj)
            await self.db.commit()
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting debt {obj_id}: {str(e)}")
            raise