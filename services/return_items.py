import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.return_items import Return_items

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class Return_itemsService:
    """Service layer for Return_items operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Return_items]:
        """Create a new return_items"""
        try:
            if user_id:
                data['user_id'] = user_id
            obj = Return_items(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created return_items with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating return_items: {str(e)}")
            raise

    async def check_ownership(self, obj_id: int, user_id: str) -> bool:
        """Check if user owns this record"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            return obj is not None
        except Exception as e:
            logger.error(f"Error checking ownership for return_items {obj_id}: {str(e)}")
            return False

    async def get_by_id(self, obj_id: int, user_id: Optional[str] = None) -> Optional[Return_items]:
        """Get return_items by ID (user can only see their own records)"""
        try:
            query = select(Return_items).where(Return_items.id == obj_id)
            if user_id:
                query = query.where(Return_items.user_id == user_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching return_items {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        user_id: Optional[str] = None,
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of return_itemss (user can only see their own records)"""
        try:
            query = select(Return_items)
            count_query = select(func.count(Return_items.id))
            
            if user_id:
                query = query.where(Return_items.user_id == user_id)
                count_query = count_query.where(Return_items.user_id == user_id)
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Return_items, field):
                        query = query.where(getattr(Return_items, field) == value)
                        count_query = count_query.where(getattr(Return_items, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Return_items, field_name):
                        query = query.order_by(getattr(Return_items, field_name).desc())
                else:
                    if hasattr(Return_items, sort):
                        query = query.order_by(getattr(Return_items, sort))
            else:
                query = query.order_by(Return_items.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching return_items list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Return_items]:
        """Update return_items (requires ownership)"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            if not obj:
                logger.warning(f"Return_items {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key) and key != 'user_id':
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated return_items {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating return_items {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int, user_id: Optional[str] = None) -> bool:
        """Delete return_items (requires ownership)"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            if not obj:
                logger.warning(f"Return_items {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted return_items {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting return_items {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Return_items]:
        """Get return_items by any field"""
        try:
            if not hasattr(Return_items, field_name):
                raise ValueError(f"Field {field_name} does not exist on Return_items")
            result = await self.db.execute(
                select(Return_items).where(getattr(Return_items, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching return_items by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Return_items]:
        """Get list of return_itemss filtered by field"""
        try:
            if not hasattr(Return_items, field_name):
                raise ValueError(f"Field {field_name} does not exist on Return_items")
            result = await self.db.execute(
                select(Return_items)
                .where(getattr(Return_items, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Return_items.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching return_itemss by {field_name}: {str(e)}")
            raise