import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.permissions import PermissionsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/permissions", tags=["permissions"])


# ---------- Pydantic Schemas ----------
class PermissionsData(BaseModel):
    """Entity data schema (for create/update)"""
    role: str
    page: str
    can_view: bool = None
    can_add: bool = None
    can_edit: bool = None
    can_delete: bool = None
    can_import: bool = None
    can_export: bool = None


class PermissionsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    role: Optional[str] = None
    page: Optional[str] = None
    can_view: Optional[bool] = None
    can_add: Optional[bool] = None
    can_edit: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_import: Optional[bool] = None
    can_export: Optional[bool] = None


class PermissionsResponse(BaseModel):
    """Entity response schema"""
    id: int
    role: str
    page: str
    can_view: Optional[bool] = None
    can_add: Optional[bool] = None
    can_edit: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_import: Optional[bool] = None
    can_export: Optional[bool] = None

    class Config:
        from_attributes = True


class PermissionsListResponse(BaseModel):
    """List response schema"""
    items: List[PermissionsResponse]
    total: int
    skip: int
    limit: int


class PermissionsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[PermissionsData]


class PermissionsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: PermissionsUpdateData


class PermissionsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[PermissionsBatchUpdateItem]


class PermissionsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=PermissionsListResponse)
async def query_permissionss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query permissionss with filtering, sorting, and pagination"""
    logger.debug(f"Querying permissionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = PermissionsService(db)
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
        logger.debug(f"Found {result['total']} permissionss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying permissionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=PermissionsListResponse)
async def query_permissionss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query permissionss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying permissionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = PermissionsService(db)
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
        logger.debug(f"Found {result['total']} permissionss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying permissionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=PermissionsResponse)
async def get_permissions(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single permissions by ID"""
    logger.debug(f"Fetching permissions with id: {id}, fields={fields}")
    
    service = PermissionsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Permissions with id {id} not found")
            raise HTTPException(status_code=404, detail="Permissions not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching permissions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=PermissionsResponse, status_code=201)
async def create_permissions(
    data: PermissionsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new permissions"""
    logger.debug(f"Creating new permissions with data: {data}")
    
    service = PermissionsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create permissions")
        
        logger.info(f"Permissions created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating permissions: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating permissions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[PermissionsResponse], status_code=201)
async def create_permissionss_batch(
    request: PermissionsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple permissionss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} permissionss")
    
    service = PermissionsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} permissionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[PermissionsResponse])
async def update_permissionss_batch(
    request: PermissionsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple permissionss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} permissionss")
    
    service = PermissionsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} permissionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=PermissionsResponse)
async def update_permissions(
    id: int,
    data: PermissionsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing permissions"""
    logger.debug(f"Updating permissions {id} with data: {data}")

    service = PermissionsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Permissions with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Permissions not found")
        
        logger.info(f"Permissions {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating permissions {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating permissions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_permissionss_batch(
    request: PermissionsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple permissionss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} permissionss")
    
    service = PermissionsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} permissionss successfully")
        return {"message": f"Successfully deleted {deleted_count} permissionss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_permissions(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single permissions by ID"""
    logger.debug(f"Deleting permissions with id: {id}")
    
    service = PermissionsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Permissions with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Permissions not found")
        
        logger.info(f"Permissions {id} deleted successfully")
        return {"message": "Permissions deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting permissions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")