"""Floor screens: production runs, overheads, purchases (raw-material receipts)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Item, Overhead, ProductionRun, Purchase, Supplier
from app.money import to_paise
from app.services import costing, production, purchase, stock
from app.services.common import BusinessError
from app.units import fmt_num, from_base, units_for
from app.web.deps import area, flash, go, render
from app.web.sales_routes import _do

router = APIRouter()


def _lines_from_form(form, prefix=""):
    """Rows posted as item_id / qty / unit / wasted lists."""
    ids, qtys = form.getlist(f"{prefix}item_id"), form.getlist(f"{prefix}qty")
    units, wasted = form.getlist(f"{prefix}unit"), form.getlist(f"{prefix}wasted")
    rows = []
    for i, iid in enumerate(ids):
        if not iid:
            continue
        rows.append({"item": int(iid), "qty": (qtys[i] if i < len(qtys) else "") or "0",
                     "unit": units[i] if i < len(units) else None,
                     "wasted": (wasted[i] if i < len(wasted) else "") or "0"})
    return rows


@router.get("/production")
def runs(request: Request, user=Depends(area("production")), db: Session = Depends(get_db)):
    rows = db.scalars(select(ProductionRun).order_by(ProductionRun.id.desc())).all()
    info = {r.id: {"v": production.variance(db, r), "c": costing.run_cost(db, r) if r.status == "completed" else None}
            for r in rows}
    products = db.scalars(select(Item).where(Item.kind == "finished")).all()
    return render(request, "runs.html", user, runs=rows, info=info, products=products)


@router.get("/production/new")
def run_new(request: Request, product_id: int | None = None, batches: str = "1",
            user=Depends(area("production", True)), db: Session = Depends(get_db)):
    products = db.scalars(select(Item).where(Item.kind == "finished")).all()
    product = db.get(Item, product_id) if product_id else products[0]
    try:
        std = production.standard_issue(db, product, batches, "start")
        bom = production.active_bom(db, product)
    except BusinessError as e:
        flash(request, str(e), "error")
        return go("/production")
    lines = [{"item": r["item"], "qty": fmt_num(from_base(r["item"], r["qty_base"])), "unit": r["item"].display_unit,
              "units": units_for(r["item"]), "on_hand": stock.balance(db, r["item"].id)} for r in std]
    materials = db.scalars(select(Item).where(Item.kind != "finished").order_by(Item.kind, Item.name)).all()
    return render(request, "run_new.html", user, product=product, products=products, batches=batches, lines=lines,
                  bom=bom, materials=materials, expected=bom.batch_output * production._batches_milli(batches) // 1000)


@router.post("/production/start")
async def run_start(request: Request, user=Depends(area("production", True)), db: Session = Depends(get_db)):
    form = await request.form()
    product = db.get(Item, int(form["product_id"]))
    run = _do(request, db, lambda: production.start_run(db, user, product, form.get("batches") or "1",
                                                        _lines_from_form(form)),
              lambda r: f"Run {r.number} started - materials issued, raw stock reduced")
    return go(f"/production/{run.id}" if run else "/production/new")


@router.get("/production/{rid}")
def run_detail(rid: int, request: Request, user=Depends(area("production")), db: Session = Depends(get_db)):
    run = db.get(ProductionRun, rid)
    pack = []
    if run.status == "in_progress":
        for bl in run.bom.lines:
            if bl.basis == "output":
                pack.append({"item": bl.item, "per_packet": bl.qty_per_batch / run.bom.batch_output,
                             "units": units_for(bl.item), "on_hand": stock.balance(db, bl.item_id)})
    overheads = db.scalars(select(Overhead).where(Overhead.run_id == rid)).all()
    return render(request, "run.html", user, run=run, v=production.variance(db, run), pack=pack,
                  cost=costing.run_cost(db, run) if run.status == "completed" else None, overheads=overheads)


@router.post("/production/{rid}/complete")
async def run_complete(rid: int, request: Request, user=Depends(area("production", True)), db: Session = Depends(get_db)):
    run = db.get(ProductionRun, rid)
    form = await request.form()
    try:
        good = int(form.get("good_output") or 0)
    except ValueError:
        flash(request, "Good output must be a whole number of packets", "error")
        return go(f"/production/{rid}")
    _do(request, db, lambda: production.complete_run(db, user, run, good, _lines_from_form(form),
                                                     form.get("scrap_note") or None),
        lambda r: f"Run completed: {r.good_output} packets added to stock as batch {r.batch_no}")
    return go(f"/production/{rid}")


@router.post("/production/{rid}/cancel")
def run_cancel(rid: int, request: Request, reason: str = Form(""), user=Depends(area("production", True)),
               db: Session = Depends(get_db)):
    run = db.get(ProductionRun, rid)
    _do(request, db, lambda: production.cancel_run(db, user, run, reason), "Run reversed; all stock movements undone")
    return go(f"/production/{rid}")


@router.post("/production/{rid}/overhead")
def run_overhead(rid: int, request: Request, category: str = Form(...), amount: str = Form(...), note: str = Form(""),
                 user=Depends(area("production", True)), db: Session = Depends(get_db)):
    run = db.get(ProductionRun, rid)
    _do(request, db, lambda: costing.add_overhead(db, user, category, to_paise(amount), run=run, note=note or None),
        "Overhead added - cost per packet updated")
    return go(f"/production/{rid}")


@router.get("/overheads")
def overheads(request: Request, user=Depends(area("production")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Overhead).order_by(Overhead.id.desc())).all()
    return render(request, "overheads.html", user, rows=rows)


@router.post("/overheads")
def overhead_month(request: Request, category: str = Form(...), amount: str = Form(...), on: str = Form(""),
                   note: str = Form(""), user=Depends(area("production", True)), db: Session = Depends(get_db)):
    _do(request, db, lambda: costing.add_overhead(db, user, category, to_paise(amount),
                                                  on=date.fromisoformat(on) if on else None, note=note or None),
        "Month-level overhead added; it is shared across the month's runs by good packets")
    return go("/overheads")


# --------------------------------------------------------------------------- purchases

@router.get("/purchases")
def purchases(request: Request, user=Depends(area("purchases")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Purchase).order_by(Purchase.id.desc())).all()
    mats = db.scalars(select(Item).where(Item.kind != "finished").order_by(Item.kind, Item.name)).all()
    sups = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return render(request, "purchases.html", user, purchases=rows, materials=mats, suppliers=sups,
                  units={m.id: units_for(m) for m in mats})


@router.post("/purchases")
async def purchase_create(request: Request, user=Depends(area("purchases", True)), db: Session = Depends(get_db)):
    form = await request.form()
    sup = db.get(Supplier, int(form["supplier_id"]))
    lines = []
    for i, iid in enumerate(form.getlist("item_id")):
        if iid and form.getlist("qty")[i]:
            lines.append({"item": int(iid), "qty": form.getlist("qty")[i], "unit": form.getlist("unit")[i],
                          "rate": form.getlist("rate")[i] or "0",
                          "gst_rate_bp": int(float(form.getlist("gst")[i] or 0) * 100)})
    _do(request, db, lambda: purchase.record_purchase(db, user, sup, lines,
                                                      on=date.fromisoformat(form["date"]) if form.get("date") else None,
                                                      supplier_invoice=form.get("supplier_invoice") or None),
        lambda p: f"Purchase {p.number} saved - stock increased")
    return go("/purchases")
