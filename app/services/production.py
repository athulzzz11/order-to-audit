"""Production: BOM-driven runs, automatic stock posting and variance.

A run has two steps, matching the floor:
  1. start    - ingredients are issued (raw stock goes down)
  2. complete - packaging used + good output recorded (FG stock goes up)

Planned consumption comes from the BOM:
  basis='batch'  -> qty_per_batch x batches               (flour, oil, spice)
  basis='output' -> qty_per_batch x good_output / yield   (pouches, cartons)
For count items on 'output' basis the plan is rounded down to whole pieces:
188 packets need 9 full cartons (8 packets stay loose), not 9.4 cartons.
"""
import math
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bom, Item, ProductionLine, ProductionRun
from app.services import stock
from app.services.common import BusinessError, audit, next_number, setting
from app.units import to_base


def active_bom(db: Session, product: Item) -> Bom:
    bom = db.scalar(select(Bom).where(Bom.product_id == product.id, Bom.active.is_(True))
                    .order_by(Bom.version.desc()))
    if not bom:
        raise BusinessError(f"{product.name} has no active BOM")
    return bom


def _batches_milli(batches) -> int:
    b = Decimal(str(batches)) * 1000
    if b <= 0 or b != b.to_integral_value():
        raise BusinessError("Batches must be positive, up to 3 decimals")
    return int(b)


def planned_qty(line, batches_milli: int, good_output: int | None, batch_output: int) -> Decimal | None:
    if line.basis == "batch":
        return Decimal(line.qty_per_batch) * batches_milli / 1000
    if good_output is None:
        return None  # packaging plan is known only once output is counted
    p = Decimal(line.qty_per_batch) * good_output / batch_output
    if line.item.dimension == "count":
        p = Decimal(math.floor(p))
    return p


def standard_issue(db: Session, product: Item, batches=1, stage="start") -> list[dict]:
    """Pre-fill for the floor screen: what the BOM says to issue."""
    bom = active_bom(db, product)
    bm = _batches_milli(batches)
    expected = bom.batch_output * bm // 1000
    rows = []
    for ln in bom.lines:
        if (stage == "start") != (ln.basis == "batch"):
            continue
        p = planned_qty(ln, bm, expected, bom.batch_output)
        rows.append({"item": ln.item, "qty_base": int(p), "basis": ln.basis})
    return rows


def _issue(db, user, run, rows, stage, on):
    for r in rows:
        item = r["item"] if isinstance(r["item"], Item) else db.get(Item, r["item"])
        unit = r.get("unit") or item.display_unit
        used = to_base(item, r.get("qty") or 0, unit)
        wasted = to_base(item, r.get("wasted") or 0, unit)
        if used < 0 or wasted < 0:
            raise BusinessError("Quantities cannot be negative")
        total = used + wasted
        if total == 0:
            continue
        if item.kind == "finished":
            raise BusinessError("Finished goods cannot be issued to production")
        m = stock.post(db, item=item, qty=-total, kind="production_issue", source_type="production",
                       source_id=run.id, source_ref=run.number, on=on, user=user)
        run.lines.append(ProductionLine(item_id=item.id, item=item, used=used, wasted=wasted,
                                        value_paise=-m.value_paise, stage=stage))


def start_run(db: Session, user, product: Item, batches, issues: list[dict], on: date | None = None) -> ProductionRun:
    """issues: [{'item': Item|id, 'qty': '40', 'unit': 'kg', 'wasted': '0'}]"""
    on = on or date.today()
    bom = active_bom(db, product)
    bm = _batches_milli(batches)
    run = ProductionRun(number=next_number(db, "PR", on), date=on, product_id=product.id, product=product,
                        bom_id=bom.id, bom=bom, batches_milli=bm, expected_output=bom.batch_output * bm // 1000,
                        status="in_progress", started_by=getattr(user, "id", None))
    db.add(run)
    db.flush()
    _issue(db, user, run, issues, "start", on)
    if not run.lines:
        raise BusinessError("Issue at least one material to start a run")
    audit(db, user, "run_start", "production_run", run.id, ref=run.number, batches=str(Decimal(bm) / 1000),
          issues=[{"item": l.item.code, "used": l.used, "wasted": l.wasted} for l in run.lines])
    return run


def complete_run(db: Session, user, run: ProductionRun, good_output: int, issues: list[dict],
                 scrap_note: str | None = None, on: date | None = None) -> ProductionRun:
    """Record packaging used and good packets; FG stock goes up at the run's material cost."""
    if run.status != "in_progress":
        raise BusinessError(f"Run {run.number} is {run.status}")
    good_output = int(good_output)
    if good_output < 0:
        raise BusinessError("Output cannot be negative")
    on = on or date.today()
    _issue(db, user, run, issues, "complete", on)
    run.good_output = good_output
    run.scrap_note = scrap_note
    product = run.product
    seq = next_number(db, f"BATCH-{product.batch_code or product.code}-{on:%y%m%d}", on, width=2).split("/")[-1]
    run.batch_no = f"{product.batch_code or product.code}-{on:%y%m%d}-{seq}"
    run.expiry = on + timedelta(days=product.shelf_life_days or 90)
    material = sum(l.value_paise for l in run.lines)
    if good_output > 0:
        stock.post(db, item=product, qty=good_output, kind="production_output", source_type="production",
                   source_id=run.id, source_ref=run.number, on=on, value_paise=material,
                   batch_no=run.batch_no, expiry=run.expiry, user=user)
    run.status = "completed"
    run.completed_by = getattr(user, "id", None)
    from app.models import now
    run.completed_at = now()
    audit(db, user, "run_complete", "production_run", run.id, ref=run.number, good_output=good_output,
          batch=run.batch_no, packaging=[{"item": l.item.code, "used": l.used, "wasted": l.wasted}
                                         for l in run.lines if l.stage == "complete"])
    return run


def cancel_run(db: Session, user, run: ProductionRun, reason: str) -> ProductionRun:
    """Reverse every movement of a run (e.g. wrong entry). Needs the output still in stock."""
    if run.status == "cancelled":
        raise BusinessError("Already cancelled")
    if not reason:
        raise BusinessError("Give a reason")
    from app.models import StockMovement
    moves = db.scalars(select(StockMovement).where(StockMovement.source_type == "production",
                                                   StockMovement.source_id == run.id)).all()
    # reverse output first so the stock check on FG runs before materials come back
    for m in sorted(moves, key=lambda m: -m.qty):
        stock.post(db, item=m.item, qty=-m.qty, value_paise=-m.value_paise, kind="adjustment",
                   source_type="production", source_id=run.id, source_ref=f"{run.number} (reversal)",
                   batch_no=m.batch_no, expiry=m.expiry, user=user, note=reason)
    run.status = "cancelled"
    audit(db, user, "run_cancel", "production_run", run.id, ref=run.number, reason=reason)
    return run


def variance(db: Session, run: ProductionRun) -> dict:
    """Planned vs actual consumption and yield, flagged beyond the tolerance."""
    tol = Decimal(setting(db, "variance_tolerance_bp")) / 100  # percent
    bom = run.bom
    actual: dict[int, dict] = {}
    for l in run.lines:
        a = actual.setdefault(l.item_id, {"item": l.item, "used": 0, "wasted": 0, "value": 0})
        a["used"] += l.used
        a["wasted"] += l.wasted
        a["value"] += l.value_paise
    lines = []
    seen = set()
    for bl in bom.lines:
        seen.add(bl.item_id)
        planned = planned_qty(bl, run.batches_milli, run.good_output, bom.batch_output)
        a = actual.get(bl.item_id, {"item": bl.item, "used": 0, "wasted": 0, "value": 0})
        lines.append(_vline(bl.item, planned, a, tol, bl.basis))
    for item_id, a in actual.items():
        if item_id not in seen:
            lines.append(_vline(a["item"], Decimal(0), a, tol, "extra"))
    y = None
    if run.good_output is not None:
        exp = run.expected_output
        pct = (Decimal(run.good_output - exp) * 100 / exp) if exp else Decimal(0)
        y = {"expected": exp, "actual": run.good_output, "diff": run.good_output - exp,
             "pct": pct.quantize(Decimal("0.01")), "flagged": abs(pct) > tol}
    flagged = any(l["flagged"] for l in lines) or bool(y and y["flagged"])
    return {"lines": lines, "yield": y, "tolerance_pct": tol, "flagged": flagged}


def _vline(item, planned, a, tol, basis):
    act = a["used"] + a["wasted"]
    if planned is None:
        return {"item": item, "basis": basis, "planned": None, "actual": act, "used": a["used"],
                "wasted": a["wasted"], "diff": None, "pct": None, "flagged": False, "value": a["value"]}
    diff = Decimal(act) - planned
    if planned:
        pct = (diff * 100 / planned).quantize(Decimal("0.01"))
    else:
        pct = Decimal(100) if act else Decimal(0)
    return {"item": item, "basis": basis, "planned": planned, "actual": act, "used": a["used"],
            "wasted": a["wasted"], "diff": diff, "pct": pct, "flagged": abs(pct) > tol, "value": a["value"]}


def batches_needed(db: Session, product: Item, packets: int) -> int:
    """'To make 32 more packets you need 1 batch.'"""
    bom = active_bom(db, product)
    return math.ceil(packets / bom.batch_output) if packets > 0 else 0
