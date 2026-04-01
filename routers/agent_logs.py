import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.agent_logs import Agent_logsService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/agent_logs", tags=["agent_logs"])


# ---------- Pydantic Schemas ----------
class Agent_logsData(BaseModel):
    """Entity data schema (for create/update)"""
    agent_id: str = None
    latitude: float = None
    longitude: float = None
    timestamp: Optional[datetime] = None
    app_open: bool = None
    app_close: bool = None


class Agent_logsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    agent_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: Optional[datetime] = None
    app_open: Optional[bool] = None
    app_close: Optional[bool] = None


class Agent_logsResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    agent_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: Optional[datetime] = None
    app_open: Optional[bool] = None
    app_close: Optional[bool] = None

    class Config:
        from_attributes = True


class Agent_logsListResponse(BaseModel):
    """List response schema"""
    items: List[Agent_logsResponse]
    total: int
    skip: int
    limit: int


class Agent_logsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Agent_logsData]


class Agent_logsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Agent_logsUpdateData


class Agent_logsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Agent_logsBatchUpdateItem]


class Agent_logsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Agent_logsListResponse)
async def query_agent_logss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query agent_logss with filtering, sorting, and pagination (user can only see their own records)"""
    logger.debug(f"Querying agent_logss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Agent_logsService(db)
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
        logger.debug(f"Found {result['total']} agent_logss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying agent_logss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Agent_logsListResponse)
async def query_agent_logss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query agent_logss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying agent_logss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Agent_logsService(db)
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
        logger.debug(f"Found {result['total']} agent_logss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying agent_logss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Agent_logsResponse)
async def get_agent_logs(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single agent_logs by ID (user can only see their own records)"""
    logger.debug(f"Fetching agent_logs with id: {id}, fields={fields}")
    
    service = Agent_logsService(db)
    try:
        result = await service.get_by_id(id, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Agent_logs with id {id} not found")
            raise HTTPException(status_code=404, detail="Agent_logs not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agent_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Agent_logsResponse, status_code=201)
async def create_agent_logs(
    data: Agent_logsData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new agent_logs"""
    logger.debug(f"Creating new agent_logs with data: {data}")
    
    service = Agent_logsService(db)
    try:
        result = await service.create(data.model_dump(), user_id=str(current_user.id))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent_logs")
        
        logger.info(f"Agent_logs created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating agent_logs: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating agent_logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Agent_logsResponse], status_code=201)
async def create_agent_logss_batch(
    request: Agent_logsBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple agent_logss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} agent_logss")
    
    service = Agent_logsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump(), user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} agent_logss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Agent_logsResponse])
async def update_agent_logss_batch(
    request: Agent_logsBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple agent_logss in a single request (requires ownership)"""
    logger.debug(f"Batch updating {len(request.items)} agent_logss")
    
    service = Agent_logsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict, user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} agent_logss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Agent_logsResponse)
async def update_agent_logs(
    id: int,
    data: Agent_logsUpdateData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing agent_logs (requires ownership)"""
    logger.debug(f"Updating agent_logs {id} with data: {data}")

    service = Agent_logsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Agent_logs with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Agent_logs not found")
        
        logger.info(f"Agent_logs {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating agent_logs {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating agent_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_agent_logss_batch(
    request: Agent_logsBatchDeleteRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple agent_logss by their IDs (requires ownership)"""
    logger.debug(f"Batch deleting {len(request.ids)} agent_logss")
    
    service = Agent_logsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id, user_id=str(current_user.id))
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} agent_logss successfully")
        return {"message": f"Successfully deleted {deleted_count} agent_logss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_agent_logs(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single agent_logs by ID (requires ownership)"""
    logger.debug(f"Deleting agent_logs with id: {id}")
    
    service = Agent_logsService(db)
    try:
        success = await service.delete(id, user_id=str(current_user.id))
        if not success:
            logger.warning(f"Agent_logs with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Agent_logs not found")
        
        logger.info(f"Agent_logs {id} deleted successfully")
        return {"message": "Agent_logs deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agent_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")