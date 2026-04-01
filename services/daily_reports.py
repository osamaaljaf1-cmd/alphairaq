import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.daily_reports import Daily_reports

logger = logging.getLogger(__name__)


# ------------------ Service Layer ------------------
class Daily_reportsService:
    """Service layer for Daily_reports operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Daily_reports]:
        """Create a new daily_reports"""
        try:
            if user_id:
                data['user_id'] = user_id
            obj = Daily_reports(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Created daily_reports with id: {obj.id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error creating daily_reports: {str(e)}")
            raise

    async def check_ownership(self, obj_id: int, user_id: str) -> bool:
        """Check if user owns this record"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            return obj is not None
        except Exception as e:
            logger.error(f"Error checking ownership for daily_reports {obj_id}: {str(e)}")
            return False

    async def get_by_id(self, obj_id: int, user_id: Optional[str] = None) -> Optional[Daily_reports]:
        """Get daily_reports by ID (user can only see their own records)"""
        try:
            query = select(Daily_reports).where(Daily_reports.id == obj_id)
            if user_id:
                query = query.where(Daily_reports.user_id == user_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching daily_reports {obj_id}: {str(e)}")
            raise

    async def get_list(
        self, 
        skip: int = 0, 
        limit: int = 20, 
        user_id: Optional[str] = None,
        query_dict: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of daily_reportss (user can only see their own records)"""
        try:
            query = select(Daily_reports)
            count_query = select(func.count(Daily_reports.id))
            
            if user_id:
                query = query.where(Daily_reports.user_id == user_id)
                count_query = count_query.where(Daily_reports.user_id == user_id)
            
            if query_dict:
                for field, value in query_dict.items():
                    if hasattr(Daily_reports, field):
                        query = query.where(getattr(Daily_reports, field) == value)
                        count_query = count_query.where(getattr(Daily_reports, field) == value)
            
            count_result = await self.db.execute(count_query)
            total = count_result.scalar()

            if sort:
                if sort.startswith('-'):
                    field_name = sort[1:]
                    if hasattr(Daily_reports, field_name):
                        query = query.order_by(getattr(Daily_reports, field_name).desc())
                else:
                    if hasattr(Daily_reports, sort):
                        query = query.order_by(getattr(Daily_reports, sort))
            else:
                query = query.order_by(Daily_reports.id.desc())

            result = await self.db.execute(query.offset(skip).limit(limit))
            items = result.scalars().all()

            return {
                "items": items,
                "total": total,
                "skip": skip,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Error fetching daily_reports list: {str(e)}")
            raise

    async def update(self, obj_id: int, update_data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Daily_reports]:
        """Update daily_reports (requires ownership)"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            if not obj:
                logger.warning(f"Daily_reports {obj_id} not found for update")
                return None
            for key, value in update_data.items():
                if hasattr(obj, key) and key != 'user_id':
                    setattr(obj, key, value)

            await self.db.commit()
            await self.db.refresh(obj)
            logger.info(f"Updated daily_reports {obj_id}")
            return obj
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating daily_reports {obj_id}: {str(e)}")
            raise

    async def delete(self, obj_id: int, user_id: Optional[str] = None) -> bool:
        """Delete daily_reports (requires ownership)"""
        try:
            obj = await self.get_by_id(obj_id, user_id=user_id)
            if not obj:
                logger.warning(f"Daily_reports {obj_id} not found for deletion")
                return False
            await self.db.delete(obj)
            await self.db.commit()
            logger.info(f"Deleted daily_reports {obj_id}")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error deleting daily_reports {obj_id}: {str(e)}")
            raise

    async def get_by_field(self, field_name: str, field_value: Any) -> Optional[Daily_reports]:
        """Get daily_reports by any field"""
        try:
            if not hasattr(Daily_reports, field_name):
                raise ValueError(f"Field {field_name} does not exist on Daily_reports")
            result = await self.db.execute(
                select(Daily_reports).where(getattr(Daily_reports, field_name) == field_value)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error fetching daily_reports by {field_name}: {str(e)}")
            raise

    async def list_by_field(
        self, field_name: str, field_value: Any, skip: int = 0, limit: int = 20
    ) -> List[Daily_reports]:
        """Get list of daily_reportss filtered by field"""
        try:
            if not hasattr(Daily_reports, field_name):
                raise ValueError(f"Field {field_name} does not exist on Daily_reports")
            result = await self.db.execute(
                select(Daily_reports)
                .where(getattr(Daily_reports, field_name) == field_value)
                .offset(skip)
                .limit(limit)
                .order_by(Daily_reports.id.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error fetching daily_reportss by {field_name}: {str(e)}")
            raise