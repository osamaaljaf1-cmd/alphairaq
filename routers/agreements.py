import json
import logging
from datetime import date
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.agreements import Agreements
from models.doctors import Doctors
from models.products import Products
from services.agreements import AgreementsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/agreements", tags=["agreements"])


# ---------- Pydantic Schemas ----------
class AgreementsData(BaseModel):
    """Entity data schema (for create/update)"""
    doctor_id: int
    pharmacy_id: int
    product_id: int
    agreed_price: float
    bonus_value: float
    bonus_type: str = None
    agreed_quantity: float = None
    agreed_amount: float = None
    agreed_bonus: float = None
    bonus_qty_threshold: int = None
    bonus_qty: int = None
    start_date: str = None
    end_date: str = None
    status: str = None
    notes: str = None
    order_id: int = None


class AgreementsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    doctor_id: Optional[int] = None
    pharmacy_id: Optional[int] = None
    product_id: Optional[int] = None
    agreed_price: Optional[float] = None
    bonus_value: Optional[float] = None
    bonus_type: Optional[str] = None
    agreed_quantity: Optional[float] = None
    agreed_amount: Optional[float] = None
    agreed_bonus: Optional[float] = None
    bonus_qty_threshold: Optional[int] = None
    bonus_qty: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    order_id: Optional[int] = None


class AgreementsResponse(BaseModel):
    """Entity response schema"""
    id: int
    doctor_id: int
    pharmacy_id: int
    product_id: int
    agreed_price: float
    bonus_value: float
    bonus_type: Optional[str] = None
    agreed_quantity: Optional[float] = None
    agreed_amount: Optional[float] = None
    agreed_bonus: Optional[float] = None
    bonus_qty_threshold: Optional[int] = None
    bonus_qty: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    order_id: Optional[int] = None

    class Config:
        from_attributes = True


class AgreementCheckResponse(BaseModel):
    """Response for agreement check endpoint"""
    id: int
    doctor_id: int
    pharmacy_id: int
    product_id: int
    agreed_price: float
    bonus_value: float
    bonus_type: Optional[str] = None
    agreed_quantity: Optional[float] = None
    agreed_amount: Optional[float] = None
    agreed_bonus: Optional[float] = None
    status: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    doctor_name: Optional[str] = None
    item_name: Optional[str] = None

    class Config:
        from_attributes = True


class AgreementsListResponse(BaseModel):
    """List response schema"""
    items: List[AgreementsResponse]
    total: int
    skip: int
    limit: int


class AgreementsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[AgreementsData]


class AgreementsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: AgreementsUpdateData


class AgreementsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[AgreementsBatchUpdateItem]


class AgreementsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------

@router.get("/check")
async def check_agreement(
    pharmacy_id: int = Query(..., description="Pharmacy ID"),
    item_id: int = Query(..., description="Product/Item ID"),
    db: AsyncSession = Depends(get_db),
):
    """Check if an active agreement exists for a pharmacy + item combination.
    Returns the agreement with doctor_name and item_name if found, else null."""
    logger.debug(f"Checking agreement for pharmacy_id={pharmacy_id}, item_id={item_id}")

    today_str = date.today().isoformat()

    try:
        query = (
            select(Agreements)
            .where(
                Agreements.pharmacy_id == pharmacy_id,
                Agreements.product_id == item_id,
                Agreements.status == "active",
            )
        )
        result = await db.execute(query)
        agreement = result.scalar_one_or_none()

        if not agreement:
            return None

        # Fetch doctor name
        doctor_name = None
        try:
            doc_result = await db.execute(
                select(Doctors).where(Doctors.id == agreement.doctor_id)
            )
            doctor = doc_result.scalar_one_or_none()
            if doctor:
                doctor_name = doctor.name
        except Exception:
            pass

        # Fetch item/product name
        item_name = None
        try:
            prod_result = await db.execute(
                select(Products).where(Products.id == agreement.product_id)
            )
            product = prod_result.scalar_one_or_none()
            if product:
                item_name = product.name
        except Exception:
            pass

        return {
            "id": agreement.id,
            "doctor_id": agreement.doctor_id,
            "pharmacy_id": agreement.pharmacy_id,
            "product_id": agreement.product_id,
            "agreed_price": agreement.agreed_price,
            "bonus_value": agreement.bonus_value,
            "bonus_type": agreement.bonus_type,
            "agreed_quantity": agreement.agreed_quantity,
            "agreed_amount": agreement.agreed_amount,
            "agreed_bonus": agreement.agreed_bonus,
            "status": agreement.status,
            "start_date": agreement.start_date,
            "end_date": agreement.end_date,
            "doctor_name": doctor_name,
            "item_name": item_name,
        }
    except Exception as e:
        logger.error(f"Error checking agreement: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("", response_model=AgreementsListResponse)
async def query_agreementss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query agreementss with filtering, sorting, and pagination"""
    logger.debug(f"Querying agreementss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = AgreementsService(db)
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
        logger.debug(f"Found {result['total']} agreementss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying agreementss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=AgreementsListResponse)
async def query_agreementss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query agreementss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying agreementss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = AgreementsService(db)
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
        logger.debug(f"Found {result['total']} agreementss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying agreementss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=AgreementsResponse)
async def get_agreements(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single agreements by ID"""
    logger.debug(f"Fetching agreements with id: {id}, fields={fields}")
    
    service = AgreementsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Agreements with id {id} not found")
            raise HTTPException(status_code=404, detail="Agreements not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agreements {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=AgreementsResponse, status_code=201)
async def create_agreements(
    data: AgreementsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new agreements"""
    logger.debug(f"Creating new agreements with data: {data}")
    
    service = AgreementsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agreements")
        
        logger.info(f"Agreements created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating agreements: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating agreements: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[AgreementsResponse], status_code=201)
async def create_agreementss_batch(
    request: AgreementsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple agreementss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} agreementss")
    
    service = AgreementsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} agreementss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[AgreementsResponse])
async def update_agreementss_batch(
    request: AgreementsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple agreementss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} agreementss")
    
    service = AgreementsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} agreementss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=AgreementsResponse)
async def update_agreements(
    id: int,
    data: AgreementsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing agreements"""
    logger.debug(f"Updating agreements {id} with data: {data}")

    service = AgreementsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Agreements with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Agreements not found")
        
        logger.info(f"Agreements {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating agreements {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating agreements {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_agreementss_batch(
    request: AgreementsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple agreementss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} agreementss")
    
    service = AgreementsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} agreementss successfully")
        return {"message": f"Successfully deleted {deleted_count} agreementss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_agreements(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single agreements by ID"""
    logger.debug(f"Deleting agreements with id: {id}")
    
    service = AgreementsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Agreements with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Agreements not found")
        
        logger.info(f"Agreements {id} deleted successfully")
        return {"message": "Agreements deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agreements {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")