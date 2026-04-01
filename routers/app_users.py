import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.app_users import App_usersService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/app_users", tags=["app_users"])


# ---------- Pydantic Schemas ----------
class App_usersData(BaseModel):
    """Entity data schema (for create/update)"""
    user_id: str
    name: str
    email: str
    password_hash: str
    role: str
    status: str


class App_usersUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    password_hash: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class App_usersResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    name: str
    email: str
    password_hash: str
    role: str
    status: str

    class Config:
        from_attributes = True


class App_usersListResponse(BaseModel):
    """List response schema"""
    items: List[App_usersResponse]
    total: int
    skip: int
    limit: int


class App_usersBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[App_usersData]


class App_usersBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: App_usersUpdateData


class App_usersBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[App_usersBatchUpdateItem]


class App_usersBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=App_usersListResponse)
async def query_app_userss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query app_userss with filtering, sorting, and pagination"""
    logger.debug(f"Querying app_userss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = App_usersService(db)
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
        logger.debug(f"Found {result['total']} app_userss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying app_userss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=App_usersListResponse)
async def query_app_userss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query app_userss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying app_userss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = App_usersService(db)
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
        logger.debug(f"Found {result['total']} app_userss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying app_userss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=App_usersResponse)
async def get_app_users(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single app_users by ID"""
    logger.debug(f"Fetching app_users with id: {id}, fields={fields}")
    
    service = App_usersService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"App_users with id {id} not found")
            raise HTTPException(status_code=404, detail="App_users not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching app_users {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=App_usersResponse, status_code=201)
async def create_app_users(
    data: App_usersData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new app_users"""
    logger.debug(f"Creating new app_users with data: {data}")
    
    service = App_usersService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create app_users")
        
        logger.info(f"App_users created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating app_users: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating app_users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[App_usersResponse], status_code=201)
async def create_app_userss_batch(
    request: App_usersBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple app_userss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} app_userss")
    
    service = App_usersService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} app_userss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[App_usersResponse])
async def update_app_userss_batch(
    request: App_usersBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple app_userss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} app_userss")
    
    service = App_usersService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} app_userss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=App_usersResponse)
async def update_app_users(
    id: int,
    data: App_usersUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing app_users"""
    logger.debug(f"Updating app_users {id} with data: {data}")

    service = App_usersService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"App_users with id {id} not found for update")
            raise HTTPException(status_code=404, detail="App_users not found")
        
        logger.info(f"App_users {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating app_users {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating app_users {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_app_userss_batch(
    request: App_usersBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple app_userss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} app_userss")
    
    service = App_usersService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} app_userss successfully")
        return {"message": f"Successfully deleted {deleted_count} app_userss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_app_users(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single app_users by ID"""
    logger.debug(f"Deleting app_users with id: {id}")
    
    service = App_usersService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"App_users with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="App_users not found")
        
        logger.info(f"App_users {id} deleted successfully")
        return {"message": "App_users deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting app_users {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")