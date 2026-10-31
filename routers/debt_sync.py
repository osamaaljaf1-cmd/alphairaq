"""Debts mirrored from the accounting program (see services/debt_sync.py).

Called by a small script next to the accounting program, not by a logged-in user: it
authenticates with a dedicated key (DEBT_SYNC_KEY on the server, X-Sync-Key header) that
can do nothing but this. Without the variable the routes answer 503, so deploying the code
changes nothing until the key is set.

- POST /api/v1/debt-sync/apply        the full list of open invoices; dry_run (default) only reports
- GET  /api/v1/debt-sync/runs         the latest applied runs
- POST /api/v1/debt-sync/undo/{id}    put the latest applied run back as it was
"""
import hmac
import json
import logging
import os
import time
from collections import deque
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.debt_sync import DebtSyncRuns
from services.debt_sync import UndoError, apply_plan, build_plan, ensure_tables, report, undo_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/debt-sync", tags=["debt_sync"])

# wrong keys: at most this many per client address per window
_MAX_FAILURES = 10
_WINDOW_SECONDS = 300
_failures: Dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def require_sync_key(request: Request, x_sync_key: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("DEBT_SYNC_KEY", "")
    if len(expected) < 24:  # not configured (or too weak to accept): the feature is off
        raise HTTPException(status_code=503, detail="مزامنة الديون غير مفعّلة على الخادم")
    ip = _client_ip(request)
    now = time.monotonic()
    window = _failures.setdefault(ip, deque())
    while window and now - window[0] > _WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _MAX_FAILURES:
        raise HTTPException(status_code=429, detail="محاولات كثيرة، حاول لاحقاً")
    if not x_sync_key or not hmac.compare_digest(x_sync_key.encode(), expected.encode()):
        window.append(now)
        raise HTTPException(status_code=401, detail="مفتاح المزامنة غير صحيح")


class SyncRow(BaseModel):
    invoice_number: str
    customer_name: str
    amount: float
    remaining: float
    invoice_date: Optional[str] = None


class SyncRequest(BaseModel):
    rows: List[SyncRow] = Field(..., max_length=20000)
    ignored_returns: int = 0  # credit-note rows the sender left out
    dry_run: bool = True  # report only; the sender must ask explicitly to write
    force: bool = False  # apply even though the run closes an unusual share of the debts
    source: str = "phenix"


@router.post("/apply", dependencies=[Depends(require_sync_key)])
async def apply_sync(data: SyncRequest, db: AsyncSession = Depends(get_db)):
    plan = await build_plan(db, [r.model_dump() for r in data.rows], data.ignored_returns)
    result = report(plan)
    result["dry_run"] = data.dry_run
    result["applied"] = False
    result["run_id"] = None
    if data.dry_run:
        return result
    if plan.blocked_reason and not (data.force and plan.valid_rows > 0):
        return result
    try:
        result["run_id"] = await apply_plan(db, plan, data.source)
        result["applied"] = True
    except Exception as e:
        logger.error(f"debt sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="فشلت المزامنة ولم يتغير شيء")
    return result


@router.get("/runs", dependencies=[Depends(require_sync_key)])
async def list_runs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    await ensure_tables(db)
    rows = (
        await db.execute(select(DebtSyncRuns).order_by(DebtSyncRuns.id.desc()).limit(max(1, min(limit, 100))))
    ).scalars().all()
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


@router.post("/undo/{run_id}", dependencies=[Depends(require_sync_key)])
async def undo(run_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await undo_run(db, run_id)
    except UndoError as e:
        raise HTTPException(status_code=409, detail=str(e))
