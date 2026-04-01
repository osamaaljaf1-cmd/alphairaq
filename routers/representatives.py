import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.representatives import RepresentativesService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/representatives", tags=["representatives"])


# ---------- Pydantic Schemas ----------
class RepresentativesData(BaseModel):
    """Entity data schema (for create/update)"""
    name: str
    phone: str = None
    region: str = None
    monthly_target: float = None
    role: str


class RepresentativesUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    name: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    monthly_target: Optional[float] = None
    role: Optional[str] = None


class RepresentativesResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    name: str
    phone: Optional[str] = None
    region: Optional[str] = None
    monthly_target: Optional[float] = None
    role: str

    class Config:
        from_attributes = True


class RepresentativesListResponse(BaseModel):
    """List response schema"""
    items: List[RepresentativesResponse]
    total: int
    skip: int
    limit: int


class RepresentativesBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[RepresentativesData]


class RepresentativesBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: RepresentativesUpdateData


class RepresentativesBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[RepresentativesBatchUpdateItem]


class RepresentativesBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=RepresentativesListResponse)
async def query_representativess(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query representativess with filtering, sorting, and pagination (user can only see their own records)"""
    logger.debug(f"Querying representativess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = RepresentativesService(db)
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
        logger.debug(f"Found {result['total']} representativess")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying representativess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=RepresentativesListResponse)
async def query_representativess_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query representativess with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying representativess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = RepresentativesService(db)
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
        logger.debug(f"Found {result['total']} representativess")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying representativess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=RepresentativesResponse)
async def get_representatives(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single representatives by ID (user can only see their own records)"""
    logger.debug(f"Fetching representatives with id: {id}, fields={fields}")
    
    service = RepresentativesService(db)
    try:
        result = await service.get_by_id(id, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Representatives with id {id} not found")
            raise HTTPException(status_code=404, detail="Representatives not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching representatives {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=RepresentativesResponse, status_code=201)
async def create_representatives(
    data: RepresentativesData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new representatives"""
    logger.debug(f"Creating new representatives with data: {data}")
    
    service = RepresentativesService(db)
    try:
        result = await service.create(data.model_dump(), user_id=str(current_user.id))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create representatives")
        
        logger.info(f"Representatives created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating representatives: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating representatives: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[RepresentativesResponse], status_code=201)
async def create_representativess_batch(
    request: RepresentativesBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple representativess in a single request"""
    logger.debug(f"Batch creating {len(request.items)} representativess")
    
    service = RepresentativesService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump(), user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} representativess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[RepresentativesResponse])
async def update_representativess_batch(
    request: RepresentativesBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple representativess in a single request (requires ownership)"""
    logger.debug(f"Batch updating {len(request.items)} representativess")
    
    service = RepresentativesService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict, user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} representativess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=RepresentativesResponse)
async def update_representatives(
    id: int,
    data: RepresentativesUpdateData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing representatives (requires ownership)"""
    logger.debug(f"Updating representatives {id} with data: {data}")

    service = RepresentativesService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Representatives with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Representatives not found")
        
        logger.info(f"Representatives {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating representatives {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating representatives {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_representativess_batch(
    request: RepresentativesBatchDeleteRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple representativess by their IDs (requires ownership)"""
    logger.debug(f"Batch deleting {len(request.ids)} representativess")
    
    service = RepresentativesService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id, user_id=str(current_user.id))
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} representativess successfully")
        return {"message": f"Successfully deleted {deleted_count} representativess", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_representatives(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single representatives by ID (requires ownership)"""
    logger.debug(f"Deleting representatives with id: {id}")
    
    service = RepresentativesService(db)
    try:
        success = await service.delete(id, user_id=str(current_user.id))
        if not success:
            logger.warning(f"Representatives with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Representatives not found")
        
        logger.info(f"Representatives {id} deleted successfully")
        return {"message": "Representatives deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting representatives {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")