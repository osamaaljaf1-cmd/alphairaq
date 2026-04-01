import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.daily_reports import Daily_reportsService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/daily_reports", tags=["daily_reports"])


# ---------- Pydantic Schemas ----------
class Daily_reportsData(BaseModel):
    """Entity data schema (for create/update)"""
    rep_name: str = None
    report_date: str = None
    total_visits: int = None
    completed_count: int = None
    cancelled_count: int = None
    deal_count: int = None
    quota_count: int = None
    followup_count: int = None
    report_data: str = None
    created_at: Optional[datetime] = None


class Daily_reportsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    rep_name: Optional[str] = None
    report_date: Optional[str] = None
    total_visits: Optional[int] = None
    completed_count: Optional[int] = None
    cancelled_count: Optional[int] = None
    deal_count: Optional[int] = None
    quota_count: Optional[int] = None
    followup_count: Optional[int] = None
    report_data: Optional[str] = None
    created_at: Optional[datetime] = None


class Daily_reportsResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    rep_name: Optional[str] = None
    report_date: Optional[str] = None
    total_visits: Optional[int] = None
    completed_count: Optional[int] = None
    cancelled_count: Optional[int] = None
    deal_count: Optional[int] = None
    quota_count: Optional[int] = None
    followup_count: Optional[int] = None
    report_data: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Daily_reportsListResponse(BaseModel):
    """List response schema"""
    items: List[Daily_reportsResponse]
    total: int
    skip: int
    limit: int


class Daily_reportsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Daily_reportsData]


class Daily_reportsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Daily_reportsUpdateData


class Daily_reportsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Daily_reportsBatchUpdateItem]


class Daily_reportsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Daily_reportsListResponse)
async def query_daily_reportss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query daily_reportss with filtering, sorting, and pagination (user can only see their own records)"""
    logger.debug(f"Querying daily_reportss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Daily_reportsService(db)
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
        logger.debug(f"Found {result['total']} daily_reportss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying daily_reportss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Daily_reportsListResponse)
async def query_daily_reportss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query daily_reportss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying daily_reportss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Daily_reportsService(db)
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
        logger.debug(f"Found {result['total']} daily_reportss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying daily_reportss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Daily_reportsResponse)
async def get_daily_reports(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single daily_reports by ID (user can only see their own records)"""
    logger.debug(f"Fetching daily_reports with id: {id}, fields={fields}")
    
    service = Daily_reportsService(db)
    try:
        result = await service.get_by_id(id, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Daily_reports with id {id} not found")
            raise HTTPException(status_code=404, detail="Daily_reports not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching daily_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Daily_reportsResponse, status_code=201)
async def create_daily_reports(
    data: Daily_reportsData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new daily_reports"""
    logger.debug(f"Creating new daily_reports with data: {data}")
    
    service = Daily_reportsService(db)
    try:
        result = await service.create(data.model_dump(), user_id=str(current_user.id))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create daily_reports")
        
        logger.info(f"Daily_reports created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating daily_reports: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating daily_reports: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Daily_reportsResponse], status_code=201)
async def create_daily_reportss_batch(
    request: Daily_reportsBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple daily_reportss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} daily_reportss")
    
    service = Daily_reportsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump(), user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} daily_reportss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Daily_reportsResponse])
async def update_daily_reportss_batch(
    request: Daily_reportsBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple daily_reportss in a single request (requires ownership)"""
    logger.debug(f"Batch updating {len(request.items)} daily_reportss")
    
    service = Daily_reportsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict, user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} daily_reportss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Daily_reportsResponse)
async def update_daily_reports(
    id: int,
    data: Daily_reportsUpdateData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing daily_reports (requires ownership)"""
    logger.debug(f"Updating daily_reports {id} with data: {data}")

    service = Daily_reportsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Daily_reports with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Daily_reports not found")
        
        logger.info(f"Daily_reports {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating daily_reports {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating daily_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_daily_reportss_batch(
    request: Daily_reportsBatchDeleteRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple daily_reportss by their IDs (requires ownership)"""
    logger.debug(f"Batch deleting {len(request.ids)} daily_reportss")
    
    service = Daily_reportsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id, user_id=str(current_user.id))
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} daily_reportss successfully")
        return {"message": f"Successfully deleted {deleted_count} daily_reportss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_daily_reports(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single daily_reports by ID (requires ownership)"""
    logger.debug(f"Deleting daily_reports with id: {id}")
    
    service = Daily_reportsService(db)
    try:
        success = await service.delete(id, user_id=str(current_user.id))
        if not success:
            logger.warning(f"Daily_reports with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Daily_reports not found")
        
        logger.info(f"Daily_reports {id} deleted successfully")
        return {"message": "Daily_reports deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting daily_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")