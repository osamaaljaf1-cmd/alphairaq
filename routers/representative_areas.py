import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.representative_areas import Representative_areasService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/representative_areas", tags=["representative_areas"])


# ---------- Pydantic Schemas ----------
class Representative_areasData(BaseModel):
    """Entity data schema (for create/update)"""
    representative_id: int
    area_id: int


class Representative_areasUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    representative_id: Optional[int] = None
    area_id: Optional[int] = None


class Representative_areasResponse(BaseModel):
    """Entity response schema"""
    id: int
    representative_id: int
    area_id: int

    class Config:
        from_attributes = True


class Representative_areasListResponse(BaseModel):
    """List response schema"""
    items: List[Representative_areasResponse]
    total: int
    skip: int
    limit: int


class Representative_areasBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Representative_areasData]


class Representative_areasBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Representative_areasUpdateData


class Representative_areasBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Representative_areasBatchUpdateItem]


class Representative_areasBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Representative_areasListResponse)
async def query_representative_areass(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query representative_areass with filtering, sorting, and pagination"""
    logger.debug(f"Querying representative_areass: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Representative_areasService(db)
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
        logger.debug(f"Found {result['total']} representative_areass")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying representative_areass: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Representative_areasListResponse)
async def query_representative_areass_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query representative_areass with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying representative_areass: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Representative_areasService(db)
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
        logger.debug(f"Found {result['total']} representative_areass")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying representative_areass: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Representative_areasResponse)
async def get_representative_areas(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single representative_areas by ID"""
    logger.debug(f"Fetching representative_areas with id: {id}, fields={fields}")
    
    service = Representative_areasService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Representative_areas with id {id} not found")
            raise HTTPException(status_code=404, detail="Representative_areas not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching representative_areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Representative_areasResponse, status_code=201)
async def create_representative_areas(
    data: Representative_areasData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new representative_areas"""
    logger.debug(f"Creating new representative_areas with data: {data}")
    
    service = Representative_areasService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create representative_areas")
        
        logger.info(f"Representative_areas created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating representative_areas: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating representative_areas: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Representative_areasResponse], status_code=201)
async def create_representative_areass_batch(
    request: Representative_areasBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple representative_areass in a single request"""
    logger.debug(f"Batch creating {len(request.items)} representative_areass")
    
    service = Representative_areasService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} representative_areass successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Representative_areasResponse])
async def update_representative_areass_batch(
    request: Representative_areasBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple representative_areass in a single request"""
    logger.debug(f"Batch updating {len(request.items)} representative_areass")
    
    service = Representative_areasService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} representative_areass successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Representative_areasResponse)
async def update_representative_areas(
    id: int,
    data: Representative_areasUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing representative_areas"""
    logger.debug(f"Updating representative_areas {id} with data: {data}")

    service = Representative_areasService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Representative_areas with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Representative_areas not found")
        
        logger.info(f"Representative_areas {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating representative_areas {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating representative_areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_representative_areass_batch(
    request: Representative_areasBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple representative_areass by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} representative_areass")
    
    service = Representative_areasService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} representative_areass successfully")
        return {"message": f"Successfully deleted {deleted_count} representative_areass", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_representative_areas(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single representative_areas by ID"""
    logger.debug(f"Deleting representative_areas with id: {id}")
    
    service = Representative_areasService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Representative_areas with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Representative_areas not found")
        
        logger.info(f"Representative_areas {id} deleted successfully")
        return {"message": "Representative_areas deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting representative_areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")