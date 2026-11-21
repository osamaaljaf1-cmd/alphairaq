import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core import login_throttle
from services.access import require_admin
from schemas.auth import UserResponse
from dependencies.auth import get_current_user
from core.auth import create_access_token
from core.password import hash_password, verify_password, is_legacy_hash
from services.app_users import App_usersService
from services.login_security import end_all_sessions, password_problem, record_login

# compared against when the username does not exist, so a wrong username takes as long as a wrong
# password and the answer time does not tell which usernames exist
_DUMMY_HASH = hash_password("not-a-real-password-0")

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
    # the password used does not meet the current rule: the app suggests changing it
    weak_password: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user with email and password (bcrypt)"""
    account = login_throttle.normalise_account(data.email)
    address = login_throttle.client_address(request)
    wait = login_throttle.seconds_locked(account, address)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        await record_login(db, account, False, "locked", request)
        raise HTTPException(status_code=429, detail=f"محاولات دخول خاطئة كثيرة. حاول بعد {minutes} دقيقة")
    try:
        service = App_usersService(db)
        user = await service.get_by_field("email", data.email.strip().lower())

        if not user:
            # Try with original case
            user = await service.get_by_field("email", data.email.strip())

        if not user:
            verify_password(data.password, _DUMMY_HASH)  # same time as a wrong password
            login_throttle.record_failure(account, address)
            await record_login(db, account, False, "unknown_user", request)
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

        if not user.password_hash:
            login_throttle.record_failure(account, address)
            await record_login(db, account, False, "no_password", request, user.user_id)
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

        if not verify_password(data.password, user.password_hash):
            login_throttle.record_failure(account, address)
            await record_login(db, account, False, "wrong_password", request, user.user_id)
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

        login_throttle.record_success(account)

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
            await record_login(db, account, False, "blocked", request, user.user_id)
            raise HTTPException(status_code=403, detail="هذا الحساب محظور. تواصل مع المسؤول")

        if user.status == "inactive":
            await record_login(db, account, False, "inactive", request, user.user_id)
            raise HTTPException(status_code=403, detail="هذا الحساب غير نشط. تواصل مع المسؤول")

        await record_login(db, account, True, "ok", request, user.user_id)

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
            weak_password=password_problem(data.password, user.email) is not None,
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
    current_user: UserResponse = Depends(get_current_user),
):
    """Reset a user's password (signed-in admin only)"""
    await require_admin(db, current_user, strict=True)
    try:
        service = App_usersService(db)

        # Find the user by email
        user = await service.get_by_field("email", data.email.strip())
        if not user:
            # Try with original case
            user = await service.get_by_field("email", data.email.strip().lower())

        if not user:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")

        problem = password_problem(data.new_password, user.email)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

        # Hash the new password with bcrypt
        new_hash = hash_password(data.new_password)
        await service.update(user.id, {"password_hash": new_hash})
        await end_all_sessions(db, user.user_id)  # the old password's sessions stop working

        logger.info(f"Password reset for user: {data.email}")
        return {"success": True, "message": "تم إعادة تعيين كلمة المرور بنجاح"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password reset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="حدث خطأ في النظام")


class ChangeMyPasswordRequest(BaseModel):
    current_password: str
    new_password: str


async def _my_account(db: AsyncSession, current_user: UserResponse):
    service = App_usersService(db)
    user = await service.get_by_field("user_id", str(current_user.id))
    if not user:
        raise HTTPException(status_code=404, detail="هذا الحساب لا يستعمل كلمة مرور هنا")
    return service, user


@router.post("/change-my-password")
async def change_my_password(
    data: ChangeMyPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Change your own password (the current one is asked again). Your other devices are logged out;
    this one gets a new session."""
    service, user = await _my_account(db, current_user)
    account = login_throttle.normalise_account(user.email)
    address = login_throttle.client_address(request)
    if login_throttle.seconds_locked(account, address):
        raise HTTPException(status_code=429, detail="محاولات خاطئة كثيرة. حاول لاحقاً")
    if not verify_password(data.current_password, user.password_hash or ""):
        login_throttle.record_failure(account, address)
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    problem = password_problem(data.new_password, user.email)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if verify_password(data.new_password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="اختر كلمة مرور مختلفة عن الحالية")
    await service.update(user.id, {"password_hash": hash_password(data.new_password)})
    await end_all_sessions(db, user.user_id)
    token = create_access_token(
        claims={"sub": user.user_id, "email": user.email, "name": user.name, "role": user.role},
        expires_minutes=60 * 24 * 7,
    )
    logger.info("Password changed by its owner: user_id=%s", user.user_id)
    return {"success": True, "access_token": token, "message": "تم تغيير كلمة المرور وخرجت أجهزتك الأخرى"}


@router.post("/logout-everywhere")
async def logout_everywhere(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    """End every session of your account, this one included."""
    await end_all_sessions(db, str(current_user.id))
    return {"success": True}


@router.post("/logout-user/{user_id}")
async def logout_user(user_id: str, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    """(admin) End every session of an account: it has to log in again on every device."""
    await require_admin(db, current_user, strict=True)
    await end_all_sessions(db, user_id)
    logger.info("All sessions of %s ended by %s", user_id, current_user.id)
    return {"success": True}


@router.get("/login-events")
async def login_events(
    user_id: str = "",
    failed_only: bool = False,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """(admin) The latest login attempts, all or one account's, newest first."""
    from sqlalchemy import select
    from models.auth_security import Login_events

    await require_admin(db, current_user, strict=True)
    q = select(Login_events).order_by(Login_events.id.desc()).limit(max(1, min(limit, 500)))
    if user_id:
        q = q.where(Login_events.user_id == user_id)
    if failed_only:
        q = q.where(Login_events.ok.is_(False))
    rows = (await db.execute(q)).scalars().all()
    return {
        "events": [
            {
                "id": r.id,
                "account": r.account,
                "user_id": r.user_id,
                "ok": bool(r.ok),
                "reason": r.reason,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/me")
async def get_current_local_user():
    """
    This endpoint is not used directly - local auth state is managed on the frontend via localStorage.
    """
    return {"message": "Use localStorage for local auth state"}