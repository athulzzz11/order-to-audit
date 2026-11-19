"""Cost per packet.

Materials: actual quantities issued to the run (including wastage) at moving
weighted-average cost, as stored on each ledger movement.

Overheads:
  * an overhead entered against a run goes 100% to that run;
  * an overhead entered for the month (no run) is shared across that month's
    completed runs in proportion to their good packets.
Cost per packet = (materials + overheads) / good packets. Wastage therefore
raises the cost of the packets that survived, which is what the owner pays.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Overhead, ProductionRun
from app.money import round_paise
from app.services.common import BusinessError, audit


def month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return start, date.fromordinal(nxt.toordinal() - 1)


def add_overhead(db: Session, user, category: str, amount_paise: int, run: ProductionRun | None = None,
                 on: date | None = None, note: str | None = None) -> Overhead:
    if amount_paise <= 0:
        raise BusinessError("Overhead amount must be positive")
    if category not in ("labour", "gas_power", "other"):
        raise BusinessError("Category must be labour, gas_power or other")
    oh = Overhead(date=on or (run.date if run else date.today()), category=category, amount_paise=amount_paise,
                  run_id=run.id if run else None, note=note, user_id=getattr(user, "id", None))
    db.add(oh)
    db.flush()
    audit(db, user, "overhead_add", "overhead", oh.id, ref=run.number if run else None, category=category,
          amount_paise=amount_paise)
    return oh


def _month_pool_share(db: Session, run: ProductionRun) -> int:
    start, end = month_bounds(run.date)
    pool = db.scalar(select(func.coalesce(func.sum(Overhead.amount_paise), 0))
                     .where(Overhead.run_id.is_(None), Overhead.date.between(start, end)))
    if not pool or not run.good_output:
        return 0
    total_good = db.scalar(select(func.coalesce(func.sum(ProductionRun.good_output), 0))
                           .where(ProductionRun.status == "completed", ProductionRun.date.between(start, end)))
    return round_paise(Decimal(pool) * run.good_output / total_good) if total_good else 0


def run_cost(db: Session, run: ProductionRun) -> dict:
    raw = sum(l.value_paise for l in run.lines if l.item.kind == "raw")
    pack = sum(l.value_paise for l in run.lines if l.item.kind == "packaging")
    direct = db.scalars(select(Overhead).where(Overhead.run_id == run.id)).all()
    by_cat = {}
    for o in direct:
        by_cat[o.category] = by_cat.get(o.category, 0) + o.amount_paise
    shared = _month_pool_share(db, run)
    overhead = sum(by_cat.values()) + shared
    total = raw + pack + overhead
    good = run.good_output or 0
    per = (Decimal(total) / good / 100).quantize(Decimal("0.01")) if good else None
    material_per = (Decimal(raw + pack) / good / 100).quantize(Decimal("0.01")) if good else None
    price = (Decimal(run.product.selling_price_paise) / 100).quantize(Decimal("0.01"))
    margin = (price - per) if per is not None else None
    return {
        "run": run, "raw_paise": raw, "packaging_paise": pack, "overhead_by_category": by_cat,
        "overhead_shared_paise": shared, "overhead_paise": overhead, "total_paise": total, "good_output": good,
        "material_per_packet": material_per, "cost_per_packet": per, "selling_price": price, "margin": margin,
        "margin_pct": (margin * 100 / price).quantize(Decimal("0.1")) if margin is not None and price else None,
    }


def product_costs(db: Session, start: date, end: date) -> list[dict]:
    """Weighted cost per packet per product across all runs in the period."""
    runs = db.scalars(select(ProductionRun).where(ProductionRun.status == "completed",
                                                  ProductionRun.date.between(start, end))).all()
    agg: dict[int, dict] = {}
    for r in runs:
        c = run_cost(db, r)
        a = agg.setdefault(r.product_id, {"product": r.product, "runs": 0, "good": 0, "material": 0,
                                          "overhead": 0, "total": 0})
        a["runs"] += 1
        a["good"] += c["good_output"]
        a["material"] += c["raw_paise"] + c["packaging_paise"]
        a["overhead"] += c["overhead_paise"]
        a["total"] += c["total_paise"]
    out = []
    for a in agg.values():
        per = (Decimal(a["total"]) / a["good"] / 100).quantize(Decimal("0.01")) if a["good"] else None
        price = (Decimal(a["product"].selling_price_paise) / 100).quantize(Decimal("0.01"))
        a.update(cost_per_packet=per, selling_price=price,
                 margin=(price - per) if per is not None else None,
                 margin_pct=((price - per) * 100 / price).quantize(Decimal("0.1")) if per is not None and price else None)
        out.append(a)
    return out
