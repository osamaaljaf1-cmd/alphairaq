import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.representatives import Representatives
from models.representative_areas import Representative_areas
from models.user_assignments import User_assignments
from models.staff_areas import Staff_areas
from models.areas import Areas
from models.pharmacies import Pharmacies
from schemas.auth import UserResponse
from services.customer_names import clean_name, normalize_name
from services.customer_names import normalize_name

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

    def __init__(self, unrestricted: bool, area_ids: Optional[set[int]] = None, rep_ids: Optional[list[int]] = None, by_branch: bool = False):
        self.unrestricted = unrestricted
        self.area_ids = area_ids or set()
        self.rep_ids = rep_ids or []
        # True when the scope comes from the staff member's own branch / areas (staff_areas):
        # their orders and returns then also include any order of a customer in those areas,
        # whoever entered it.
        self.by_branch = by_branch

    def covers(self, area_id, representative_id) -> bool:
        """Whether a customer with this area / rep is visible (in Python; ``customer_filter`` is the
        same rule in SQL). For a branch scope the rep only counts for a customer with no area: a
        customer whose area is in another branch stays there whoever their rep is."""
        if self.unrestricted:
            return True
        if area_id is not None and area_id in self.area_ids:
            return True
        if representative_id is None or representative_id not in self.rep_ids:
            return False
        return area_id is None or not self.by_branch

    def customer_filter(self, model):
        """SQL filter for a customer table (pharmacies, doctors) with area_id and representative_id."""
        by_area = model.area_id.in_(self.area_ids) if self.area_ids else false()
        if not self.rep_ids:
            return by_area
        by_rep = model.representative_id.in_(self.rep_ids)
        if self.by_branch:
            by_rep = and_(model.area_id.is_(None), by_rep)
        return or_(by_area, by_rep)


async def reps_in_areas(db: AsyncSession, area_ids: set[int]) -> list[int]:
    """Reps who work only inside these areas (every area they are assigned is one of them). A rep
    who also covers areas elsewhere is left out: their customers there must not show up through
    the rep, and their customers here show anyway by area."""
    if not area_ids:
        return []
    rows = await db.execute(select(Representative_areas.representative_id, Representative_areas.area_id))
    per_rep: dict[int, set[int]] = {}
    for rep_id, area_id in rows.all():
        if rep_id is not None:
            per_rep.setdefault(rep_id, set()).add(area_id)
    return sorted(rep_id for rep_id, areas in per_rep.items() if areas and areas <= area_ids)


async def get_staff_scope(db: AsyncSession, user_id: str) -> Optional[CustomerScope]:
    """The scope of a staff member linked to a branch / governorate / areas (staff_areas), or None
    when they have no link (then the role's usual rule applies)."""
    rows = await db.execute(select(Staff_areas.area_id).where(Staff_areas.user_id == str(user_id)))
    direct = {row[0] for row in rows.all()}
    if not direct:
        return None
    area_ids = await expand_area_ids(db, direct)
    return CustomerScope(unrestricted=False, area_ids=area_ids, rep_ids=await reps_in_areas(db, area_ids), by_branch=True)


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

    # A staff member linked to a branch / areas (anyone but a rep, whose areas are in
    # representative_areas) sees that part of the tree only. The admin always sees everything.
    if not rep or rep.role != "rep":
        staff = await get_staff_scope(db, current_user.id)
        if staff is not None:
            from services.permission_check import resolve_user_role

            if await resolve_user_role(db, current_user) != "admin":
                return staff

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


async def area_ids_by_name(db: AsyncSession) -> dict[str, int]:
    """Real areas keyed by normalized name (case and spacing ignored)."""
    rows = (await db.execute(select(Areas.id, Areas.name).order_by(Areas.id))).all()
    known: dict[str, int] = {}
    for area_id, name in rows:
        known.setdefault(normalize_name(name), area_id)
    return known


async def link_area(db: AsyncSession, data: dict, known: Optional[dict[str, int]] = None) -> None:
    """Link a customer to its real area. Visibility by area works on ``area_id``,
    so a customer saved with only an area *name* (Excel import, a client that
    sends the text alone) would never show up for the rep of that area. The id is
    looked up by name; a name that is not an area yet is created, the same as the
    customer form does. Does nothing when an id is already given or there is no
    name."""
    name = clean_name(data.get("area"))
    if data.get("area_id") is not None or not name:
        return
    known = known if known is not None else await area_ids_by_name(db)
    key = normalize_name(name)
    if key not in known:
        area = Areas(name=name)
        db.add(area)
        await db.flush()
        known[key] = area.id
    data["area_id"] = known[key]


def require_area(data: dict) -> None:
    """A customer added by hand must belong to an area — visibility by area
    depends on it. Call after link_area (which turns the name into an id)."""
    if data.get("area_id") is None:
        raise HTTPException(status_code=400, detail="المنطقة مطلوبة — اختر منطقة للزبون")


async def get_rep_scope(db: AsyncSession, rep_id: int) -> CustomerScope:
    """The customer scope of one specific representative: their assigned areas
    (expanded to descendants) plus the customers assigned to them directly —
    the same rule a rep's own login is restricted to."""
    area_ids = await _areas_for_reps(db, [rep_id])
    return CustomerScope(unrestricted=False, area_ids=area_ids, rep_ids=[rep_id])


async def get_scoped_pharmacy_list(db: AsyncSession, scope: "CustomerScope") -> list[str]:
    """The registered names (as written, cleaned) of every pharmacy visible under this scope —
    all of them when the scope is unrestricted. The display twin of get_scoped_pharmacy_names."""
    query = select(Pharmacies.name).where(Pharmacies.name.isnot(None))
    if not scope.unrestricted:
        if not scope.area_ids and not scope.rep_ids:
            return []
        query = query.where(scope.customer_filter(Pharmacies))
    seen: dict[str, str] = {}
    for (name,) in (await db.execute(query)).all():
        cleaned = clean_name(name)
        if cleaned:
            seen.setdefault(normalize_name(cleaned), cleaned)
    return sorted(seen.values())


async def get_scoped_pharmacy_names(db: AsyncSession, scope: "CustomerScope") -> Optional[set[str]]:
    """Resolve the pharmacy names (trimmed, lowercased, spaces collapsed) visible under this
    scope, for matching against free-text fields that store a customer name
    instead of a real pharmacy_id (e.g. Debts.customer_name). Returns None
    when the scope is unrestricted (no filtering needed) — a distinct value
    from an empty set, which means "restricted, but zero pharmacies in
    scope" (show nothing)."""
    if scope.unrestricted:
        return None
    if not scope.area_ids and not scope.rep_ids:
        return set()

    result = await db.execute(
        select(Pharmacies.name).where(scope.customer_filter(Pharmacies))
    )
    return {normalize_name(row[0]) for row in result.all() if row[0]}
