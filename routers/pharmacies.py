import json
import logging
from typing import List, Optional


from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.pharmacies import Pharmacies
from services.pharmacies import PharmaciesService
from services.doctors import DoctorsService
from services.area_scope import get_customer_scope
from services.permission_check import require_permission
from services.customer_names import are_similar, clean_name, normalize_name, pharmacy_index

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
    area_id: Optional[int] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None
    credit_limit: Optional[float] = None


class PharmaciesUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    name: Optional[str] = None
    customer_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    area: Optional[str] = None
    area_id: Optional[int] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None
    credit_limit: Optional[float] = None


class PharmaciesResponse(BaseModel):
    """Entity response schema"""
    id: int
    name: str
    customer_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    area: Optional[str] = None
    area_id: Optional[int] = None
    contact_person: Optional[str] = None
    representative_id: Optional[int] = None
    status: Optional[str] = None
    credit_limit: Optional[float] = None

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
    limit: int = Query(20, ge=1, le=20000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query pharmaciess with filtering, sorting, and pagination — scoped to
    the caller's assigned areas/reps unless they're admin/accounting."""
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

        scope = await get_customer_scope(db, current_user)
        result = await service.get_list(
            skip=skip,
            limit=limit,
            query_dict=query_dict,
            sort=sort,
            scope=scope,
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
    limit: int = Query(20, ge=1, le=20000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Same as above but without pagination-owner scoping historically — now
    ALSO applies the caller's area/rep scope, so a rep or manager can't
    bypass their restriction just by calling /all instead of the base route.
    Only admin/accounting (or roles this feature doesn't apply to) actually
    see everything here."""
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

        scope = await get_customer_scope(db, current_user)
        result = await service.get_list(
            skip=skip,
            limit=limit,
            query_dict=query_dict,
            sort=sort,
            scope=scope,
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
    current_user: UserResponse = Depends(get_current_user),
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
    current_user: UserResponse = Depends(get_current_user),
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


async def _checked_name(db: AsyncSession, name: str, own_id: Optional[int] = None) -> str:
    """The name to store (spacing cleaned). Refused when it is empty or when a
    different customer already has the same or a very similar name — a debt is
    linked to its customer by name, so look-alikes end up with hidden debts."""
    cleaned = clean_name(name)
    if not cleaned:
        raise HTTPException(status_code=400, detail="اسم الزبون فارغ")
    rows = (await db.execute(select(Pharmacies.id, Pharmacies.name))).all()
    for pid, other in rows:
        if pid != own_id and other and are_similar(cleaned, other):
            raise HTTPException(status_code=400, detail=f"يوجد زبون بنفس الاسم أو مشابه جداً: «{clean_name(other)}»")
    return cleaned


async def _clean_name_on_update(db: AsyncSession, pharmacy_id: int, update_dict: dict) -> None:
    """Clean the spacing of a name being saved. The similarity check only runs
    when the name really changes, so editing another field of a customer that
    already has a look-alike is not blocked."""
    if "name" not in update_dict:
        return
    current = (await db.execute(select(Pharmacies.name).where(Pharmacies.id == pharmacy_id))).scalar_one_or_none()
    cleaned = clean_name(update_dict["name"])
    if current is not None and normalize_name(cleaned) == normalize_name(current):
        update_dict["name"] = cleaned
    elif current is not None and cleaned and clean_name(current).replace(" ", "") == cleaned.replace(" ", ""):
        update_dict["name"] = cleaned  # only the spacing changed
    else:
        update_dict["name"] = await _checked_name(db, update_dict["name"], own_id=pharmacy_id)


@router.post("", response_model=PharmaciesResponse, status_code=201)
async def create_pharmacies(
    data: PharmaciesData,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pharmacies (requires can_add on the customers page —
    this endpoint previously had no auth at all)"""
    await require_permission(db, current_user, "customers", "add")
    logger.debug(f"Creating new pharmacies with data: {data}")

    service = PharmaciesService(db)
    try:
        create_data = data.model_dump()
        create_data["name"] = await _checked_name(db, data.name)
        if create_data.get("credit_limit") is None:
            create_data["credit_limit"] = 1_000_000

        result = await service.create(create_data)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create pharmacies")
        
        logger.info(f"Pharmacies created successfully with id: {result.id}")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error creating pharmacies: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating pharmacies: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[PharmaciesResponse], status_code=201)
async def create_pharmaciess_batch(
    request: PharmaciesBatchCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple pharmaciess in a single request — this is the Excel
    bulk-import path, so it requires can_import on the customers page (this
    endpoint previously had no auth at all).

    Skips rows whose name already exists (case/whitespace-insensitive), both
    against existing records and duplicates within the same upload, and
    performs a single bulk insert instead of one commit per row so large
    imports don't hit the client's request timeout.
    """
    await require_permission(db, current_user, "customers", "import")
    logger.debug(f"Batch creating {len(request.items)} pharmaciess")

    try:
        index = await pharmacy_index(db)

        new_objs: List[Pharmacies] = []
        skipped = 0

        for item_data in request.items:
            data = item_data.model_dump()
            cleaned = clean_name(data.get("name"))
            # same or very similar to a customer that exists (or one added
            # earlier in this same file) -> not added
            if not cleaned or index.registered(cleaned) or index.similar(cleaned):
                skipped += 1
                continue
            data["name"] = cleaned
            if data.get("credit_limit") is None:
                data["credit_limit"] = 1_000_000
            index.add(cleaned)
            new_objs.append(Pharmacies(**data))

        db.add_all(new_objs)
        await db.commit()
        for obj in new_objs:
            await db.refresh(obj)

        logger.info(f"Batch created {len(new_objs)} pharmaciess successfully, skipped {skipped} duplicates")
        return new_objs
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[PharmaciesResponse])
async def update_pharmaciess_batch(
    request: PharmaciesBatchUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update multiple pharmaciess in a single request (requires can_edit on
    the customers page — this endpoint previously had no auth at all)"""
    await require_permission(db, current_user, "customers", "edit")
    logger.debug(f"Batch updating {len(request.items)} pharmaciess")

    service = PharmaciesService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            await _clean_name_on_update(db, item.id, update_dict)
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
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing pharmacies (requires can_edit on the customers
    page — this endpoint previously had no auth at all)"""
    await require_permission(db, current_user, "customers", "edit")
    logger.debug(f"Updating pharmacies {id} with data: {data}")

    service = PharmaciesService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        await _clean_name_on_update(db, id, update_dict)
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
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple pharmaciess by their IDs (requires can_delete permission
    on the customers page — this endpoint previously had no auth at all)"""
    await require_permission(db, current_user, "customers", "delete")
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
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single pharmacies by ID (requires can_delete permission on the
    customers page — this endpoint previously had no auth at all)"""
    await require_permission(db, current_user, "customers", "delete")
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