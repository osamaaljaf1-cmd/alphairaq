"""One-time fix: replace NULL values in products table with defaults."""
import logging
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

router = APIRouter(prefix="/api/v1/fix", tags=["fix"])


@router.post("/products-nulls")
async def fix_product_nulls(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace NULL stock_quantity with 0 and NULL category/code/unit with empty string."""
    try:
        await db.execute(
            text("UPDATE products SET stock_quantity = 0 WHERE stock_quantity IS NULL")
        )
        await db.execute(
            text("UPDATE products SET category = '' WHERE category IS NULL")
        )
        await db.execute(
            text("UPDATE products SET code = '' WHERE code IS NULL")
        )
        await db.execute(
            text("UPDATE products SET unit = '' WHERE unit IS NULL")
        )
        await db.commit()
        return {"status": "ok", "message": "Null values fixed successfully"}
    except Exception as e:
        logging.error(f"Error fixing nulls: {e}")
        await db.rollback()
        return {"status": "error", "message": str(e)}