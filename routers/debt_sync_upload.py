"""The accountant uploads the debts export of Phenix from the app (no script, no key).

Same mirroring as the script next to the accounting program (services/debt_sync.py): the file is the
FULL list of open invoices, new invoices of registered customers are added, known ones updated,
invoices gone from the file are closed, unknown customers are refused and listed, and a run that
would close an unusual share of the debts is held back unless forced. The returns of the file are mirrored
too, as negative credits on their customers. The report also compares, per representative, what the
program shows with what the app holds before and after. Every applied run can be undone
(the latest one). Needs the "debts_phenix_sync" action permission (accounting and admin by default).

- POST /api/v1/debt-sync-upload/preview   the file -> what WOULD change (writes nothing)
- POST /api/v1/debt-sync-upload/apply     the same file + confirm=true -> applies it
- GET  /api/v1/debt-sync-upload/runs      the latest runs
- POST /api/v1/debt-sync-upload/undo/{id} put the latest applied run back as it was
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.app_users import App_users
from models.debt_sync import DebtSyncRuns
from schemas.auth import UserResponse
from services.debt_sync import UndoError, apply_plan, build_plan, ensure_tables, rep_breakdown, report, undo_run
from services.feature_permissions import require_feature
from services.phenix_export import ExportError, MAX_FILE_BYTES, read_export

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/debt-sync-upload", tags=["debt_sync_upload"])

FEATURE = "debts_phenix_sync"


async def _read_upload(file: UploadFile):
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel بصيغة xlsx كما يصدّره فينكس")
    content = await file.read(MAX_FILE_BYTES + 1)
    try:
        return read_export(content)
    except ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _account_name(db: AsyncSession, user: UserResponse) -> str:
    name = (await db.execute(select(App_users.name).where(App_users.user_id == str(user.id)).order_by(App_users.id))).scalars().first()
    return (name or "").strip() or user.name or str(user.id)


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, FEATURE)
    rows, returns, info, credits = await _read_upload(file)
    plan = await build_plan(db, rows, returns, credits)
    result = report(plan)
    result.update({"dry_run": True, "applied": False, "run_id": None, "file": info, "file_name": file.filename})
    result["by_rep"] = await rep_breakdown(db, plan)
    return result


@router.post("/apply")
async def apply(
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    force: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, FEATURE)
    if not confirm:
        raise HTTPException(status_code=400, detail="يلزم تأكيد التطبيق")
    rows, returns, info, credits = await _read_upload(file)
    # planned again against the debts as they are now, not as they were when the preview was made
    plan = await build_plan(db, rows, returns, credits)
    result = report(plan)
    result.update({"dry_run": False, "applied": False, "run_id": None, "file": info, "file_name": file.filename})
    result["by_rep"] = await rep_breakdown(db, plan)
    if plan.blocked_reason and not (force and plan.valid_rows > 0):
        return result
    try:
        source = f"phenix-upload:{await _account_name(db, current_user)}"
        result["run_id"] = await apply_plan(db, plan, source)
        result["applied"] = True
    except Exception as e:
        logger.error(f"debt sync upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="فشلت المزامنة ولم يتغير شيء")
    return result


@router.get("/runs")
async def runs(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, FEATURE)
    await ensure_tables(db)
    rows = (await db.execute(select(DebtSyncRuns).order_by(DebtSyncRuns.id.desc()).limit(max(1, min(limit, 50))))).scalars().all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "undone_at": r.undone_at.isoformat() if r.undone_at else None,
            "summary": json.loads(r.summary) if r.summary else None,
        }
        for r in rows
    ]


@router.post("/undo/{run_id}")
async def undo(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, FEATURE)
    try:
        return await undo_run(db, run_id)
    except UndoError as e:
        raise HTTPException(status_code=409, detail=str(e))
