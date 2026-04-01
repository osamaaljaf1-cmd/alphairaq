import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.areas import AreasService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/areas", tags=["areas"])


# ---------- Pydantic Schemas ----------
class AreasData(BaseModel):
    """Entity data schema (for create/update)"""
    name: str
    parent_area_id: int = None


class AreasUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    name: Optional[str] = None
    parent_area_id: Optional[int] = None


class AreasResponse(BaseModel):
    """Entity response schema"""
    id: int
    name: str
    parent_area_id: Optional[int] = None

    class Config:
        from_attributes = True


class AreasListResponse(BaseModel):
    """List response schema"""
    items: List[AreasResponse]
    total: int
    skip: int
    limit: int


class AreasBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[AreasData]


class AreasBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: AreasUpdateData


class AreasBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[AreasBatchUpdateItem]


class AreasBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=AreasListResponse)
async def query_areass(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query areass with filtering, sorting, and pagination"""
    logger.debug(f"Querying areass: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = AreasService(db)
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
        logger.debug(f"Found {result['total']} areass")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying areass: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=AreasListResponse)
async def query_areass_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query areass with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying areass: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = AreasService(db)
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
        logger.debug(f"Found {result['total']} areass")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying areass: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=AreasResponse)
async def get_areas(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single areas by ID"""
    logger.debug(f"Fetching areas with id: {id}, fields={fields}")
    
    service = AreasService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Areas with id {id} not found")
            raise HTTPException(status_code=404, detail="Areas not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=AreasResponse, status_code=201)
async def create_areas(
    data: AreasData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new areas"""
    logger.debug(f"Creating new areas with data: {data}")
    
    service = AreasService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create areas")
        
        logger.info(f"Areas created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating areas: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating areas: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[AreasResponse], status_code=201)
async def create_areass_batch(
    request: AreasBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple areass in a single request"""
    logger.debug(f"Batch creating {len(request.items)} areass")
    
    service = AreasService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} areass successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[AreasResponse])
async def update_areass_batch(
    request: AreasBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple areass in a single request"""
    logger.debug(f"Batch updating {len(request.items)} areass")
    
    service = AreasService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} areass successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=AreasResponse)
async def update_areas(
    id: int,
    data: AreasUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing areas"""
    logger.debug(f"Updating areas {id} with data: {data}")

    service = AreasService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Areas with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Areas not found")
        
        logger.info(f"Areas {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating areas {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_areass_batch(
    request: AreasBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple areass by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} areass")
    
    service = AreasService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} areass successfully")
        return {"message": f"Successfully deleted {deleted_count} areass", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_areas(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single areas by ID"""
    logger.debug(f"Deleting areas with id: {id}")
    
    service = AreasService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Areas with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Areas not found")
        
        logger.info(f"Areas {id} deleted successfully")
        return {"message": "Areas deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting areas {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")