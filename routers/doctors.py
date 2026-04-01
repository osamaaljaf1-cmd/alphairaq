import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.doctors import DoctorsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/doctors", tags=["doctors"])


# ---------- Pydantic Schemas ----------
class DoctorsData(BaseModel):
    """Entity data schema (for create/update)"""
    name: str
    customer_number: str = None
    specialty: str = None
    phone: str = None
    hospital: str = None
    area: str = None
    representative_id: int = None
    status: str = None
    notes: str = None


class DoctorsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    name: Optional[str] = None
    customer_number: Optional[str] = None
    specialty: Optional[str] = None
    phone: Optional[str] = None
    hospital: Optional[str] = None
    area: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class DoctorsResponse(BaseModel):
    """Entity response schema"""
    id: int
    name: str
    customer_number: Optional[str] = None
    specialty: Optional[str] = None
    phone: Optional[str] = None
    hospital: Optional[str] = None
    area: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class DoctorsListResponse(BaseModel):
    """List response schema"""
    items: List[DoctorsResponse]
    total: int
    skip: int
    limit: int


class DoctorsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[DoctorsData]


class DoctorsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: DoctorsUpdateData


class DoctorsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[DoctorsBatchUpdateItem]


class DoctorsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=DoctorsListResponse)
async def query_doctorss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query doctorss with filtering, sorting, and pagination"""
    logger.debug(f"Querying doctorss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = DoctorsService(db)
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
        logger.debug(f"Found {result['total']} doctorss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying doctorss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=DoctorsListResponse)
async def query_doctorss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query doctorss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying doctorss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = DoctorsService(db)
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
        logger.debug(f"Found {result['total']} doctorss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying doctorss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=DoctorsResponse)
async def get_doctors(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single doctors by ID"""
    logger.debug(f"Fetching doctors with id: {id}, fields={fields}")
    
    service = DoctorsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Doctors with id {id} not found")
            raise HTTPException(status_code=404, detail="Doctors not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching doctors {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=DoctorsResponse, status_code=201)
async def create_doctors(
    data: DoctorsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new doctors"""
    logger.debug(f"Creating new doctors with data: {data}")
    
    service = DoctorsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create doctors")
        
        logger.info(f"Doctors created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating doctors: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating doctors: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[DoctorsResponse], status_code=201)
async def create_doctorss_batch(
    request: DoctorsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple doctorss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} doctorss")
    
    service = DoctorsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} doctorss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[DoctorsResponse])
async def update_doctorss_batch(
    request: DoctorsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple doctorss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} doctorss")
    
    service = DoctorsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} doctorss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=DoctorsResponse)
async def update_doctors(
    id: int,
    data: DoctorsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing doctors"""
    logger.debug(f"Updating doctors {id} with data: {data}")

    service = DoctorsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Doctors with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Doctors not found")
        
        logger.info(f"Doctors {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating doctors {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating doctors {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_doctorss_batch(
    request: DoctorsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple doctorss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} doctorss")
    
    service = DoctorsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} doctorss successfully")
        return {"message": f"Successfully deleted {deleted_count} doctorss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_doctors(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single doctors by ID"""
    logger.debug(f"Deleting doctors with id: {id}")
    
    service = DoctorsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Doctors with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Doctors not found")
        
        logger.info(f"Doctors {id} deleted successfully")
        return {"message": "Doctors deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting doctors {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")