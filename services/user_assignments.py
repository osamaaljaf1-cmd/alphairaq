import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_assignments import User_assignments

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class User_assignmentsService:
    """Service layer for User_assignments operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any]) -> Optional[User_assignments]:
        """Create a new user_assignments"""
        try:
            obj = User_assignments(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created user_assignments with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating user_assignments: {str(e)}")
            raise

    async def get_by_id(self, obj_id: int) -> Optional[User_assignments]:
        """Get user_assignments by ID"""
        try:
            query = select(User_assignments).where(User_assignments.id == obj_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching user_assignments {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of user_assignmentss"""
        try:
            query = select(User_assignments)
            count_query = select(func.count(User_assignments.id))
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(User_assignments, field):
                        query = query.where(getattr(User_assignments, field) == value)
                        count_query = count_query.where(getattr(User_assignments, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(User_assignments, field_name):
                        query = query.order_by(getattr(User_assignments, field_name).desc())
                else:
                    if hasattr(User_assignments, sort):
                        query = query.order_by(getattr(User_assignments, sort))
            else:
                query = query.order_by(User_assignments.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching user_assignments list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any]) -> Optional[User_assignments]:
        """Update user_assignments"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"User_assignments {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated user_assignments {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating user_assignments {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int) -> bool:
        """Delete user_assignments"""
        try:
            obj = await self.get_by_id(obj_id)
            if not obj:
                logger.warning(f"User_assignments {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted user_assignments {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting user_assignments {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[User_assignments]:
        """Get user_assignments by any field"""
        try:
            if not hasattr(User_assignments, field_name):
                raise ValueError(f"Field {field_name} does not exist on User_assignments")
            result = await self.db.execute(
                select(User_assignments).where(getattr(User_assignments, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching user_assignments by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[User_assignments]:
        """Get list of user_assignmentss filtered by field"""
        try:
            if not hasattr(User_assignments, field_name):
                raise ValueError(f"Field {field_name} does not exist on User_assignments")
            result = await self.db.execute(
                select(User_assignments)
                .where(getattr(User_assignments, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(User_assignments.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching user_assignmentss by {field_name}: {str(e)}")
            raise