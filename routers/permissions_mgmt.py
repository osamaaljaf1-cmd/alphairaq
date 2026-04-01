import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.permissions import PermissionsService
from services.areas import AreasService
from services.representative_areas import Representative_areasService
from services.app_users import App_usersService
from services.representatives import RepresentativesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/permissions-mgmt", tags=["permissions-mgmt"])


async def verify_admin_or_manager(db: AsyncSession, user_id: str) -> str:
    """Verify that the given user_id belongs to an admin or manager. Returns the role."""
    if not user_id:
        raise HTTPException(status_code=403, detail="صلاحيات غير كافية")
    service = App_usersService(db)
    user = await service.get_by_field("user_id", user_id)
    if user and user.role in ("admin", "manager") and user.status == "active":
        return user.role
    rep_service = RepresentativesService(db)
    rep = await rep_service.get_by_field("user_id", user_id)
    if rep and rep.role in ("admin", "manager"):
        return rep.role
    raise HTTPException(status_code=403, detail="صلاحيات غير كافية")


# ============================================================
# Permission Models
# ============================================================

class PermissionItem(BaseModel):
    id: int
    role: str
    page: str
    can_view: bool
    can_add: bool
    can_edit: bool
    can_delete: bool
    can_import: bool = False
    can_export: bool = False

    class Config:
        from_attributes = True


class PermissionsListResponse(BaseModel):
    items: List[PermissionItem]


class UpdatePermissionRequest(BaseModel):
    admin_user_id: str
    permissions: List[dict]  # [{id, can_view, can_add, can_edit, can_delete, can_import, can_export}]


class GetPermissionsRequest(BaseModel):
    user_id: str


# ============================================================
# Area Models
# ============================================================

class AreaItem(BaseModel):
    id: int
    name: str
    parent_area_id: Optional[int] = None

    class Config:
        from_attributes = True


class AreasListResponse(BaseModel):
    items: List[AreaItem]


class CreateAreaRequest(BaseModel):
    admin_user_id: str
    name: str
    parent_area_id: Optional[int] = None


class UpdateAreaRequest(BaseModel):
    admin_user_id: str
    name: Optional[str] = None
    parent_area_id: Optional[int] = None


class DeleteAreaRequest(BaseModel):
    admin_user_id: str


# ============================================================
# Representative Areas Models
# ============================================================

class RepAreaItem(BaseModel):
    id: int
    representative_id: int
    area_id: int

    class Config:
        from_attributes = True


class RepAreasListResponse(BaseModel):
    items: List[RepAreaItem]


class SetRepAreasRequest(BaseModel):
    admin_user_id: str
    representative_id: int
    area_ids: List[int]


# ============================================================
# Permission Endpoints
# ============================================================

@router.post("/list", response_model=PermissionsListResponse)
async def list_permissions(
    data: GetPermissionsRequest,
    db: AsyncSession = Depends(get_db),
):
    """List all permissions (admin/manager only)"""
    await verify_admin_or_manager(db, data.user_id)
    service = PermissionsService(db)
    try:
        result = await service.get_list(skip=0, limit=500)
        items = result.get("items", [])
        return PermissionsListResponse(items=items)
    except Exception as e:
        logger.error(f"Error listing permissions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class GetPermissionsByRoleRequest(BaseModel):
    role: str


@router.post("/by-role")
async def get_permissions_by_role(
    data: GetPermissionsByRoleRequest,
    db: AsyncSession = Depends(get_db),
):
    """Get permissions for a specific role (public - used by frontend to filter nav)"""
    service = PermissionsService(db)
    try:
        items = await service.list_by_field("role", data.role, skip=0, limit=100)
        return {"items": [{
            "id": p.id, "role": p.role, "page": p.page,
            "can_view": p.can_view, "can_add": p.can_add, "can_edit": p.can_edit, "can_delete": p.can_delete,
            "can_import": getattr(p, "can_import", False) or False,
            "can_export": getattr(p, "can_export", False) or False,
        } for p in items]}
    except Exception as e:
        logger.error(f"Error getting permissions by role: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update")
async def update_permissions(
    data: UpdatePermissionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple permissions (admin only)"""
    role = await verify_admin_or_manager(db, data.admin_user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="فقط المسؤول يمكنه تعديل الصلاحيات")

    service = PermissionsService(db)
    try:
        updated = 0
        for perm in data.permissions:
            perm_id = perm.get("id")
            if not perm_id:
                continue
            update_data = {}
            if "can_view" in perm:
                update_data["can_view"] = perm["can_view"]
            if "can_add" in perm:
                update_data["can_add"] = perm["can_add"]
            if "can_edit" in perm:
                update_data["can_edit"] = perm["can_edit"]
            if "can_delete" in perm:
                update_data["can_delete"] = perm["can_delete"]
            if "can_import" in perm:
                update_data["can_import"] = perm["can_import"]
            if "can_export" in perm:
                update_data["can_export"] = perm["can_export"]
            if update_data:
                await service.update(perm_id, update_data)
                updated += 1
        return {"success": True, "updated": updated}
    except Exception as e:
        logger.error(f"Error updating permissions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Area Endpoints
# ============================================================

@router.post("/areas/list", response_model=AreasListResponse)
async def list_areas(
    db: AsyncSession = Depends(get_db),
):
    """List all areas (public)"""
    service = AreasService(db)
    try:
        result = await service.get_list(skip=0, limit=500)
        items = result.get("items", [])
        return AreasListResponse(items=items)
    except Exception as e:
        logger.error(f"Error listing areas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/areas/create", response_model=AreaItem)
async def create_area(
    data: CreateAreaRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new area (admin only)"""
    await verify_admin_or_manager(db, data.admin_user_id)
    service = AreasService(db)
    try:
        create_data = {"name": data.name}
        if data.parent_area_id is not None:
            create_data["parent_area_id"] = data.parent_area_id
        result = await service.create(create_data)
        if not result:
            raise HTTPException(status_code=400, detail="فشل في إنشاء المنطقة")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating area: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/areas/{area_id}", response_model=AreaItem)
async def update_area(
    area_id: int,
    data: UpdateAreaRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update an area (admin only)"""
    await verify_admin_or_manager(db, data.admin_user_id)
    service = AreasService(db)
    try:
        update_data = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.parent_area_id is not None:
            update_data["parent_area_id"] = data.parent_area_id
        if not update_data:
            raise HTTPException(status_code=400, detail="لا توجد حقول للتحديث")
        result = await service.update(area_id, update_data)
        if not result:
            raise HTTPException(status_code=404, detail="المنطقة غير موجودة")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating area: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/areas/delete/{area_id}")
async def delete_area(
    area_id: int,
    data: DeleteAreaRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete an area (admin only)"""
    await verify_admin_or_manager(db, data.admin_user_id)
    service = AreasService(db)
    try:
        result = await service.delete(area_id)
        if not result:
            raise HTTPException(status_code=404, detail="المنطقة غير موجودة")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting area: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Representative Areas Endpoints
# ============================================================

@router.post("/rep-areas/list", response_model=RepAreasListResponse)
async def list_rep_areas(
    db: AsyncSession = Depends(get_db),
):
    """List all representative-area assignments (public)"""
    service = Representative_areasService(db)
    try:
        result = await service.get_list(skip=0, limit=2000)
        items = result.get("items", [])
        return RepAreasListResponse(items=items)
    except Exception as e:
        logger.error(f"Error listing rep areas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rep-areas/set")
async def set_rep_areas(
    data: SetRepAreasRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set areas for a representative (replaces existing assignments)"""
    await verify_admin_or_manager(db, data.admin_user_id)
    service = Representative_areasService(db)
    try:
        # Get existing assignments for this rep
        existing = await service.list_by_field("representative_id", data.representative_id, skip=0, limit=500)

        # Delete removed assignments
        existing_area_ids = {item.area_id for item in existing}
        new_area_ids = set(data.area_ids)

        for item in existing:
            if item.area_id not in new_area_ids:
                await service.delete(item.id)

        # Create new assignments
        for area_id in new_area_ids:
            if area_id not in existing_area_ids:
                await service.create({
                    "representative_id": data.representative_id,
                    "area_id": area_id,
                })

        return {"success": True, "representative_id": data.representative_id, "area_count": len(data.area_ids)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting rep areas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))