import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.agreements import Agreements

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class AgreementsService:
    """Service layer for Agreements operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Agreements]:
        """Create a new agreements"""
        try:
            obj = Agreements(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created agreements with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating agreements: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Agreements]:
        """Get agreements by ID"""
        try:
            query = select(Agreements).where(Agreements.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching agreements {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of agreementss"""
        try:
            query = select(Agreements)
            count_query = select(func.count(Agreements.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Agreements, field):
                        query = query.where(getattr(Agreements, field) == value)
                        count_query = count_query.where(getattr(Agreements, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Agreements, field_name):
                        query = query.order_by(getattr(Agreements, field_name).desc())
                else:
                    if hasattr(Agreements, sort):
                        query = query.order_by(getattr(Agreements, sort))
            else:
                query = query.order_by(Agreements.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching agreements list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Agreements]:
        """Update agreements"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Agreements {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated agreements {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating agreements {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete agreements"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Agreements {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted agreements {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting agreements {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Agreements]:
        """Get agreements by any field"""
        try:
            if not hasattr(Agreements, field_name):
                raise ValueError(f"Field {field_name} does not exist on Agreements")
            result = await self.db.execute(
                select(Agreements).where(getattr(Agreements, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching agreements by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Agreements]:
        """Get list of agreementss filtered by field"""
        try:
            if not hasattr(Agreements, field_name):
                raise ValueError(f"Field {field_name} does not exist on Agreements")
            result = await self.db.execute(
                select(Agreements)
                .where(getattr(Agreements, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Agreements.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching agreementss by {field_name}: {str(e)}")
            raise