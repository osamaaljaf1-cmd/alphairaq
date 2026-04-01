import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.pharmacies import PharmaciesService
from services.doctors import DoctorsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/pharmacies", tags=["pharmacies"])


# ---------- Pydantic Schemas ----------
class PharmaciesData(BaseModel):
    """Entity data schema (for create/update)"""
    name: str
    customer_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    area: Optional[str] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None


class PharmaciesUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    name: Optional[str] = None
    customer_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    area: Optional[str] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None


class PharmaciesResponse(BaseModel):
    """Entity response schema"""
    id: int
    name: str
    customer_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    area: Optional[str] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None

    class Config:
        from_attributes = True


class PharmaciesListResponse(BaseModel):
    """List response schema"""
    items: List[PharmaciesResponse]
    total: int
    skip: int
    limit: int


class PharmaciesBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[PharmaciesData]


class PharmaciesBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: PharmaciesUpdateData


class PharmaciesBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[PharmaciesBatchUpdateItem]


class PharmaciesBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=PharmaciesListResponse)
async def query_pharmaciess(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query pharmaciess with filtering, sorting, and pagination"""
    logger.debug(f"Querying pharmaciess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = PharmaciesService(db)
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
        logger.debug(f"Found {result['total']} pharmaciess")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying pharmaciess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=PharmaciesListResponse)
async def query_pharmaciess_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query pharmaciess with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying pharmaciess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = PharmaciesService(db)
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
        logger.debug(f"Found {result['total']} pharmaciess")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying pharmaciess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/near-doctor", response_model=List[PharmaciesResponse])
async def get_near_pharmacies(
    doctor_id: int = Query(..., description="Doctor ID to find nearby pharmacies"),
    db: AsyncSession = Depends(get_db),
):
    """Get pharmacies near a doctor based on matching area"""
    logger.debug(f"Finding pharmacies near doctor_id={doctor_id}")

    doctor_service = DoctorsService(db)
    pharmacy_service = PharmaciesService(db)

    try:
        # Get the doctor
        doctor = await doctor_service.get_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        # Get doctor's area
        doctor_area = getattr(doctor, 'area', None) or ''

        if not doctor_area:
            # No area info, return empty list
            return []

        # Find pharmacies in the same area
        result = await pharmacy_service.get_list(
            skip=0,
            limit=100,
            query_dict={"area": doctor_area},
            sort="name",
        )
        return result.get("items", [])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error finding near pharmacies: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=PharmaciesResponse)
async def get_pharmacies(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single pharmacies by ID"""
    logger.debug(f"Fetching pharmacies with id: {id}, fields={fields}")
    
    service = PharmaciesService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Pharmacies with id {id} not found")
            raise HTTPException(status_code=404, detail="Pharmacies not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching pharmacies {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=PharmaciesResponse, status_code=201)
async def create_pharmacies(
    data: PharmaciesData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new pharmacies"""
    logger.debug(f"Creating new pharmacies with data: {data}")
    
    service = PharmaciesService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create pharmacies")
        
        logger.info(f"Pharmacies created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating pharmacies: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating pharmacies: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[PharmaciesResponse], status_code=201)
async def create_pharmaciess_batch(
    request: PharmaciesBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple pharmaciess in a single request"""
    logger.debug(f"Batch creating {len(request.items)} pharmaciess")
    
    service = PharmaciesService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} pharmaciess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[PharmaciesResponse])
async def update_pharmaciess_batch(
    request: PharmaciesBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple pharmaciess in a single request"""
    logger.debug(f"Batch updating {len(request.items)} pharmaciess")
    
    service = PharmaciesService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} pharmaciess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=PharmaciesResponse)
async def update_pharmacies(
    id: int,
    data: PharmaciesUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing pharmacies"""
    logger.debug(f"Updating pharmacies {id} with data: {data}")

    service = PharmaciesService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Pharmacies with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Pharmacies not found")
        
        logger.info(f"Pharmacies {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating pharmacies {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating pharmacies {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_pharmaciess_batch(
    request: PharmaciesBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple pharmaciess by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} pharmaciess")
    
    service = PharmaciesService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} pharmaciess successfully")
        return {"message": f"Successfully deleted {deleted_count} pharmaciess", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_pharmacies(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single pharmacies by ID"""
    logger.debug(f"Deleting pharmacies with id: {id}")
    
    service = PharmaciesService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Pharmacies with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Pharmacies not found")
        
        logger.info(f"Pharmacies {id} deleted successfully")
        return {"message": "Pharmacies deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting pharmacies {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")