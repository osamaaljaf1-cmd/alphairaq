import logging
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/migrate", tags=["migrate"])

# Whitelist of allowed table names and column definitions for migration safety
ALLOWED_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "doctors": [
        ("customer_number", "VARCHAR"),
        ("area", "VARCHAR"),
        ("representative_id", "INTEGER"),
        ("status", "VARCHAR"),
        ("doctor_class", "VARCHAR"),
    ],
    "pharmacies": [
        ("customer_number", "VARCHAR"),
        ("representative_id", "INTEGER"),
        ("status", "VARCHAR"),
    ],
    "agreements": [
        ("bonus_qty_threshold", "INTEGER"),
        ("bonus_qty", "INTEGER"),
    ],
    "order_items": [
        ("gift_qty", "INTEGER DEFAULT 0"),
        ("has_deal", "BOOLEAN DEFAULT FALSE"),
    ],
    "orders": [
        ("manager_approved_at", "TIMESTAMP WITH TIME ZONE"),
        ("manager_approved_by", "VARCHAR"),
        ("accounting_approved_at", "TIMESTAMP WITH TIME ZONE"),
        ("accounting_approved_by", "VARCHAR"),
        ("printed_at", "TIMESTAMP WITH TIME ZONE"),
        ("printed_by", "VARCHAR"),
        ("delivered_at", "TIMESTAMP WITH TIME ZONE"),
        ("delivered_by", "VARCHAR"),
    ],
    "permissions": [
        ("can_import", "BOOLEAN DEFAULT FALSE"),
        ("can_export", "BOOLEAN DEFAULT FALSE"),
    ],
    "activity_logs": [
        ("item_id", "INTEGER"),
        ("item_name", "VARCHAR"),
        ("latitude", "FLOAT"),
        ("longitude", "FLOAT"),
    ],
}

# Allowed table names for validation
ALLOWED_TABLE_NAMES = frozenset(ALLOWED_MIGRATIONS.keys())

# Allowed column name pattern (alphanumeric + underscore only)
import re
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Allowed SQL types for column definitions
ALLOWED_SQL_TYPES = frozenset([
    "VARCHAR",
    "INTEGER",
    "FLOAT",
    "BOOLEAN",
    "TEXT",
    "TIMESTAMP WITH TIME ZONE",
    "INTEGER DEFAULT 0",
    "BOOLEAN DEFAULT FALSE",
])


def _validate_identifier(name: str) -> bool:
    """Validate that an identifier is safe (alphanumeric + underscore only)."""
    return bool(SAFE_IDENTIFIER_RE.match(name)) and len(name) <= 64


def _build_alter_sql(table_name: str, col_name: str, col_type: str) -> str | None:
    """Build a safe ALTER TABLE SQL statement using whitelisted values only."""
    if table_name not in ALLOWED_TABLE_NAMES:
        return None
    if not _validate_identifier(col_name):
        return None
    if col_type not in ALLOWED_SQL_TYPES:
        return None
    # All values are from the hardcoded whitelist, safe to interpolate
    return f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {col_type}'


@router.post("/add_columns")
async def add_missing_columns(db: AsyncSession = Depends(get_db)):
    """Add missing columns to all tables and create messages table"""
    results = []

    for table_name, columns in ALLOWED_MIGRATIONS.items():
        for col_name, col_type in columns:
            sql = _build_alter_sql(table_name, col_name, col_type)
            if sql is None:
                results.append(f"{table_name}.{col_name}: skipped (validation failed)")
                continue
            try:
                await db.execute(text(sql))
                await db.commit()
                results.append(f"{table_name}.{col_name}: added")
            except Exception as e:
                await db.rollback()
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                    results.append(f"{table_name}.{col_name}: already exists")
                else:
                    results.append(f"{table_name}.{col_name}: error - {str(e)}")

    # Set default status for existing records using parameterized queries
    try:
        await db.execute(
            text("UPDATE doctors SET status = :status WHERE status IS NULL"),
            {"status": "active"},
        )
        await db.execute(
            text("UPDATE pharmacies SET status = :status WHERE status IS NULL"),
            {"status": "active"},
        )
        await db.commit()
        results.append("default status set to 'active' for existing records")
    except Exception as e:
        await db.rollback()
        results.append(f"default status error: {str(e)}")

    # Create messages table if not exists (static DDL, no user input)
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                doctor_id INTEGER NOT NULL,
                pharmacy_id INTEGER,
                product_id INTEGER,
                return_id INTEGER,
                order_id INTEGER,
                message_type VARCHAR NOT NULL DEFAULT 'whatsapp',
                message_content TEXT NOT NULL,
                doctor_phone VARCHAR,
                doctor_name VARCHAR,
                product_name VARCHAR,
                pharmacy_name VARCHAR,
                status VARCHAR DEFAULT 'sent',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        await db.commit()
        results.append("messages table: created or already exists")
    except Exception as e:
        await db.rollback()
        results.append(f"messages table: error - {str(e)}")

    return {"results": results}


# Hardcoded list of roles and pages for permission seeding
_SEED_ROLES = ("admin", "manager", "rep", "accounting", "delivery", "scientific", "sales")
_SEED_PAGES = (
    "dashboard", "orders", "returns", "agreements", "target", "customers",
    "items", "map", "chat", "admin_users", "permissions", "areas",
    "rep_operations", "doctor_visits", "pharmacy_visits",
)

# Pages that should have can_view=True for ALL roles by default
_DEFAULT_VIEW_ALL_PAGES = frozenset({"doctor_visits", "pharmacy_visits"})


@router.post("/seed-permissions")
async def seed_permissions(db: AsyncSession = Depends(get_db)):
    """
    Seed missing permission records for all role+page combinations.
    Only inserts rows that don't already exist (idempotent).
    Admin role gets can_view=True by default for all pages.
    doctor_visits and pharmacy_visits get can_view=True for ALL roles.
    """
    results = []
    for role in _SEED_ROLES:
        for page in _SEED_PAGES:
            try:
                # Check if permission already exists
                check = await db.execute(
                    text("SELECT id FROM permissions WHERE role = :role AND page = :page"),
                    {"role": role, "page": page},
                )
                existing = check.scalar_one_or_none()
                if existing:
                    continue  # Already exists, skip

                # Admin gets can_view for all pages; all roles get can_view for visit pages
                default_view = role == "admin" or page in _DEFAULT_VIEW_ALL_PAGES
                await db.execute(
                    text(
                        "INSERT INTO permissions (role, page, can_view, can_add, can_edit, can_delete, can_import, can_export) "
                        "VALUES (:role, :page, :can_view, false, false, false, false, false)"
                    ),
                    {"role": role, "page": page, "can_view": default_view},
                )
                await db.commit()
                results.append(f"{role}/{page}: added (can_view={default_view})")
            except Exception as e:
                await db.rollback()
                if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                    results.append(f"{role}/{page}: already exists")
                else:
                    results.append(f"{role}/{page}: error - {str(e)}")

    if not results:
        results.append("All permission records already exist")

    return {"success": True, "results": results}


@router.post("/grant-visit-permissions")
async def grant_visit_permissions(db: AsyncSession = Depends(get_db)):
    """
    Grant can_view=True for doctor_visits and pharmacy_visits to ALL existing roles.
    This updates existing permission records that have can_view=False, and creates
    missing records for any role that doesn't have them yet.
    Idempotent - safe to call multiple times.
    """
    results = []

    # Step 1: Update existing records where can_view is False
    for page in ("doctor_visits", "pharmacy_visits"):
        try:
            update_result = await db.execute(
                text(
                    "UPDATE permissions SET can_view = true "
                    "WHERE page = :page AND (can_view = false OR can_view IS NULL)"
                ),
                {"page": page},
            )
            await db.commit()
            count = update_result.rowcount
            if count > 0:
                results.append(f"{page}: updated {count} roles to can_view=true")
            else:
                results.append(f"{page}: all roles already have can_view=true")
        except Exception as e:
            await db.rollback()
            results.append(f"{page}: update error - {str(e)}")

    # Step 2: Create missing records for roles that don't have these pages at all
    for role in _SEED_ROLES:
        for page in ("doctor_visits", "pharmacy_visits"):
            try:
                check = await db.execute(
                    text("SELECT id FROM permissions WHERE role = :role AND page = :page"),
                    {"role": role, "page": page},
                )
                if check.scalar_one_or_none():
                    continue  # Already exists

                await db.execute(
                    text(
                        "INSERT INTO permissions (role, page, can_view, can_add, can_edit, can_delete, can_import, can_export) "
                        "VALUES (:role, :page, true, false, false, false, false, false)"
                    ),
                    {"role": role, "page": page},
                )
                await db.commit()
                results.append(f"{role}/{page}: created with can_view=true")
            except Exception as e:
                await db.rollback()
                if "duplicate" in str(e).lower():
                    continue
                results.append(f"{role}/{page}: insert error - {str(e)}")

    return {"success": True, "results": results}