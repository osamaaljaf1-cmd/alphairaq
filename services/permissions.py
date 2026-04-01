import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.permissions import Permissions

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class PermissionsService:
    """Service layer for Permissions operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[Permissions]:
        """Create a new permissions"""
        try:
            obj = Permissions(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created permissions with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating permissions: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[Permissions]:
        """Get permissions by ID"""
        try:
            query = select(Permissions).where(Permissions.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching permissions {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of permissionss"""
        try:
            query = select(Permissions)
            count_query = select(func.count(Permissions.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Permissions, field):
                        query = query.where(getattr(Permissions, field) == value)
                        count_query = count_query.where(getattr(Permissions, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Permissions, field_name):
                        query = query.order_by(getattr(Permissions, field_name).desc())
                else:
                    if hasattr(Permissions, sort):
                        query = query.order_by(getattr(Permissions, sort))
            else:
                query = query.order_by(Permissions.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching permissions list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[Permissions]:
        """Update permissions"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Permissions {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated permissions {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating permissions {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete permissions"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"Permissions {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted permissions {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting permissions {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Permissions]:
        """Get permissions by any field"""
        try:
            if not hasattr(Permissions, field_name):
                raise ValueError(f"Field {field_name} does not exist on Permissions")
            result = await self.db.execute(
                select(Permissions).where(getattr(Permissions, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching permissions by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Permissions]:
        """Get list of permissionss filtered by field"""
        try:
            if not hasattr(Permissions, field_name):
                raise ValueError(f"Field {field_name} does not exist on Permissions")
            result = await self.db.execute(
                select(Permissions)
                .where(getattr(Permissions, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Permissions.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching permissionss by {field_name}: {str(e)}")
            raise