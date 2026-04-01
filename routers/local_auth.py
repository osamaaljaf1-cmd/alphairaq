import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.auth import create_access_token
from core.password import hash_password, verify_password, is_legacy_hash
from services.app_users import App_usersService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/local-auth", tags=["local-auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    user_id: str = ""
    name: str = ""
    email: str = ""
    role: str = ""
    status: str = ""
    message: str = ""
    app_user_id: int = 0
    access_token: str = ""


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user with email and password (bcrypt)"""
    try:
        service = App_usersService(db)
        user = await service.get_by_field("email", data.email.strip().lower())

        if not user:
            # Try with original case
            user = await service.get_by_field("email", data.email.strip())

        if not user:
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

        if not user.password_hash:
            raise HTTPException(status_code=401, detail="هذا الحساب لا يدعم تسجيل الدخول بكلمة المرور")

        if not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

        # Auto-upgrade legacy SHA-256 hash to bcrypt on successful login
        if is_legacy_hash(user.password_hash):
            try:
                new_hash = hash_password(data.password)
                await service.update(user.id, {"password_hash": new_hash})
                logger.info(f"Auto-upgraded password hash to bcrypt for user: {user.email}")
            except Exception as upgrade_err:
                # Non-critical: don't block login if upgrade fails
                logger.warning(f"Failed to auto-upgrade password hash: {upgrade_err}")

        if user.status == "blocked":
            raise HTTPException(status_code=403, detail="هذا الحساب محظور. تواصل مع المسؤول")

        if user.status == "inactive":
            raise HTTPException(status_code=403, detail="هذا الحساب غير نشط. تواصل مع المسؤول")

        logger.info(f"Local login success: email={data.email}, role={user.role}")

        # Generate JWT access token for authenticated API calls
        token = create_access_token(
            claims={
                "sub": user.user_id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
            },
            expires_minutes=60 * 24 * 7,  # 7 days
        )

        return LoginResponse(
            success=True,
            user_id=user.user_id,
            name=user.name,
            email=user.email,
            role=user.role,
            status=user.status,
            message="تم تسجيل الدخول بنجاح",
            app_user_id=user.id,
            access_token=token,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="حدث خطأ في النظام")


class ResetPasswordRequest(BaseModel):
    email: str
    new_password: str
    admin_key: str = ""


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password (admin key or admin user required)"""
    try:
        service = App_usersService(db)

        # Find the user by email
        user = await service.get_by_field("email", data.email.strip())
        if not user:
            # Try with original case
            user = await service.get_by_field("email", data.email.strip().lower())

        if not user:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")

        if not data.new_password or len(data.new_password) < 4:
            raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 4 أحرف على الأقل")

        # Hash the new password with bcrypt
        new_hash = hash_password(data.new_password)
        await service.update(user.id, {"password_hash": new_hash})

        logger.info(f"Password reset for user: {data.email}")
        return {"success": True, "message": "تم إعادة تعيين كلمة المرور بنجاح"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password reset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="حدث خطأ في النظام")


@router.get("/me")
async def get_current_local_user():
    """
    This endpoint is not used directly - local auth state is managed on the frontend via localStorage.
    """
    return {"message": "Use localStorage for local auth state"}