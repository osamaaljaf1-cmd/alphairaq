"""Customer-name handling shared by debts, payments, collections and the
customer list.

Debts and payments link to a customer by its name (not its id), so a name must
be written exactly like the registered customer. Names are compared after
whitespace clean-up only (case, leading/trailing spaces, repeated inner
spaces); the type word (صيدلية / مذخر) is part of the customer's identity and
is never ignored. A name that is merely *similar* to a registered customer is
not accepted — callers reject it and point at the registered spelling.
"""
import re
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pharmacies import Pharmacies

_SPACES_RE = re.compile(r"\s+")
_TYPE_WORDS = {"صيدلية": "صيدلية", "صيدليه": "صيدلية", "مذخر": "مذخر"}


def clean_name(value: Optional[str]) -> str:
    """The name as it should be stored: no leading/trailing spaces and every
    run of inner whitespace collapsed to a single space."""
    return _SPACES_RE.sub(" ", (value or "").strip())


def normalize_name(value: Optional[str]) -> str:
    """clean_name lowercased — the key used to compare two names."""
    return clean_name(value).lower()


def name_sql(column):
    """SQL twin of normalize_name (lower + trim + up to 8 inner spaces
    collapsed) for comparing a name column against normalized names."""
    spaced = func.replace(func.replace(func.replace(column, "  ", " "), "  ", " "), "  ", " ")
    return func.lower(func.trim(spaced))


def _compact(value: Optional[str]) -> str:
    return normalize_name(value).replace(" ", "")


def _split_type(value: Optional[str]) -> tuple[Optional[str], str]:
    """('صيدلية' | 'مذخر' | None, the rest of the name without any space)."""
    norm = normalize_name(value)
    first, _, rest = norm.partition(" ")
    kind = _TYPE_WORDS.get(first)
    if kind and rest:
        return kind, rest.replace(" ", "")
    return None, norm.replace(" ", "")


def are_similar(a: Optional[str], b: Optional[str]) -> bool:
    """Two names that would be mistaken for one customer: identical apart from
    spaces/case, or identical once a type word is added to / dropped from only
    one of them ("نور" vs "صيدلية نور"). "صيدلية نور" and "مذخر نور" are two
    different customers and are not similar."""
    if _compact(a) and _compact(a) == _compact(b):
        return True
    ta, ka = _split_type(a)
    tb, kb = _split_type(b)
    if not ka or ka != kb:
        return False
    return ta == tb or ta is None or tb is None


class PharmacyIndex:
    """The customer list indexed for lookups (an import checks hundreds of
    names against thousands of customers)."""

    def __init__(self, names: Iterable[str]):
        self.exact: dict[str, str] = {}
        self._by_rest: dict[str, list[str]] = {}
        self._by_compact: dict[str, list[str]] = {}
        for raw in names:
            self.add(raw)

    def add(self, raw: Optional[str]) -> None:
        if not raw or not clean_name(raw):
            return
        registered = clean_name(raw)
        self.exact.setdefault(normalize_name(raw), registered)
        self._by_rest.setdefault(_split_type(raw)[1], []).append(registered)
        self._by_compact.setdefault(_compact(raw), []).append(registered)

    def registered(self, name: Optional[str]) -> Optional[str]:
        """The registered spelling when the name matches a customer exactly
        (ignoring case and spacing), else None."""
        return self.exact.get(normalize_name(name))

    def similar(self, name: Optional[str]) -> list[str]:
        """Registered customers similar to this name (see are_similar)."""
        rest = _split_type(name)[1]
        if not rest:
            return []
        candidates = self._by_rest.get(rest, []) + self._by_compact.get(_compact(name), [])
        return list(dict.fromkeys(n for n in candidates if are_similar(name, n)))


async def pharmacy_names(db: AsyncSession) -> list[str]:
    result = await db.execute(select(Pharmacies.name))
    return [row[0] for row in result.all() if row[0]]


async def pharmacy_index(db: AsyncSession) -> PharmacyIndex:
    return PharmacyIndex(await pharmacy_names(db))
