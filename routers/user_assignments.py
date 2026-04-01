import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.user_assignments import User_assignmentsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/user_assignments", tags=["user_assignments"])


# ---------- Pydantic Schemas ----------
class User_assignmentsData(BaseModel):
    """Entity data schema (for create/update)"""
    user_id: str
    manager_rep_id: int
    assigned_rep_id: int
    assignment_type: str


class User_assignmentsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    user_id: Optional[str] = None
    manager_rep_id: Optional[int] = None
    assigned_rep_id: Optional[int] = None
    assignment_type: Optional[str] = None


class User_assignmentsResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    manager_rep_id: int
    assigned_rep_id: int
    assignment_type: str

    class Config:
        from_attributes = True


class User_assignmentsListResponse(BaseModel):
    """List response schema"""
    items: List[User_assignmentsResponse]
    total: int
    skip: int
    limit: int


class User_assignmentsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[User_assignmentsData]


class User_assignmentsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: User_assignmentsUpdateData


class User_assignmentsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[User_assignmentsBatchUpdateItem]


class User_assignmentsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=User_assignmentsListResponse)
async def query_user_assignmentss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query user_assignmentss with filtering, sorting, and pagination"""
    logger.debug(f"Querying user_assignmentss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = User_assignmentsService(db)
    try:
        # Parse query JSON if provided
        query_dict = None
        if query:
            try:
                query_dict = json.loads(query)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid query JSON format")
        
        result = await service.get_list(
            skip=skip, 
            limit=limit,
            query_dict=query_dict,
            sort=sort,
        )
        logger.debug(f"Found {result['total']} user_assignmentss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying user_assignmentss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=User_assignmentsListResponse)
async def query_user_assignmentss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query user_assignmentss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying user_assignmentss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = User_assignmentsService(db)
    try:
        # Parse query JSON if provided
        query_dict = None
        if query:
            try:
                query_dict = json.loads(query)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid query JSON format")

        result = await service.get_list(
            skip=skip,
            limit=limit,
            query_dict=query_dict,
            sort=sort
        )
        logger.debug(f"Found {result['total']} user_assignmentss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying user_assignmentss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=User_assignmentsResponse)
async def get_user_assignments(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user_assignments by ID"""
    logger.debug(f"Fetching user_assignments with id: {id}, fields={fields}")
    
    service = User_assignmentsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"User_assignments with id {id} not found")
            raise HTTPException(status_code=404, detail="User_assignments not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user_assignments {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=User_assignmentsResponse, status_code=201)
async def create_user_assignments(
    data: User_assignmentsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user_assignments"""
    logger.debug(f"Creating new user_assignments with data: {data}")
    
    service = User_assignmentsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create user_assignments")
        
        logger.info(f"User_assignments created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating user_assignments: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating user_assignments: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[User_assignmentsResponse], status_code=201)
async def create_user_assignmentss_batch(
    request: User_assignmentsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple user_assignmentss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} user_assignmentss")
    
    service = User_assignmentsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} user_assignmentss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[User_assignmentsResponse])
async def update_user_assignmentss_batch(
    request: User_assignmentsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple user_assignmentss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} user_assignmentss")
    
    service = User_assignmentsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} user_assignmentss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=User_assignmentsResponse)
async def update_user_assignments(
    id: int,
    data: User_assignmentsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing user_assignments"""
    logger.debug(f"Updating user_assignments {id} with data: {data}")

    service = User_assignmentsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"User_assignments with id {id} not found for update")
            raise HTTPException(status_code=404, detail="User_assignments not found")
        
        logger.info(f"User_assignments {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating user_assignments {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating user_assignments {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_user_assignmentss_batch(
    request: User_assignmentsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple user_assignmentss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} user_assignmentss")
    
    service = User_assignmentsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} user_assignmentss successfully")
        return {"message": f"Successfully deleted {deleted_count} user_assignmentss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_user_assignments(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single user_assignments by ID"""
    logger.debug(f"Deleting user_assignments with id: {id}")
    
    service = User_assignmentsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"User_assignments with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="User_assignments not found")
        
        logger.info(f"User_assignments {id} deleted successfully")
        return {"message": "User_assignments deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user_assignments {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")