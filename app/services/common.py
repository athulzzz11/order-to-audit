"""Cross-cutting helpers: document numbering, settings, audit trail, errors."""
import json
from datetime import date

from sqlalchemy.orm import Session

from app.models import AuditLog, Sequence, Setting


class BusinessError(ValueError):
    """A rule was broken (e.g. approving a rejected order). Shown to the user as-is."""


DEFAULT_SETTINGS = {
    "company_name": "Sri Annapoorna Snacks (Demo)",
    "company_address": "12 Industrial Estate, Thuvakudi, Tiruchirappalli, Tamil Nadu 620015",
    "company_gstin": "33AAAFS0000A1Z5",
    "company_state_code": "33",
    "company_state_name": "Tamil Nadu",
    "company_phone": "+91 90000 00000",
    "variance_tolerance_bp": "300",  # 3.00 %
}


def setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row else DEFAULT_SETTINGS[key]


def set_setting(db: Session, key: str, value: str):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def fy_of(d: date) -> str:
    """Indian financial year: 1 Apr - 31 Mar. 18 Nov 2026 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_number(db: Session, prefix: str, on: date, width: int = 4) -> str:
    """Gapless per-financial-year numbering: INV/2026-27/0001.

    The row lock (SELECT ... FOR UPDATE on Postgres) stops two desks grabbing the same number.
    """
    name = f"{prefix}/{fy_of(on)}"
    seq = db.get(Sequence, name, with_for_update=True)
    if seq is None:
        seq = Sequence(name=name, last=0)
        db.add(seq)
    seq.last += 1
    db.flush()
    return f"{name}/{seq.last:0{width}d}"


def audit(db: Session, user, action: str, entity: str, entity_id=None, ref=None, **detail):
    db.add(AuditLog(user_id=getattr(user, "id", None), action=action, entity=entity, entity_id=entity_id,
                    ref=ref, detail=json.dumps(detail, default=str) if detail else None))
