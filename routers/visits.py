import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.visits import VisitsService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/visits", tags=["visits"])


# ---------- Pydantic Schemas ----------
class VisitsData(BaseModel):
    """Entity data schema (for create/update)"""
    rep_name: str = None
    customer_name: str = None
    visit_time: Optional[datetime] = None
    latitude: float = None
    longitude: float = None
    address: str = None
    notes: str = None
    tags: str = None
    status: str = None
    created_at: Optional[datetime] = None


class VisitsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    rep_name: Optional[str] = None
    customer_name: Optional[str] = None
    visit_time: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class VisitsResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    rep_name: Optional[str] = None
    customer_name: Optional[str] = None
    visit_time: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class VisitsListResponse(BaseModel):
    """List response schema"""
    items: List[VisitsResponse]
    total: int
    skip: int
    limit: int


class VisitsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[VisitsData]


class VisitsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: VisitsUpdateData


class VisitsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[VisitsBatchUpdateItem]


class VisitsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=VisitsListResponse)
async def query_visitss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query visitss with filtering, sorting, and pagination (user can only see their own records)"""
    logger.debug(f"Querying visitss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = VisitsService(db)
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
            user_id=str(current_user.id),
        )
        logger.debug(f"Found {result['total']} visitss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying visitss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=VisitsListResponse)
async def query_visitss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query visitss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying visitss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = VisitsService(db)
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
        logger.debug(f"Found {result['total']} visitss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying visitss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=VisitsResponse)
async def get_visits(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single visits by ID (user can only see their own records)"""
    logger.debug(f"Fetching visits with id: {id}, fields={fields}")
    
    service = VisitsService(db)
    try:
        result = await service.get_by_id(id, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Visits with id {id} not found")
            raise HTTPException(status_code=404, detail="Visits not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching visits {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=VisitsResponse, status_code=201)
async def create_visits(
    data: VisitsData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new visits"""
    logger.debug(f"Creating new visits with data: {data}")
    
    service = VisitsService(db)
    try:
        result = await service.create(data.model_dump(), user_id=str(current_user.id))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create visits")
        
        logger.info(f"Visits created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating visits: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating visits: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[VisitsResponse], status_code=201)
async def create_visitss_batch(
    request: VisitsBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple visitss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} visitss")
    
    service = VisitsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump(), user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} visitss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[VisitsResponse])
async def update_visitss_batch(
    request: VisitsBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple visitss in a single request (requires ownership)"""
    logger.debug(f"Batch updating {len(request.items)} visitss")
    
    service = VisitsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict, user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} visitss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=VisitsResponse)
async def update_visits(
    id: int,
    data: VisitsUpdateData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing visits (requires ownership)"""
    logger.debug(f"Updating visits {id} with data: {data}")

    service = VisitsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Visits with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Visits not found")
        
        logger.info(f"Visits {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating visits {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating visits {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_visitss_batch(
    request: VisitsBatchDeleteRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple visitss by their IDs (requires ownership)"""
    logger.debug(f"Batch deleting {len(request.ids)} visitss")
    
    service = VisitsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id, user_id=str(current_user.id))
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} visitss successfully")
        return {"message": f"Successfully deleted {deleted_count} visitss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_visits(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single visits by ID (requires ownership)"""
    logger.debug(f"Deleting visits with id: {id}")
    
    service = VisitsService(db)
    try:
        success = await service.delete(id, user_id=str(current_user.id))
        if not success:
            logger.warning(f"Visits with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Visits not found")
        
        logger.info(f"Visits {id} deleted successfully")
        return {"message": "Visits deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting visits {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")