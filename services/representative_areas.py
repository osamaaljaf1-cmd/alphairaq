import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.representative_areas import Representative_areas

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class Representative_areasService:
    """Service layer for Representative_areas operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Representative_areas]:
        """Create a new representative_areas"""
        try:
            obj = Representative_areas(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created representative_areas with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating representative_areas: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Representative_areas]:
        """Get representative_areas by ID"""
        try:
            query = select(Representative_areas).where(Representative_areas.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching representative_areas {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of representative_areass"""
        try:
            query = select(Representative_areas)
            count_query = select(func.count(Representative_areas.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Representative_areas, field):
                        query = query.where(getattr(Representative_areas, field) == value)
                        count_query = count_query.where(getattr(Representative_areas, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Representative_areas, field_name):
                        query = query.order_by(getattr(Representative_areas, field_name).desc())
                else:
                    if hasattr(Representative_areas, sort):
                        query = query.order_by(getattr(Representative_areas, sort))
            else:
                query = query.order_by(Representative_areas.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching representative_areas list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Representative_areas]:
        """Update representative_areas"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Representative_areas {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated representative_areas {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating representative_areas {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete representative_areas"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Representative_areas {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted representative_areas {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting representative_areas {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Representative_areas]:
        """Get representative_areas by any field"""
        try:
            if not hasattr(Representative_areas, field_name):
                raise ValueError(f"Field {field_name} does not exist on Representative_areas")
            result = await self.db.execute(
                select(Representative_areas).where(getattr(Representative_areas, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching representative_areas by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Representative_areas]:
        """Get list of representative_areass filtered by field"""
        try:
            if not hasattr(Representative_areas, field_name):
                raise ValueError(f"Field {field_name} does not exist on Representative_areas")
            result = await self.db.execute(
                select(Representative_areas)
                .where(getattr(Representative_areas, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Representative_areas.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching representative_areass by {field_name}: {str(e)}")
            raise