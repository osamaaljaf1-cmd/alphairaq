import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.representatives import Representatives
from models.representative_areas import Representative_areas
from models.user_assignments import User_assignments
from models.areas import Areas
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)


async def expand_area_ids(db: AsyncSession, area_ids: set[int]) -> set[int]:
    """Expand a set of area ids to include every descendant area (assigning a
    parent area implies access to everything nested under it, matching the
    area tree shown on the Permissions page)."""
    if not area_ids:
        return set()

    all_areas = await db.execute(select(Areas.id, Areas.parent_area_id))
    children_map: dict[int, list[int]] = {}
    for area_id, parent_id in all_areas.all():
        if parent_id is not None:
            children_map.setdefault(parent_id, []).append(area_id)

    expanded: set[int] = set()
    queue = list(area_ids)
    while queue:
        current = queue.pop()
        if current in expanded:
            continue
        expanded.add(current)
        queue.extend(children_map.get(current, []))
    return expanded


async def _areas_for_reps(db: AsyncSession, rep_ids: list[int]) -> set[int]:
    if not rep_ids:
        return set()
    result = await db.execute(
        select(Representative_areas.area_id).where(
            Representative_areas.representative_id.in_(rep_ids)
        )
    )
    direct_area_ids = {row[0] for row in result.all()}
    return await expand_area_ids(db, direct_area_ids)


class CustomerScope:
    """Resolved visibility scope for the pharmacies/doctors list endpoints.

    unrestricted=True means "show everything" (admin/accounting, or any role
    this feature doesn't apply to). Otherwise a customer is visible if its
    area_id is in area_ids OR its representative_id is in rep_ids — the
    rep_id fallback keeps a rep's own directly-assigned customers visible
    even before/without an area assignment, so this can't silently blank a
    rep's whole customer list just because areas haven't been configured
    for them yet.
    """

    def __init__(self, unrestricted: bool, area_ids: Optional[set[int]] = None, rep_ids: Optional[list[int]] = None):
        self.unrestricted = unrestricted
        self.area_ids = area_ids or set()
        self.rep_ids = rep_ids or []


async def get_customer_scope(db: AsyncSession, current_user: UserResponse) -> CustomerScope:
    """Resolve the current user's representative row + role and compute what
    customers (pharmacies/doctors) they're allowed to see.

    - admin / accounting (or any user with no representative row at all,
      e.g. an app_user-only account): unrestricted.
    - rep: their own assigned areas (expanded to descendants) + their own
      representative_id as a fallback.
    - manager: the union of areas assigned to every rep under them + those
      reps' ids as a fallback. A manager with zero assigned reps is left
      unrestricted (not yet configured), matching this app's existing
      fail-open convention for that case elsewhere.
    - any other role (delivery, sales, scientific, custom roles): unrestricted
      — this feature only scopes rep/manager as requested.
    """
    rep_result = await db.execute(
        select(Representatives).where(Representatives.user_id == current_user.id)
    )
    rep = rep_result.scalar_one_or_none()

    if not rep or rep.role not in ("rep", "manager"):
        return CustomerScope(unrestricted=True)

    if rep.role == "rep":
        area_ids = await _areas_for_reps(db, [rep.id])
        return CustomerScope(unrestricted=False, area_ids=area_ids, rep_ids=[rep.id])

    # manager
    assigned_result = await db.execute(
        select(User_assignments.assigned_rep_id).where(
            User_assignments.manager_rep_id == rep.id,
            User_assignments.assignment_type == "manager",
        )
    )
    assigned_rep_ids = [row[0] for row in assigned_result.all()]
    if not assigned_rep_ids:
        return CustomerScope(unrestricted=True)

    area_ids = await _areas_for_reps(db, assigned_rep_ids)
    return CustomerScope(unrestricted=False, area_ids=area_ids, rep_ids=assigned_rep_ids)
