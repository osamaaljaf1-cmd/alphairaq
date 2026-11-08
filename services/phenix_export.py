"""Read the debts export of the accounting program (Phenix) that the accountant uploads.

The same rules as tools/phenix_sync/phenix_sync.py (which reads the file from disk on the
accountant's PC): the export is the FULL list of open invoices with the columns رقم السند, النوع,
العميل, التاريخ, القيمة, المسدد, الباقي. The program writes a blank "#" header, so the data may
sit one column to the left of its header: the offset is detected, not assumed. A wrong file (other
report, shifted columns, amounts that do not add up) is refused with the reason, before anything is
read into the debts table.
"""
import datetime as dt
import io
import re
from typing import List, Tuple

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_ROWS = 20000

NEEDED = {
    "invoice": "رقم السند",
    "kind": "النوع",
    "customer": "العميل",
    "date": "التاريخ",
    "amount": "القيمة",
    "paid": "المسدد",
    "remaining": "الباقي",
}


class ExportError(Exception):
    """The file is not the expected export; the message says why (shown to the accountant)."""


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _date(value) -> str:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")
    text = _text(value)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else text


def read_export(content: bytes) -> Tuple[List[dict], int, dict]:
    """-> (rows, ignored_returns, info). Raises ExportError when the file is not the debts export."""
    if len(content) > MAX_FILE_BYTES:
        raise ExportError("الملف كبير جداً")
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - the server image has openpyxl (pandas' Excel reader)
        raise ExportError("قراءة ملفات Excel غير متاحة على الخادم")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise ExportError("تعذّر فتح الملف: يجب أن يكون ملف Excel بصيغة xlsx كما يصدّره البرنامج")
    try:
        ws = wb.worksheets[0]
        all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    all_rows = [r for r in all_rows if any(c not in (None, "") for c in r)]
    if len(all_rows) < 2:
        raise ExportError("الملف فارغ")

    header = [_text(c) for c in all_rows[0]]
    missing = [h for h in NEEDED.values() if h not in header]
    if missing:
        raise ExportError("الملف ليس تقرير الديون المتوقع، أعمدة مفقودة: " + "، ".join(missing))
    pos = {k: header.index(h) for k, h in NEEDED.items()}
    data = all_rows[1:]
    if len(data) > MAX_ROWS:
        raise ExportError(f"الملف كبير جداً ({len(data)} سطر)")

    offset = None
    for off in (0, 1):  # data column = header column - off
        ok = 0
        sample = data[:30]
        for r in sample:
            try:
                cust = r[pos["customer"] - off]
                rem = r[pos["remaining"] - off]
                inv = r[pos["invoice"] - off]
                amt = r[pos["amount"] - off]
            except IndexError:
                break
            if _num(cust) is None and _text(cust) and _num(rem) is not None and _num(amt) is not None and re.fullmatch(r"\d+", _text(inv) or "x"):
                ok += 1
        if sample and ok == len(sample):
            offset = off
            break
    if offset is None:
        raise ExportError("ترتيب الأعمدة بالملف غير مطابق للمتوقع (اسم الزبون/الباقي/رقم السند). لم يُقرأ شيء.")

    rows, returns, mismatches = [], 0, 0
    for r in data:
        def get(k):
            i = pos[k] - offset
            return r[i] if 0 <= i < len(r) else None

        invoice, customer = _text(get("invoice")), _text(get("customer"))
        amount, remaining, paid = _num(get("amount")), _num(get("remaining")), _num(get("paid"))
        if not invoice and not customer:
            continue
        if amount is None or remaining is None:
            raise ExportError(f"سطر بمبلغ غير مفهوم (رقم السند {invoice or '?'}).")
        if amount <= 0 or "مرتجع" in _text(get("kind")):
            returns += 1
            continue
        if paid is not None and abs(amount - paid - remaining) > 1:
            mismatches += 1
        rows.append(
            {
                "invoice_number": invoice,
                "customer_name": customer,
                "amount": amount,
                "remaining": remaining,
                "invoice_date": _date(get("date")),
            }
        )
    if not rows:
        raise ExportError("لا توجد قوائم دين بالملف")
    if mismatches > max(3, len(rows) * 0.02):
        raise ExportError(f"{mismatches} سطر لا يطابق (القيمة − المسدد = الباقي)؛ الأعمدة غالباً غير صحيحة.")
    info = {
        "rows": len(rows),
        "returns_ignored": returns,
        "customers": len({r["customer_name"] for r in rows}),
        "remaining_total": round(sum(r["remaining"] for r in rows), 2),
    }
    return rows, returns, info
