import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.pharmacies import Pharmacies

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class PharmaciesService:
    """Service layer for Pharmacies operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Pharmacies]:
        """Create a new pharmacies"""
        try:
            obj = Pharmacies(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created pharmacies with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating pharmacies: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Pharmacies]:
        """Get pharmacies by ID"""
        try:
            query = select(Pharmacies).where(Pharmacies.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching pharmacies {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of pharmaciess"""
        try:
            query = select(Pharmacies)
            count_query = select(func.count(Pharmacies.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Pharmacies, field):
                        query = query.where(getattr(Pharmacies, field) == value)
                        count_query = count_query.where(getattr(Pharmacies, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Pharmacies, field_name):
                        query = query.order_by(getattr(Pharmacies, field_name).desc())
                else:
                    if hasattr(Pharmacies, sort):
                        query = query.order_by(getattr(Pharmacies, sort))
            else:
                query = query.order_by(Pharmacies.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching pharmacies list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Pharmacies]:
        """Update pharmacies"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Pharmacies {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated pharmacies {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating pharmacies {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete pharmacies"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Pharmacies {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted pharmacies {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting pharmacies {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Pharmacies]:
        """Get pharmacies by any field"""
        try:
            if not hasattr(Pharmacies, field_name):
                raise ValueError(f"Field {field_name} does not exist on Pharmacies")
            result = await self.db.execute(
                select(Pharmacies).where(getattr(Pharmacies, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching pharmacies by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Pharmacies]:
        """Get list of pharmaciess filtered by field"""
        try:
            if not hasattr(Pharmacies, field_name):
                raise ValueError(f"Field {field_name} does not exist on Pharmacies")
            result = await self.db.execute(
                select(Pharmacies)
                .where(getattr(Pharmacies, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Pharmacies.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching pharmaciess by {field_name}: {str(e)}")
            raise