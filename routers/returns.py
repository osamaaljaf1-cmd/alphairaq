import json
import logging
from typing import List, Optional

from datetime import datetime, date, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from core.database import get_db
from services.returns import ReturnsService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/returns", tags=["returns"])


# ---------- Pydantic Schemas ----------
class ReturnsData(BaseModel):
    """Entity data schema (for create/update)"""
    order_id: Optional[int] = None
    pharmacy_id: Optional[int] = None
    doctor_id: Optional[int] = None
    representative_id: Optional[int] = None
    product_id: Optional[int] = None
    quantity: Optional[int] = None
    reason: str
    status: Optional[str] = None
    whatsapp_sent: Optional[bool] = None
    invoice_number: Optional[str] = None
    created_at: Optional[datetime] = None


class ReturnsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    order_id: Optional[int] = None
    pharmacy_id: Optional[int] = None
    doctor_id: Optional[int] = None
    representative_id: Optional[int] = None
    product_id: Optional[int] = None
    quantity: Optional[int] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    whatsapp_sent: Optional[bool] = None
    invoice_number: Optional[str] = None
    created_at: Optional[datetime] = None


class ReturnsResponse(BaseModel):
    """Entity response schema"""
    id: int
    user_id: str
    order_id: Optional[int] = None
    pharmacy_id: Optional[int] = None
    doctor_id: Optional[int] = None
    representative_id: Optional[int] = None
    product_id: Optional[int] = None
    quantity: Optional[int] = None
    reason: str
    status: Optional[str] = None
    whatsapp_sent: Optional[bool] = None
    invoice_number: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReturnsListResponse(BaseModel):
    """List response schema"""
    items: List[ReturnsResponse]
    total: int
    skip: int
    limit: int


class ReturnsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[ReturnsData]


class ReturnsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: ReturnsUpdateData


class ReturnsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[ReturnsBatchUpdateItem]


class ReturnsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=ReturnsListResponse)
async def query_returnss(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query returnss with filtering, sorting, and pagination (user can only see their own records)"""
    logger.debug(f"Querying returnss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = ReturnsService(db)
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
        logger.debug(f"Found {result['total']} returnss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying returnss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=ReturnsListResponse)
async def query_returnss_all(
    query: str = Query(None, description="Query conditions (JSON string)"),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query returnss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying returnss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = ReturnsService(db)
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
        logger.debug(f"Found {result['total']} returnss")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying returnss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=ReturnsResponse)
async def get_returns(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single returns by ID (user can only see their own records)"""
    logger.debug(f"Fetching returns with id: {id}, fields={fields}")
    
    service = ReturnsService(db)
    try:
        result = await service.get_by_id(id, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Returns with id {id} not found")
            raise HTTPException(status_code=404, detail="Returns not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching returns {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=ReturnsResponse, status_code=201)
async def create_returns(
    data: ReturnsData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new returns"""
    logger.debug(f"Creating new returns with data: {data}")
    
    service = ReturnsService(db)
    try:
        result = await service.create(data.model_dump(), user_id=str(current_user.id))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create returns")
        
        logger.info(f"Returns created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating returns: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating returns: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[ReturnsResponse], status_code=201)
async def create_returnss_batch(
    request: ReturnsBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple returnss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} returnss")
    
    service = ReturnsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump(), user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} returnss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[ReturnsResponse])
async def update_returnss_batch(
    request: ReturnsBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple returnss in a single request (requires ownership)"""
    logger.debug(f"Batch updating {len(request.items)} returnss")
    
    service = ReturnsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict, user_id=str(current_user.id))
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} returnss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=ReturnsResponse)
async def update_returns(
    id: int,
    data: ReturnsUpdateData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing returns (requires ownership)"""
    logger.debug(f"Updating returns {id} with data: {data}")

    service = ReturnsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict, user_id=str(current_user.id))
        if not result:
            logger.warning(f"Returns with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Returns not found")
        
        logger.info(f"Returns {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating returns {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating returns {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_returnss_batch(
    request: ReturnsBatchDeleteRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple returnss by their IDs (requires ownership)"""
    logger.debug(f"Batch deleting {len(request.ids)} returnss")
    
    service = ReturnsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id, user_id=str(current_user.id))
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} returnss successfully")
        return {"message": f"Successfully deleted {deleted_count} returnss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_returns(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single returns by ID (requires ownership)"""
    logger.debug(f"Deleting returns with id: {id}")
    
    service = ReturnsService(db)
    try:
        success = await service.delete(id, user_id=str(current_user.id))
        if not success:
            logger.warning(f"Returns with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Returns not found")
        
        logger.info(f"Returns {id} deleted successfully")
        return {"message": "Returns deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting returns {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# ---------- Agreement WhatsApp Message Endpoint ----------
class AgreementMessageResponse(BaseModel):
    """Response for agreement-based WhatsApp message"""
    has_agreement: bool = False
    doctor_id: int | None = None
    doctor_name: str | None = None
    doctor_phone: str | None = None
    pharmacy_name: str | None = None
    product_names: List[str] = []
    message_content: str | None = None
    whatsapp_url: str | None = None
    message_id: int | None = None


@router.post("/{id}/agreement-message", response_model=AgreementMessageResponse)
async def process_agreement_message(
    id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a return has items linked to agreements.
    If so, fetch the doctor's phone from the database, build a WhatsApp message,
    save it to the messages table, and return the WhatsApp URL.
    """
    try:
        # 1. Get the return record
        ret_result = await db.execute(
            text("SELECT * FROM returns WHERE id = :id"),
            {"id": id},
        )
        ret = ret_result.fetchone()
        if not ret:
            raise HTTPException(status_code=404, detail="المرتجع غير موجود")

        # 2. Get return items that have an agreement_id
        items_result = await db.execute(
            text("""
                SELECT ri.*, a.doctor_id as agr_doctor_id, a.pharmacy_id as agr_pharmacy_id
                FROM return_items ri
                LEFT JOIN agreements a ON ri.agreement_id = a.id
                WHERE ri.return_id = :return_id AND ri.agreement_id IS NOT NULL
            """),
            {"return_id": id},
        )
        agreement_items = items_result.fetchall()

        if not agreement_items:
            return AgreementMessageResponse(has_agreement=False)

        # 3. Get the doctor_id from the first agreement item
        doctor_id = agreement_items[0].agr_doctor_id
        if not doctor_id:
            # Fallback: use doctor_id from the return record itself
            doctor_id = ret.doctor_id

        if not doctor_id:
            return AgreementMessageResponse(
                has_agreement=True,
                doctor_id=None,
                message_content="لا يوجد طبيب مرتبط بالاتفاقية",
            )

        # 4. Fetch doctor info from the database
        doc_result = await db.execute(
            text("SELECT id, name, phone FROM doctors WHERE id = :id"),
            {"id": doctor_id},
        )
        doctor = doc_result.fetchone()

        if not doctor:
            return AgreementMessageResponse(
                has_agreement=True,
                doctor_id=doctor_id,
                message_content="الطبيب غير موجود في قاعدة البيانات",
            )

        # 5. Get pharmacy name
        pharmacy_name = "-"
        pharmacy_id = ret.pharmacy_id
        if pharmacy_id:
            pharma_result = await db.execute(
                text("SELECT name FROM pharmacies WHERE id = :id"),
                {"id": pharmacy_id},
            )
            pharma_row = pharma_result.fetchone()
            if pharma_row:
                pharmacy_name = pharma_row.name

        # 6. Get product names for agreement items
        product_ids = [item.product_id for item in agreement_items]
        product_names = []
        product_details = []
        if product_ids:
            # Use ANY(:ids) with array cast to avoid dynamic SQL interpolation
            prod_result = await db.execute(
                text("SELECT id, name FROM products WHERE id = ANY(:ids)"),
                {"ids": product_ids},
            )
            prod_map = {row.id: row.name for row in prod_result.fetchall()}
            for item in agreement_items:
                pname = prod_map.get(item.product_id, "-")
                product_names.append(pname)
                product_details.append(f"- {pname} (الكمية: {item.quantity})")

        # 7. Get representative name (current user)
        rep_name = ""
        try:
            user_result = await db.execute(
                text("SELECT name FROM app_users WHERE user_id = :uid"),
                {"uid": current_user.id},
            )
            user_row = user_result.fetchone()
            if user_row:
                rep_name = user_row.name
        except Exception:
            pass

        # 8. Build the WhatsApp message
        items_list = "\n".join(product_details)
        message_content = (
            f"السلام عليكم دكتور {doctor.name},\n"
            f"تم استرجاع المواد التالية ضمن الاتفاقية:\n"
            f"{items_list}\n"
            f"من قبل الصيدلية: {pharmacy_name}\n"
        )
        if rep_name:
            message_content += f"المندوب: {rep_name}\n"
        if ret.invoice_number:
            message_content += f"رقم الفاتورة: {ret.invoice_number}\n"
        message_content += f"السبب: {ret.reason}\n"
        message_content += "نرجو التواصل معنا لمتابعة الإجراءات."

        # 9. Build WhatsApp URL
        whatsapp_url = None
        if doctor.phone:
            encoded_msg = message_content.replace("\n", "%0A")
            # URL-encode Arabic text properly
            import urllib.parse
            encoded_msg = urllib.parse.quote(message_content)
            whatsapp_url = f"https://wa.me/{doctor.phone}?text={encoded_msg}"

        # 10. Save message to database
        message_id = None
        try:
            msg_result = await db.execute(
                text("""
                    INSERT INTO messages (user_id, doctor_id, pharmacy_id, product_id, return_id,
                        message_type, message_content, doctor_phone, status, created_at)
                    VALUES (:user_id, :doctor_id, :pharmacy_id, :product_id, :return_id,
                        :message_type, :message_content, :doctor_phone, :status, :created_at)
                    RETURNING id
                """),
                {
                    "user_id": current_user.id,
                    "doctor_id": doctor.id,
                    "pharmacy_id": pharmacy_id,
                    "product_id": agreement_items[0].product_id,
                    "return_id": id,
                    "message_type": "whatsapp",
                    "message_content": message_content,
                    "doctor_phone": doctor.phone or "",
                    "status": "sent",
                    "created_at": datetime.now(timezone.utc),
                },
            )
            await db.commit()
            msg_row = msg_result.fetchone()
            if msg_row:
                message_id = msg_row.id
        except Exception as msg_err:
            logger.error(f"Error saving message: {msg_err}")
            await db.rollback()

        # 11. Update return record: mark whatsapp_sent = true
        try:
            await db.execute(
                text("UPDATE returns SET whatsapp_sent = true WHERE id = :id"),
                {"id": id},
            )
            await db.commit()
        except Exception:
            await db.rollback()

        return AgreementMessageResponse(
            has_agreement=True,
            doctor_id=doctor.id,
            doctor_name=doctor.name,
            doctor_phone=doctor.phone,
            pharmacy_name=pharmacy_name,
            product_names=product_names,
            message_content=message_content,
            whatsapp_url=whatsapp_url,
            message_id=message_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing agreement message for return {id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في معالجة رسالة الاتفاقية: {str(e)}")