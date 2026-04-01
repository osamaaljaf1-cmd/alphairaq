import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.areas import Areas

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class AreasService:
    """Service layer for Areas operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Areas]:
        """Create a new areas"""
        try:
            obj = Areas(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created areas with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating areas: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Areas]:
        """Get areas by ID"""
        try:
            query = select(Areas).where(Areas.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching areas {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of areass"""
        try:
            query = select(Areas)
            count_query = select(func.count(Areas.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Areas, field):
                        query = query.where(getattr(Areas, field) == value)
                        count_query = count_query.where(getattr(Areas, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Areas, field_name):
                        query = query.order_by(getattr(Areas, field_name).desc())
                else:
                    if hasattr(Areas, sort):
                        query = query.order_by(getattr(Areas, sort))
            else:
                query = query.order_by(Areas.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching areas list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Areas]:
        """Update areas"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Areas {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated areas {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating areas {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete areas"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Areas {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted areas {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting areas {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Areas]:
        """Get areas by any field"""
        try:
            if not hasattr(Areas, field_name):
                raise ValueError(f"Field {field_name} does not exist on Areas")
            result = await self.db.execute(
                select(Areas).where(getattr(Areas, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching areas by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Areas]:
        """Get list of areass filtered by field"""
        try:
            if not hasattr(Areas, field_name):
                raise ValueError(f"Field {field_name} does not exist on Areas")
            result = await self.db.execute(
                select(Areas)
                .where(getattr(Areas, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Areas.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching areass by {field_name}: {str(e)}")
            raise