"""Section 5 benchmark.

`run_scenario(db)` performs the 9 steps through the same service functions the
UI calls. `checks(db)` reads the *current* database and compares it with the
expected results, so it also works after you have clicked through the demo by
hand. Both are exposed: CLI `python -m app.benchmark` and the in-app
"Benchmark check" page.
"""
from decimal import Decimal

from sqlalchemy import select

from app.models import Customer, Invoice, Item, Order, ProductionRun, StockMovement, Supplier, User
from app.money import fmt, to_paise
from app.services import costing, production, purchase, sales, stock
from app.services.common import BusinessError
from app.units import fmt_qty


def _item(db, code):
    return db.scalar(select(Item).where(Item.code == code))


def _user(db, username):
    return db.scalar(select(User).where(User.username == username))


def _cust(db, prefix):
    return db.scalar(select(Customer).where(Customer.name.like(f"{prefix}%")))


def run_scenario(db, log=print):
    admin, prod = _user(db, "admin"), _user(db, "prod")
    flour, oil, spice = _item(db, "FLOUR"), _item(db, "OIL"), _item(db, "SPICE")
    pouch, carton, mur = _item(db, "POUCH200"), _item(db, "CARTON20"), _item(db, "MUR200")

    p = purchase.record_purchase(db, prod, db.scalar(select(Supplier)),
                                 [{"item": flour, "qty": "60", "unit": "kg", "rate": "45"}], supplier_invoice="SLFM-881")
    log(f"1. Purchase {p.number}: 60 kg flour @ Rs 45")

    run = production.start_run(db, prod, mur, 1, [
        {"item": flour, "qty": "40", "unit": "kg"},
        {"item": oil, "qty": "10.5", "unit": "l"},
        {"item": spice, "qty": "1.2", "unit": "kg"},
    ])
    log(f"2. Run {run.number} started: 40 kg flour, 10.5 L oil, 1.2 kg spice issued")

    production.complete_run(db, prod, run, 188, [
        {"item": pouch, "qty": "188", "unit": "pcs", "wasted": "6"},
        {"item": carton, "qty": "9", "unit": "pcs"},
    ], scrap_note="Broken pieces swept from fryer")
    log(f"3. Run completed: 188 good packets, 6 pouches damaged, 9 cartons -> batch {run.batch_no}")

    oa = sales.create_order(db, _user(db, "salesA"), _cust(db, "Retailer A"),
                            [{"product": mur, "qty": 150, "unit": "packet"}], client_uuid="bench-order-A")
    log(f"4. Salesman A order {oa.number}: 150 packets for Retailer A")

    sales.approve(db, admin, oa)
    inv = sales.raise_invoice(db, admin, oa)
    sales.dispatch(db, admin, inv, vehicle_no="TN45AB1234")
    log(f"5. Approved, invoice {inv.number} = {fmt(inv.total_paise)}, dispatched")

    ob = sales.create_order(db, _user(db, "salesB"), _cust(db, "Distributor B"),
                            [{"product": mur, "qty": 120, "unit": "packet"}], client_uuid="bench-order-B")
    sales.approve(db, admin, ob)
    try:
        sales.raise_invoice(db, admin, ob)
    except sales.ShortageError as e:
        log(f"6. Distributor B order {ob.number}: invoice blocked -> {e}")

    pay = sales.record_payment(db, admin, inv.customer, to_paise("4000"), invoice=inv, mode="upi", reference="UPI-REF-1")
    log(f"7. Payment {pay.number}: Rs 4,000 from Retailer A")

    costing.add_overhead(db, prod, "labour", to_paise("1200"), run=run)
    costing.add_overhead(db, prod, "gas_power", to_paise("600"), run=run)
    c = costing.run_cost(db, run)
    log(f"8. Overheads added. Cost per packet = Rs {c['cost_per_packet']}")
    db.commit()
    log("9. Reports: open /reports or run `python -m app.export_reports`")


def _chk(name, expected, actual, ok):
    return {"check": name, "expected": expected, "actual": actual, "ok": bool(ok)}


def checks(db) -> list[dict]:
    """Compare the live database with the section 5 'Results we will check' table."""
    out = []
    flour, oil, pouch, mur = (_item(db, c) for c in ("FLOUR", "OIL", "POUCH200", "MUR200"))
    if not mur:
        return [_chk("Seed data", "loaded", "missing", False)]
    out.append(_chk("Flour closing balance", "120 kg", fmt_qty(flour, stock.balance(db, flour.id)),
                    stock.balance(db, flour.id) == 120000))
    out.append(_chk("Oil closing balance", "19.5 l", fmt_qty(oil, stock.balance(db, oil.id)),
                    stock.balance(db, oil.id) == 19500))
    out.append(_chk("Pouches closing balance", "306 pcs", fmt_qty(pouch, stock.balance(db, pouch.id)),
                    stock.balance(db, pouch.id) == 306))

    run = db.scalar(select(ProductionRun).where(ProductionRun.product_id == mur.id,
                                                ProductionRun.status == "completed").order_by(ProductionRun.id))
    after_run = db.scalar(select(StockMovement.id).where(StockMovement.item_id == mur.id,
                                                         StockMovement.kind == "production_output")
                          .order_by(StockMovement.id))
    fg_after_run = sum(m.qty for m in db.scalars(select(StockMovement).where(
        StockMovement.item_id == mur.id, StockMovement.id <= (after_run or 0))))
    out.append(_chk("Finished goods after run", "238 packets", f"{fg_after_run} packets", fg_after_run == 238))

    ra, db_ = _cust(db, "Retailer A"), _cust(db, "Distributor B")
    inv = db.scalar(select(Invoice).where(Invoice.customer_id == ra.id).order_by(Invoice.id))
    fg_after_disp = None
    if inv and inv.status == "dispatched":
        last = db.scalar(select(StockMovement.id).where(StockMovement.source_type == "invoice",
                                                        StockMovement.source_id == inv.id)
                         .order_by(StockMovement.id.desc()))
        fg_after_disp = sum(m.qty for m in db.scalars(select(StockMovement).where(
            StockMovement.item_id == mur.id, StockMovement.id <= last)))
    out.append(_chk("Finished goods after Retailer A dispatch", "88 packets",
                    f"{fg_after_disp} packets" if fg_after_disp is not None else "not dispatched",
                    fg_after_disp == 88))

    if run:
        v = production.variance(db, run)
        y = v["yield"]
        out.append(_chk("Yield variance", "188 vs 200, -6%, flagged",
                        f"{y['actual']} vs {y['expected']}, {y['pct']}%, {'flagged' if y['flagged'] else 'ok'}",
                        y["actual"] == 188 and y["pct"] == Decimal("-6.00") and y["flagged"]))
        ol = next(l for l in v["lines"] if l["item"].code == "OIL")
        out.append(_chk("Oil consumption variance", "+5%, flagged",
                        f"{ol['pct']:+}%, {'flagged' if ol['flagged'] else 'ok'}",
                        ol["pct"] == Decimal("5.00") and ol["flagged"]))
    else:
        out.append(_chk("Production run", "completed", "none", False))

    if inv:
        out.append(_chk("Retailer A invoice", "₹7,500.00 + ₹187.50 CGST + ₹187.50 SGST = ₹7,875.00",
                        f"{fmt(inv.taxable_paise)} + {fmt(inv.cgst_paise)} CGST + {fmt(inv.sgst_paise)} SGST "
                        f"= {fmt(inv.total_paise)}",
                        (inv.taxable_paise, inv.cgst_paise, inv.sgst_paise, inv.total_paise) ==
                        (750000, 18750, 18750, 787500)))
        o = sales.outstanding(db, ra.id)
        out.append(_chk("Retailer A outstanding", "₹3,875.00", fmt(o), o == 387500))
    else:
        out.append(_chk("Retailer A invoice", "₹7,875.00", "not raised", False))

    ob = db.scalar(select(Order).where(Order.customer_id == db_.id).order_by(Order.id))
    if ob:
        sc = sales.stock_check(db, ob)[0]
        pv = sales.tax_preview(db, ob)
        out.append(_chk("Distributor B order", "Shortage of 32 flagged; IGST if invoiced",
                        f"Shortage {sc['shortage']} ({sc['batches_needed']} batch to make); "
                        f"{pv['supply_type']}-state, IGST {fmt(pv['igst'])}",
                        sc["shortage"] == 32 and pv["supply_type"] == "inter" and pv["igst"] == 30000))
    else:
        out.append(_chk("Distributor B order", "placed", "none", False))

    if run:
        c = costing.run_cost(db, run)
        out.append(_chk("Cost per packet", "your figure (weighted average, overheads per run)",
                        f"₹{c['cost_per_packet']} (materials {fmt(c['raw_paise'] + c['packaging_paise'])} + "
                        f"overheads {fmt(c['overhead_paise'])}) / {c['good_output']}; margin {c['margin_pct']}%",
                        c["overhead_paise"] > 0))
    return out


if __name__ == "__main__":
    import argparse

    from app import db as dbm
    from app.seed import reset_and_seed

    ap = argparse.ArgumentParser(description="Reset the DB, run the section 5 scenario and print the checks")
    ap.add_argument("--keep", action="store_true", help="run on the existing DB instead of resetting")
    a = ap.parse_args()
    dbm.init()
    if not a.keep:
        reset_and_seed()
    s = dbm.SessionLocal()
    try:
        run_scenario(s)
    except BusinessError as e:
        print("Scenario stopped:", e)
    print()
    rows = checks(s)
    w = max(len(r["check"]) for r in rows)
    for r in rows:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']:<{w}}  {r['actual']}")
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} checks pass")
