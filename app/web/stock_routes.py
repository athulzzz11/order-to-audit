"""Stock views, adjustments and master data (items, BOM, customers, suppliers)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Bom, BomLine, Customer, Item, Supplier, User
from app.money import to_paise
from app.services import reports, sales, stock
from app.services.common import BusinessError, audit
from app.services.gst import STATES
from app.units import to_base, units_for
from app.web.deps import area, flash, go, render
from app.web.sales_routes import _do

router = APIRouter()


@router.get("/stock")
def stock_view(request: Request, user=Depends(area("stock")), db: Session = Depends(get_db)):
    items = db.scalars(select(Item).order_by(Item.kind.desc(), Item.code)).all()
    rows = []
    for it in items:
        bal = stock.balance(db, it.id)
        rows.append({"item": it, "balance": bal, "value": stock.value(db, it.id), "low": bal < it.reorder_level,
                     "reserved": sales.reserved(db, it.id) if it.kind == "finished" else 0,
                     "batches": stock.batches(db, it.id) if it.tracks_batches else [], "units": units_for(it)})
    return render(request, "stock.html", user, rows=rows)


@router.get("/stock/ledger")
def ledger(request: Request, item_id: int | None = None, start: str = "", end: str = "",
           user=Depends(area("stock")), db: Session = Depends(get_db)):
    s = date.fromisoformat(start) if start else date.today().replace(day=1)
    e = date.fromisoformat(end) if end else date.today()
    rep = reports.stock_ledger(db, s, e, item_id)
    items = db.scalars(select(Item).order_by(Item.kind.desc(), Item.code)).all()
    return render(request, "report.html", user, rep=rep, start=s, end=e, key="stock_ledger", items=items,
                  item_id=item_id, reports=reports.REPORTS)


@router.post("/stock/adjust")
def adjust(request: Request, item_id: int = Form(...), qty: str = Form(...), unit: str = Form(...),
           direction: str = Form("minus"), reason: str = Form(""), user=Depends(area("stock", True)),
           db: Session = Depends(get_db)):
    it = db.get(Item, item_id)

    def do():
        q = to_base(it, qty, unit)
        return stock.adjust(db, user=user, item=it, qty=q if direction == "plus" else -q, reason=reason)
    _do(request, db, do, lambda m: f"Adjustment {m.source_ref} posted to the ledger")
    return go("/stock")


# --------------------------------------------------------------------------- masters

@router.get("/masters")
def masters(request: Request, user=Depends(area("masters")), db: Session = Depends(get_db)):
    return render(request, "masters.html", user,
                  items=db.scalars(select(Item).order_by(Item.kind.desc(), Item.code)).all(),
                  boms=db.scalars(select(Bom).where(Bom.active.is_(True))).all(),
                  customers=db.scalars(select(Customer).order_by(Customer.name)).all(),
                  suppliers=db.scalars(select(Supplier).order_by(Supplier.name)).all(),
                  salesmen=db.scalars(select(User).where(User.role == "salesman")).all(), states=STATES)


@router.post("/masters/customers")
def add_customer(request: Request, name: str = Form(...), kind: str = Form("retailer"), gstin: str = Form(""),
                 state_code: str = Form("33"), phone: str = Form(""), address: str = Form(""),
                 salesman_id: int = Form(...), user=Depends(area("masters", True)), db: Session = Depends(get_db)):
    gstin = gstin.strip().upper() or None
    if gstin and (len(gstin) != 15 or gstin[:2] != state_code):
        flash(request, "GSTIN must be 15 characters and start with the state code", "error")
        return go("/masters")
    c = Customer(name=name, kind=kind, gstin=gstin, state_code=state_code, state_name=STATES[state_code],
                 phone=phone or None, address=address or None, salesman_id=salesman_id)
    db.add(c)
    db.flush()
    audit(db, user, "customer_add", "customer", c.id, ref=name)
    db.commit()
    flash(request, f"Customer {name} added")
    return go("/masters")


@router.post("/masters/suppliers")
def add_supplier(request: Request, name: str = Form(...), gstin: str = Form(""), phone: str = Form(""),
                 user=Depends(area("masters", True)), db: Session = Depends(get_db)):
    db.add(Supplier(name=name, gstin=gstin or None, phone=phone or None))
    db.commit()
    flash(request, f"Supplier {name} added")
    return go("/masters")


@router.post("/masters/items")
def add_item(request: Request, code: str = Form(...), name: str = Form(...), kind: str = Form(...),
             display_unit: str = Form(...), reorder: str = Form("0"), price: str = Form("0"), hsn: str = Form(""),
             gst: str = Form("5"), per_carton: str = Form(""), user=Depends(area("masters", True)),
             db: Session = Depends(get_db)):
    base = {"kg": "g", "g": "g", "l": "ml", "ml": "ml", "pcs": "pcs", "packet": "pcs"}[display_unit]
    it = Item(code=code.upper(), name=name, kind=kind, base_unit=base, display_unit=display_unit)
    if kind == "finished":
        it.selling_price_paise, it.hsn, it.gst_rate_bp = to_paise(price), hsn or None, int(float(gst) * 100)
        it.pack_per_carton, it.shelf_life_days, it.batch_code = int(per_carton or 0) or None, 90, code.upper()[:6]
    try:
        it.reorder_level = to_base(it, reorder or 0, display_unit)
    except ValueError as e:
        flash(request, str(e), "error")
        return go("/masters")
    db.add(it)
    db.flush()
    audit(db, user, "item_add", "item", it.id, ref=it.code)
    db.commit()
    flash(request, f"Item {it.name} added")
    return go("/masters")


@router.post("/masters/bom/{bid}")
async def edit_bom(bid: int, request: Request, user=Depends(area("bom", True)), db: Session = Depends(get_db)):
    """Saving a BOM creates a new version; past runs keep pointing at the version they used."""
    old = db.get(Bom, bid)
    form = await request.form()
    try:
        new = Bom(product_id=old.product_id, version=old.version + 1, batch_output=int(form["batch_output"]),
                  note=form.get("note") or old.note)
        for ln in old.lines:
            q = form.get(f"qty_{ln.id}")
            if q and float(q) > 0:
                new.lines.append(BomLine(item_id=ln.item_id, item=ln.item, basis=form.get(f"basis_{ln.id}", ln.basis),
                                         qty_per_batch=to_base(ln.item, q, ln.item.display_unit)))
        if form.get("new_item_id") and form.get("new_qty"):
            it = db.get(Item, int(form["new_item_id"]))
            new.lines.append(BomLine(item_id=it.id, item=it, basis=form.get("new_basis", "batch"),
                                     qty_per_batch=to_base(it, form["new_qty"], it.display_unit)))
        if not new.lines or new.batch_output <= 0:
            raise BusinessError("A BOM needs a yield and at least one line")
    except (ValueError, BusinessError) as e:
        flash(request, str(e), "error")
        return go("/masters")
    old.active = False
    db.add(new)
    db.flush()
    audit(db, user, "bom_version", "bom", new.id, ref=f"{old.product.code} v{new.version}",
          lines=[{"item": l.item.code, "qty": l.qty_per_batch, "basis": l.basis} for l in new.lines])
    db.commit()
    flash(request, f"BOM for {old.product.name} saved as version {new.version}")
    return go("/masters")
