"""The stock ledger.

There is no `stock` column anywhere. Every balance is computed from
StockMovement rows, and every row points at the document that caused it.

Valuation: moving weighted average. When stock comes in, its value is added
to the pool; when it goes out, it leaves at (pool value / pool qty). Each
movement stores its own value in integer paise, so SUM(value_paise) is always
the exact stock value and never drifts.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Item, StockMovement
from app.money import round_paise
from app.units import fmt_qty
from app.services.common import BusinessError, audit


class InsufficientStock(BusinessError):
    def __init__(self, item, needed: int, available: int):
        self.item, self.needed, self.available = item, needed, available
        self.shortage = needed - available
        super().__init__(f"Not enough {item.name}: need {fmt_qty(item, needed)}, "
                         f"have {fmt_qty(item, available)} (short by {fmt_qty(item, self.shortage)})")


def _q(item_id, upto=None, batch_no=None):
    conds = [StockMovement.item_id == item_id]
    if upto is not None:
        conds.append(StockMovement.date <= upto)
    if batch_no is not None:
        conds.append(StockMovement.batch_no == batch_no)
    return conds


def balance(db: Session, item_id: int, upto: date | None = None, batch_no: str | None = None) -> int:
    return db.scalar(select(func.coalesce(func.sum(StockMovement.qty), 0)).where(*_q(item_id, upto, batch_no)))


def value(db: Session, item_id: int, upto: date | None = None) -> int:
    return db.scalar(select(func.coalesce(func.sum(StockMovement.value_paise), 0)).where(*_q(item_id, upto)))


def avg_cost(db: Session, item_id: int) -> Decimal:
    """Weighted average cost in paise per base unit (0 if no stock)."""
    q, v = balance(db, item_id), value(db, item_id)
    return Decimal(v) / Decimal(q) if q > 0 else Decimal(0)


def batches(db: Session, item_id: int) -> list[dict]:
    """Open batches in first-expiry-first-out order."""
    rows = db.execute(
        select(StockMovement.batch_no, StockMovement.expiry, func.sum(StockMovement.qty))
        .where(StockMovement.item_id == item_id)
        .group_by(StockMovement.batch_no, StockMovement.expiry)
    ).all()
    out = [{"batch_no": b, "expiry": e, "qty": q} for b, e, q in rows if q and q > 0]
    out.sort(key=lambda r: (r["expiry"] or date.max, r["batch_no"] or ""))
    return out


def post(db: Session, *, item: Item, qty: int, kind: str, source_type: str, source_id=None, source_ref=None,
         on: date | None = None, value_paise: int | None = None, batch_no=None, expiry=None, user=None,
         note=None) -> StockMovement:
    """Write one movement. Receipts must carry their value; issues are valued at average cost."""
    if qty == 0:
        raise BusinessError("Zero-quantity stock movement")
    # Serialise movements per item (row lock on Postgres; SQLite locks the whole DB on write anyway).
    db.execute(select(Item.id).where(Item.id == item.id).with_for_update())
    if qty < 0:
        have = balance(db, item.id, batch_no=batch_no) if batch_no else balance(db, item.id)
        if have + qty < 0:
            raise InsufficientStock(item, -qty, have)
        if value_paise is None:
            pool_q, pool_v = balance(db, item.id), value(db, item.id)
            if -qty == pool_q:
                value_paise = -pool_v  # last unit out takes the exact remaining value: no paise left behind
            else:
                value_paise = -round_paise(Decimal(pool_v) * Decimal(-qty) / Decimal(pool_q))
    elif value_paise is None:
        raise BusinessError("Stock receipts must carry a value")
    m = StockMovement(date=on or date.today(), item_id=item.id, qty=qty, value_paise=value_paise, kind=kind,
                      source_type=source_type, source_id=source_id, source_ref=source_ref, batch_no=batch_no,
                      expiry=expiry, user_id=getattr(user, "id", None), note=note)
    db.add(m)
    db.flush()
    return m


def issue_fefo(db: Session, *, item: Item, qty: int, **kw) -> list[StockMovement]:
    """Take `qty` out of a batch-tracked item, earliest expiry first."""
    have = balance(db, item.id)
    if have < qty:
        raise InsufficientStock(item, qty, have)
    left, out = qty, []
    for b in batches(db, item.id):
        take = min(left, b["qty"])
        out.append(post(db, item=item, qty=-take, batch_no=b["batch_no"], expiry=b["expiry"], **kw))
        left -= take
        if left == 0:
            break
    return out


def adjust(db: Session, *, user, item: Item, qty: int, reason: str, on: date | None = None,
           rate_paise_per_base: Decimal | None = None) -> StockMovement:
    """Physical-count correction. Gains are valued at current average cost (or a given rate)."""
    if not reason:
        raise BusinessError("An adjustment needs a reason")
    val = None
    if qty > 0:
        rate = rate_paise_per_base if rate_paise_per_base is not None else avg_cost(db, item.id)
        val = round_paise(rate * qty)
    batch = None
    if item.tracks_batches:
        open_b = batches(db, item.id)
        batch = open_b[0]["batch_no"] if open_b else "ADJ"
    m = post(db, item=item, qty=qty, kind="adjustment", source_type="adjustment", on=on, value_paise=val,
             batch_no=batch, user=user, note=reason)
    m.source_id = m.id
    m.source_ref = f"ADJ-{m.id}"
    audit(db, user, "stock_adjust", "item", item.id, ref=m.source_ref, qty=qty, reason=reason)
    return m
