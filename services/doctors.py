import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.doctors import Doctors

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class DoctorsService:
    """Service layer for Doctors operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Doctors]:
        """Create a new doctors"""
        try:
            obj = Doctors(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created doctors with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating doctors: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Doctors]:
        """Get doctors by ID"""
        try:
            query = select(Doctors).where(Doctors.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching doctors {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of doctorss"""
        try:
            query = select(Doctors)
            count_query = select(func.count(Doctors.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Doctors, field):
                        query = query.where(getattr(Doctors, field) == value)
                        count_query = count_query.where(getattr(Doctors, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Doctors, field_name):
                        query = query.order_by(getattr(Doctors, field_name).desc())
                else:
                    if hasattr(Doctors, sort):
                        query = query.order_by(getattr(Doctors, sort))
            else:
                query = query.order_by(Doctors.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching doctors list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Doctors]:
        """Update doctors"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Doctors {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated doctors {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating doctors {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete doctors"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Doctors {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted doctors {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting doctors {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Doctors]:
        """Get doctors by any field"""
        try:
            if not hasattr(Doctors, field_name):
                raise ValueError(f"Field {field_name} does not exist on Doctors")
            result = await self.db.execute(
                select(Doctors).where(getattr(Doctors, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching doctors by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Doctors]:
        """Get list of doctorss filtered by field"""
        try:
            if not hasattr(Doctors, field_name):
                raise ValueError(f"Field {field_name} does not exist on Doctors")
            result = await self.db.execute(
                select(Doctors)
                .where(getattr(Doctors, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Doctors.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching doctorss by {field_name}: {str(e)}")
            raise