import logging
import secrets
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.password import hash_password
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.app_users import App_users
from models.representatives import Representatives

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bootstrap", tags=["bootstrap"])


class BootstrapResponse(BaseModel):
    success: bool
    message: str
    role: str = ""


class SeedAdminRequest(BaseModel):
    name: str = "مسؤول النظام"
    email: str = "admin"
    password: str = "admin123"


@router.post("/setup-admin", response_model=BootstrapResponse)
async def setup_admin(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bootstrap: Make the current logged-in user an admin.
    Only works if NO admin exists in both app_users and representatives tables.
    """
    try:
        # Check if any admin exists in app_users
        admin_count_q = select(func.count()).select_from(App_users).where(App_users.role == "admin")
        result = await db.execute(admin_count_q)
        admin_count = result.scalar() or 0

        # Check if any admin exists in representatives
        rep_admin_q = select(func.count()).select_from(Representatives).where(Representatives.role == "admin")
        rep_result = await db.execute(rep_admin_q)
        rep_admin_count = rep_result.scalar() or 0

        if admin_count > 0 or rep_admin_count > 0:
            raise HTTPException(status_code=403, detail="Admin already exists. Cannot bootstrap.")

        user_id = str(current_user.id)
        display_name = current_user.name or current_user.email or user_id[:8]

        # Check if user already has an app_users record
        existing_q = select(App_users).where(App_users.user_id == user_id)
        existing_result = await db.execute(existing_q)
        existing = existing_result.scalar_one_or_none()

        if existing:
            existing.role = "admin"
            existing.status = "active"
            existing.name = display_name
        else:
            new_admin = App_users(
                user_id=user_id,
                name=display_name,
                email=current_user.email or "",
                role="admin",
                status="active",
            )
            db.add(new_admin)

        # Also create/update representative record
        rep_q = select(Representatives).where(Representatives.user_id == user_id)
        rep_result = await db.execute(rep_q)
        rep = rep_result.scalar_one_or_none()

        if rep:
            rep.role = "admin"
        else:
            new_rep = Representatives(
                user_id=user_id,
                name=display_name,
                role="admin",
            )
            db.add(new_rep)

        await db.commit()
        logger.info(f"Admin bootstrapped: user_id={user_id}, name={display_name}")

        return BootstrapResponse(
            success=True,
            message=f"تم تعيينك كمسؤول بنجاح! ({display_name})",
            role="admin",
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Bootstrap error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seed-admin", response_model=BootstrapResponse)
async def seed_admin(
    data: SeedAdminRequest = SeedAdminRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Seed an admin account with email/password for local login.
    Only works if NO admin exists yet. Public endpoint, no auth required.
    """
    try:
        # Check if any admin exists in app_users
        admin_count_q = select(func.count()).select_from(App_users).where(App_users.role == "admin")
        result = await db.execute(admin_count_q)
        admin_count = result.scalar() or 0

        if admin_count > 0:
            # Check if admin email already matches
            existing_admin = await db.execute(
                select(App_users).where(App_users.role == "admin").limit(1)
            )
            admin_user = existing_admin.scalar_one_or_none()
            if admin_user:
                return BootstrapResponse(
                    success=True,
                    message=f"حساب المسؤول موجود بالفعل: {admin_user.email}",
                    role="admin",
                )

        # Check if email already exists
        existing_q = select(App_users).where(App_users.email == data.email.strip().lower())
        existing_result = await db.execute(existing_q)
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Update existing user to admin
            existing.role = "admin"
            existing.status = "active"
            existing.name = data.name
            existing.password_hash = hash_password(data.password)
            await db.commit()
            logger.info(f"Existing user updated to admin: email={data.email}")
            return BootstrapResponse(
                success=True,
                message=f"تم تحديث الحساب الموجود إلى مسؤول: {data.email}",
                role="admin",
            )

        # Create new admin user
        user_id = f"local_{secrets.token_hex(8)}"
        password_hash = hash_password(data.password)

        new_admin = App_users(
            user_id=user_id,
            name=data.name,
            email=data.email.strip().lower(),
            password_hash=password_hash,
            role="admin",
            status="active",
        )
        db.add(new_admin)

        # Also create representative record
        new_rep = Representatives(
            user_id=user_id,
            name=data.name,
            role="admin",
        )
        db.add(new_rep)

        await db.commit()
        logger.info(f"Admin seeded: email={data.email}, user_id={user_id}")

        return BootstrapResponse(
            success=True,
            message=f"تم إنشاء حساب المسؤول بنجاح! البريد: {data.email}",
            role="admin",
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Seed admin error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check-admin", response_model=dict)
async def check_admin_exists(
    db: AsyncSession = Depends(get_db),
):
    """Check if any admin exists (public endpoint, no auth required)"""
    try:
        admin_count_q = select(func.count()).select_from(App_users).where(App_users.role == "admin")
        result = await db.execute(admin_count_q)
        admin_count = result.scalar() or 0

        rep_admin_q = select(func.count()).select_from(Representatives).where(Representatives.role == "admin")
        rep_result = await db.execute(rep_admin_q)
        rep_admin_count = rep_result.scalar() or 0

        has_admin = (admin_count > 0) or (rep_admin_count > 0)

        return {"has_admin": has_admin}
    except Exception as e:
        logger.error(f"Check admin error: {e}", exc_info=True)
        return {"has_admin": False}