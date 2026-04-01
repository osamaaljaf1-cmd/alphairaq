import logging
import secrets
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.password import hash_password
from services.app_users import App_usersService
from services.representatives import RepresentativesService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin-users", tags=["admin-users"])


async def verify_local_admin(db: AsyncSession, admin_user_id: str) -> bool:
    """Verify that the given user_id belongs to an active admin."""
    if not admin_user_id:
        return False
    service = App_usersService(db)
    user = await service.get_by_field("user_id", admin_user_id)
    if user and user.role == "admin" and user.status == "active":
        return True
    # Also check representatives table
    rep_service = RepresentativesService(db)
    rep = await rep_service.get_by_field("user_id", admin_user_id)
    if rep and rep.role == "admin":
        return True
    return False


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str = "rep"


class LocalCreateUserRequest(BaseModel):
    admin_user_id: str
    name: str
    email: str
    password: str
    role: str = "rep"


class CreateUserResponse(BaseModel):
    success: bool
    id: Optional[int] = None
    message: str = ""


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


class LocalUpdateUserRequest(BaseModel):
    admin_user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class LocalChangePasswordRequest(BaseModel):
    admin_user_id: str
    new_password: str


class LocalDeleteUserRequest(BaseModel):
    admin_user_id: str


class LocalListRequest(BaseModel):
    user_id: str


class UserItem(BaseModel):
    id: int
    user_id: str
    name: str
    email: str
    role: str
    status: str

    class Config:
        from_attributes = True


class UsersListResponse(BaseModel):
    items: List[UserItem]
    total: int


# ============================================================
# Local Auth Endpoints (for users logged in via username/password)
# ============================================================

@router.post("/local-list", response_model=UsersListResponse)
async def local_list_users(
    data: LocalListRequest,
    db: AsyncSession = Depends(get_db),
):
    """List all users (local admin auth)"""
    if not await verify_local_admin(db, data.user_id):
        raise HTTPException(status_code=403, detail="صلاحيات المسؤول مطلوبة")

    service = App_usersService(db)
    try:
        result = await service.get_list(skip=0, limit=2000)
        items = result.get("items", [])
        total = result.get("total", 0)
        return UsersListResponse(items=items, total=total)
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في النظام: {str(e)}")


@router.post("/local-create-user", response_model=CreateUserResponse)
async def local_create_user(
    data: LocalCreateUserRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (local admin auth)"""
    if not await verify_local_admin(db, data.admin_user_id):
        raise HTTPException(status_code=403, detail="صلاحيات المسؤول مطلوبة")

    service = App_usersService(db)
    try:
        # Check if username already exists
        existing = await service.get_by_field("email", data.email.strip())
        if existing:
            raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")

        # Hash the password
        password_hash = hash_password(data.password)

        # Generate unique user_id
        new_user_id = f"local_{secrets.token_hex(8)}"

        # Create the user in app_users
        result = await service.create({
            "user_id": new_user_id,
            "name": data.name,
            "email": data.email.strip(),
            "password_hash": password_hash,
            "role": data.role,
            "status": "active",
        })

        if not result:
            raise HTTPException(status_code=400, detail="فشل في إنشاء المستخدم")

        # Also create a representative record
        try:
            rep_service = RepresentativesService(db)
            await rep_service.create({
                "user_id": new_user_id,
                "name": data.name,
                "role": data.role,
            })
        except Exception as rep_err:
            logger.warning(f"Failed to create representative record: {rep_err}")

        logger.info(f"User created via local auth: id={result.id}, email={data.email}, role={data.role}")
        return CreateUserResponse(success=True, id=result.id, message="تم إنشاء المستخدم بنجاح")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في النظام: {str(e)}")


@router.put("/local-update/{user_id}", response_model=UserItem)
async def local_update_user(
    user_id: int,
    data: LocalUpdateUserRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update user role or status (local admin auth)"""
    if not await verify_local_admin(db, data.admin_user_id):
        raise HTTPException(status_code=403, detail="صلاحيات المسؤول مطلوبة")

    service = App_usersService(db)
    try:
        update_dict = {}
        if data.name is not None:
            update_dict["name"] = data.name
        if data.email is not None:
            # Check if email is taken by another user
            existing = await service.get_by_field("email", data.email.strip())
            if existing and existing.id != user_id:
                raise HTTPException(status_code=400, detail="اسم المستخدم مستخدم بالفعل")
            update_dict["email"] = data.email.strip()
        if data.role is not None:
            update_dict["role"] = data.role
        if data.status is not None:
            update_dict["status"] = data.status

        if not update_dict:
            raise HTTPException(status_code=400, detail="لا توجد حقول للتحديث")

        result = await service.update(user_id, update_dict)
        if not result:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")

        # Also update representative record if exists
        try:
            rep_service = RepresentativesService(db)
            user_record = await service.get_by_id(user_id)
            if user_record:
                rep = await rep_service.get_by_field("user_id", user_record.user_id)
                if rep:
                    rep_update = {}
                    if data.role is not None:
                        rep_update["role"] = data.role
                    if data.name is not None:
                        rep_update["name"] = data.name
                    if rep_update:
                        await rep_service.update(rep.id, rep_update)
        except Exception as rep_err:
            logger.warning(f"Failed to update representative record: {rep_err}")

        logger.info(f"User {user_id} updated via local auth: {update_dict}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في النظام: {str(e)}")


@router.post("/local-change-password/{user_id}")
async def local_change_password(
    user_id: int,
    data: LocalChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Change a user's password (local admin auth)"""
    if not await verify_local_admin(db, data.admin_user_id):
        raise HTTPException(status_code=403, detail="صلاحيات المسؤول مطلوبة")

    if not data.new_password or len(data.new_password) < 4:
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 4 أحرف على الأقل")

    service = App_usersService(db)
    try:
        user_record = await service.get_by_id(user_id)
        if not user_record:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")

        password_hash = hash_password(data.new_password)
        result = await service.update(user_id, {"password_hash": password_hash})
        if not result:
            raise HTTPException(status_code=500, detail="فشل في تغيير كلمة المرور")

        logger.info(f"Password changed for user {user_id} by admin {data.admin_user_id}")
        return {"success": True, "message": "تم تغيير كلمة المرور بنجاح"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في النظام: {str(e)}")


@router.delete("/local-delete/{user_id}")
async def local_delete_user(
    user_id: int,
    data: LocalDeleteUserRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete a user (local admin auth)"""
    if not await verify_local_admin(db, data.admin_user_id):
        raise HTTPException(status_code=403, detail="صلاحيات المسؤول مطلوبة")

    service = App_usersService(db)
    try:
        user_record = await service.get_by_id(user_id)
        if not user_record:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")

        # Prevent deleting yourself
        if user_record.user_id == data.admin_user_id:
            raise HTTPException(status_code=400, detail="لا يمكنك حذف حسابك الخاص")

        # Delete representative record first
        try:
            rep_service = RepresentativesService(db)
            rep = await rep_service.get_by_field("user_id", user_record.user_id)
            if rep:
                await rep_service.delete(rep.id)
        except Exception as rep_err:
            logger.warning(f"Failed to delete representative record: {rep_err}")

        # Delete the user
        await service.delete(user_id)

        logger.info(f"User {user_id} ({user_record.email}) deleted by admin {data.admin_user_id}")
        return {"success": True, "message": "تم حذف المستخدم بنجاح"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"خطأ في النظام: {str(e)}")


# ============================================================
# Atoms Cloud Auth Endpoints (original endpoints)
# ============================================================

@router.post("/create-user", response_model=CreateUserResponse)
async def create_user(
    data: CreateUserRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (Atoms Cloud admin only)"""
    service = App_usersService(db)
    try:
        # Verify the caller is admin
        admin_check = await service.get_by_field("user_id", str(current_user.id))
        if not admin_check or admin_check.role != "admin":
            rep_service = RepresentativesService(db)
            rep = await rep_service.get_by_field("user_id", str(current_user.id))
            if not rep or rep.role != "admin":
                raise HTTPException(status_code=403, detail="Only admin users can create users")

        # Check if email already exists
        existing = await service.get_by_field("email", data.email)
        if existing:
            raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")

        # Hash the password
        password_hash = hash_password(data.password)

        # Create the user
        result = await service.create({
            "user_id": f"local_{secrets.token_hex(8)}",
            "name": data.name,
            "email": data.email,
            "password_hash": password_hash,
            "role": data.role,
            "status": "active",
        })

        if not result:
            raise HTTPException(status_code=400, detail="Failed to create user")

        # Also create a representative record
        try:
            rep_service = RepresentativesService(db)
            await rep_service.create({
                "user_id": result.user_id,
                "name": data.name,
                "role": data.role,
            })
        except Exception as rep_err:
            logger.warning(f"Failed to create representative record: {rep_err}")

        logger.info(f"User created: id={result.id}, email={data.email}, role={data.role}")
        return CreateUserResponse(success=True, id=result.id, message="User created successfully")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("", response_model=UsersListResponse)
async def list_users(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users (Atoms Cloud admin only)"""
    service = App_usersService(db)
    try:
        # Verify admin
        admin_check = await service.get_by_field("user_id", str(current_user.id))
        is_admin = admin_check and admin_check.role == "admin"
        if not is_admin:
            rep_service = RepresentativesService(db)
            rep = await rep_service.get_by_field("user_id", str(current_user.id))
            if not rep or rep.role != "admin":
                raise HTTPException(status_code=403, detail="Only admin users can list users")

        result = await service.get_list(skip=0, limit=2000)
        items = result.get("items", [])
        total = result.get("total", 0)

        return UsersListResponse(items=items, total=total)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.put("/{user_id}", response_model=UserItem)
async def update_user(
    user_id: int,
    data: UpdateUserRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user role or status (Atoms Cloud admin only)"""
    service = App_usersService(db)
    try:
        # Verify admin
        admin_check = await service.get_by_field("user_id", str(current_user.id))
        is_admin = admin_check and admin_check.role == "admin"
        if not is_admin:
            rep_service = RepresentativesService(db)
            rep = await rep_service.get_by_field("user_id", str(current_user.id))
            if not rep or rep.role != "admin":
                raise HTTPException(status_code=403, detail="Only admin users can update users")

        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        if not update_dict:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = await service.update(user_id, update_dict)
        if not result:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"User {user_id} updated: {update_dict}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")