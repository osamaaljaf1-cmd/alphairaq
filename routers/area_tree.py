"""الفروع والمناطق: the main branch, the branches (Erbil, Baghdad), their governorates and areas as one
tree, and the price list of every level (inherited by the levels under it unless set).

- GET  /api/v1/area-tree                 the whole tree with each node's own and effective price list,
                                         its branch, and how many customers / reps it has
- POST /api/v1/area-tree/setup           (admin) create the main branch, the two branches, the
                                         governorates and the two price lists, and put the existing
                                         top-level areas under Baghdad. Safe to run again.
- POST /api/v1/area-tree                 add a node {name, parent_id, kind, price_list_id}
- PUT  /api/v1/area-tree/{area_id}       rename / move / change kind / change price list
- POST /api/v1/area-tree/price-lists     add a price list; PUT …/price-lists/{id} rename it

Who sees what is not changed here (next step): this only builds the tree.
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.area_tree import Area_nodes, Price_lists
from models.areas import Areas
from models.pharmacies import Pharmacies
from models.representative_areas import Representative_areas
from schemas.auth import UserResponse
from services.access import require_admin, require_view_any
from services.customer_names import clean_name, normalize_name
from services.permission_check import require_permission, resolve_user_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/area-tree", tags=["area_tree"])

KINDS = ("hq", "branch", "governorate", "area")
RANK = {"hq": 0, "branch": 1, "governorate": 2, "area": 3}
KIND_LABELS = {"hq": "الفرع الرئيسي", "branch": "فرع", "governorate": "محافظة", "area": "منطقة"}

ERBIL_GOVERNORATES = ["أربيل", "السليمانية", "دهوك"]
BAGHDAD_GOVERNORATES = [
    "بغداد", "البصرة", "نينوى", "كركوك", "ديالى", "الأنبار", "بابل", "كربلاء", "النجف", "واسط",
    "صلاح الدين", "ذي قار", "ميسان", "المثنى", "القادسية",
]


class NodeBody(BaseModel):
    name: str
    parent_id: Optional[int] = None
    kind: str = "area"
    price_list_id: Optional[int] = None


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    move_to_top: bool = False  # parent_id None is "unchanged"; this makes it a top-level node
    kind: Optional[str] = None
    price_list_id: Optional[int] = None
    inherit_price: bool = False  # clear the node's own price list (use the level above)


class PriceListBody(BaseModel):
    name: str


async def _require_areas(db: AsyncSession, current_user: UserResponse, action: str) -> None:
    """The admin always may; anyone else needs the action on the areas page."""
    if await resolve_user_role(db, current_user) == "admin":
        return
    await require_permission(db, current_user, "areas", action)


async def _load(db: AsyncSession):
    areas = {a.id: a for a in (await db.execute(select(Areas))).scalars().all()}
    nodes = {n.area_id: n for n in (await db.execute(select(Area_nodes))).scalars().all()}
    lists = {p.id: p for p in (await db.execute(select(Price_lists).order_by(Price_lists.sort, Price_lists.id))).scalars().all()}
    return areas, nodes, lists


def _kind(nodes: Dict[int, Area_nodes], area_id: int) -> str:
    n = nodes.get(area_id)
    return n.kind if n and n.kind in RANK else "area"


def _check_place(areas, nodes, area_id: Optional[int], kind: str, parent_id: Optional[int]) -> None:
    """The main branch is at the top; a branch under it; a governorate under a branch; an area under a
    governorate or another area. No node may move under itself."""
    if kind not in RANK:
        raise HTTPException(status_code=400, detail="نوع غير صحيح")
    if parent_id is None:
        if kind not in ("hq", "area"):
            raise HTTPException(status_code=400, detail=f"«{KIND_LABELS[kind]}» يجب أن يكون تحت مستوى أعلى")
        return
    if parent_id not in areas:
        raise HTTPException(status_code=404, detail="المستوى الأعلى غير موجود")
    if kind == "hq":
        raise HTTPException(status_code=400, detail="الفرع الرئيسي يكون في الأعلى")
    parent_kind = _kind(nodes, parent_id)
    allowed = {"branch": ("hq",), "governorate": ("branch",), "area": ("governorate", "area", "branch")}[kind]
    if parent_kind not in allowed:
        raise HTTPException(status_code=400, detail=f"لا يمكن وضع «{KIND_LABELS[kind]}» تحت «{KIND_LABELS[parent_kind]}»")
    # no cycles
    seen = set()
    cur = parent_id
    while cur is not None and cur not in seen:
        if area_id is not None and cur == area_id:
            raise HTTPException(status_code=400, detail="لا يمكن نقل المستوى تحت نفسه")
        seen.add(cur)
        cur = areas[cur].parent_area_id if cur in areas else None


@router.get("")
async def get_tree(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_view_any(db, current_user, ["areas", "rep_areas", "permissions", "customers"])
    areas, nodes, lists = await _load(db)
    customers = dict((await db.execute(select(Pharmacies.area_id, func.count(Pharmacies.id)).where(Pharmacies.area_id.isnot(None)).group_by(Pharmacies.area_id))).all())
    reps = dict((await db.execute(select(Representative_areas.area_id, func.count(Representative_areas.id)).group_by(Representative_areas.area_id))).all())

    def effective(area_id: int):
        """(price list id, where it comes from: the node's own id), branch (id, name) — walking up."""
        price, source, branch = None, None, None
        seen = set()
        cur = area_id
        while cur is not None and cur in areas and cur not in seen:
            seen.add(cur)
            n = nodes.get(cur)
            if price is None and n and n.price_list_id:
                price, source = n.price_list_id, cur
            if branch is None and n and n.kind == "branch":
                branch = cur
            cur = areas[cur].parent_area_id
        return price, source, branch

    out = []
    for a in sorted(areas.values(), key=lambda x: (RANK.get(_kind(nodes, x.id), 3), x.name)):
        n = nodes.get(a.id)
        price, source, branch = effective(a.id)
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "parent_id": a.parent_area_id,
                "kind": _kind(nodes, a.id),
                "price_list_id": n.price_list_id if n else None,
                "effective_price_list_id": price,
                "price_inherited": price is not None and source != a.id,
                "branch_id": branch,
                "branch_name": areas[branch].name if branch in areas else None,
                "phenix_branch": n.phenix_branch if n else None,
                "customers": int(customers.get(a.id, 0)),
                "reps": int(reps.get(a.id, 0)),
            }
        )
    return {
        "nodes": out,
        "price_lists": [{"id": p.id, "name": p.name, "code": p.code} for p in lists.values()],
        "has_tree": any(n.kind == "hq" for n in nodes.values()),
    }


async def _price_list(db: AsyncSession, code: str, name: str, sort: int) -> Price_lists:
    p = (await db.execute(select(Price_lists).where(Price_lists.code == code))).scalar_one_or_none()
    if p is None:
        p = Price_lists(code=code, name=name, sort=sort)
        db.add(p)
        await db.flush()
    return p


async def _node(db: AsyncSession, areas: Dict[int, Areas], nodes: Dict[int, Area_nodes], name: str, parent_id: Optional[int], kind: str,
                price_list_id: Optional[int] = None, phenix_branch: Optional[int] = None) -> int:
    """The node with this name under this parent (or, for a top-level search, any top-level area with
    this name: an existing area named like a governorate becomes that governorate), else a new one."""
    key = normalize_name(name)
    match = next((a for a in areas.values() if normalize_name(a.name) == key and a.parent_area_id == parent_id), None)
    if match is None and parent_id is not None:
        match = next((a for a in areas.values() if normalize_name(a.name) == key and a.parent_area_id is None and a.id not in nodes), None)
    if match is None:
        match = Areas(name=name, parent_area_id=parent_id)
        db.add(match)
        await db.flush()
        areas[match.id] = match
    else:
        match.parent_area_id = parent_id
    n = nodes.get(match.id)
    if n is None:
        n = Area_nodes(area_id=match.id, kind=kind)
        db.add(n)
        nodes[match.id] = n
    n.kind = kind
    if price_list_id is not None and n.price_list_id is None:
        n.price_list_id = price_list_id
    if phenix_branch is not None:
        n.phenix_branch = phenix_branch
    await db.flush()
    return match.id


@router.post("/setup")
async def setup_tree(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    """Build الفرع الرئيسي → فرع أربيل / فرع بغداد → governorates, with the two price lists, and move the
    areas that are not in the tree yet under محافظة بغداد. Running it again changes nothing else."""
    await require_admin(db, current_user, strict=True)
    areas, nodes, _ = await _load(db)
    before = {a_id for a_id, a in areas.items()}
    baghdad_price = await _price_list(db, "baghdad", "سعر بغداد", 1)
    erbil_price = await _price_list(db, "erbil", "سعر أربيل", 2)
    hq = await _node(db, areas, nodes, "الفرع الرئيسي", None, "hq")
    erbil = await _node(db, areas, nodes, "فرع أربيل", hq, "branch", erbil_price.id, 2)
    baghdad = await _node(db, areas, nodes, "فرع بغداد", hq, "branch", baghdad_price.id, 1)
    gov_ids = {}
    for g in ERBIL_GOVERNORATES:
        gov_ids[g] = await _node(db, areas, nodes, g, erbil, "governorate")
    for g in BAGHDAD_GOVERNORATES:
        gov_ids[g] = await _node(db, areas, nodes, g, baghdad, "governorate")
    baghdad_gov = gov_ids["بغداد"]
    tree_ids = {hq, erbil, baghdad, *gov_ids.values()}
    moved = 0
    for a in list(areas.values()):
        if a.id in tree_ids or a.parent_area_id is not None or a.id in nodes:
            continue
        a.parent_area_id = baghdad_gov
        db.add(Area_nodes(area_id=a.id, kind="area"))
        nodes[a.id] = True  # placeholder, not used further
        moved += 1
    await db.commit()
    created = len([i for i in tree_ids if i not in before])
    logger.info("Area tree set up: %s new nodes, %s areas moved under Baghdad", created, moved)
    return {"ok": True, "created": created, "moved_to_baghdad": moved}


@router.post("")
async def add_node(data: NodeBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await _require_areas(db, current_user, "add")
    name = clean_name(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="الاسم مطلوب")
    areas, nodes, lists = await _load(db)
    _check_place(areas, nodes, None, data.kind, data.parent_id)
    if data.price_list_id is not None and data.price_list_id not in lists:
        raise HTTPException(status_code=400, detail="قائمة الأسعار غير موجودة")
    if any(normalize_name(a.name) == normalize_name(name) and a.parent_area_id == data.parent_id for a in areas.values()):
        raise HTTPException(status_code=409, detail="يوجد مستوى بنفس الاسم في نفس المكان")
    a = Areas(name=name, parent_area_id=data.parent_id)
    db.add(a)
    await db.flush()
    db.add(Area_nodes(area_id=a.id, kind=data.kind, price_list_id=data.price_list_id))
    await db.commit()
    return {"id": a.id}


@router.put("/{area_id}")
async def update_node(area_id: int, data: NodeUpdate, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await _require_areas(db, current_user, "edit")
    areas, nodes, lists = await _load(db)
    a = areas.get(area_id)
    if a is None:
        raise HTTPException(status_code=404, detail="غير موجود")
    kind = data.kind or _kind(nodes, area_id)
    parent_id = None if data.move_to_top else (data.parent_id if data.parent_id is not None else a.parent_area_id)
    _check_place(areas, nodes, area_id, kind, parent_id)
    # the children must still fit under the new kind
    for child in areas.values():
        if child.parent_area_id == area_id:
            ck = _kind(nodes, child.id)
            allowed = {"branch": ("hq",), "governorate": ("branch",), "area": ("governorate", "area", "branch")}.get(ck, ())
            if ck == "hq" or kind not in allowed:
                raise HTTPException(status_code=400, detail=f"تحته «{child.name}» ({KIND_LABELS[ck]}) لا يناسب النوع الجديد")
    if data.name is not None:
        name = clean_name(data.name)
        if not name:
            raise HTTPException(status_code=400, detail="الاسم مطلوب")
        a.name = name
    a.parent_area_id = parent_id
    n = nodes.get(area_id)
    if n is None:
        n = Area_nodes(area_id=area_id, kind=kind)
        db.add(n)
    n.kind = kind
    if data.inherit_price:
        n.price_list_id = None
    elif data.price_list_id is not None:
        if data.price_list_id not in lists:
            raise HTTPException(status_code=400, detail="قائمة الأسعار غير موجودة")
        n.price_list_id = data.price_list_id
    await db.commit()
    return {"ok": True}


@router.post("/price-lists")
async def add_price_list(data: PriceListBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    name = clean_name(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="الاسم مطلوب")
    p = Price_lists(name=name, sort=100)
    db.add(p)
    await db.commit()
    return {"id": p.id, "name": p.name}


@router.put("/price-lists/{list_id}")
async def rename_price_list(list_id: int, data: PriceListBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    p = (await db.execute(select(Price_lists).where(Price_lists.id == list_id))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="غير موجود")
    name = clean_name(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="الاسم مطلوب")
    p.name = name
    await db.commit()
    return {"ok": True}
