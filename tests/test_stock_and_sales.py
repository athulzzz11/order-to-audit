from decimal import Decimal

import pytest
from sqlalchemy import select

from app.benchmark import _cust, _item, _user, run_scenario
from app.models import Item, StockMovement
from app.services import production, reports, sales, stock
from app.services.stock import InsufficientStock


def test_balance_is_sum_of_ledger_and_negative_stock_blocked(db):
    flour = _item(db, "FLOUR")
    assert stock.balance(db, flour.id) == 100000
    with pytest.raises(InsufficientStock):
        stock.post(db, item=flour, qty=-100001, kind="production_issue", source_type="production")


def test_weighted_average_issue_value(db):
    run_scenario(db, log=lambda *_: None)
    flour = _item(db, "FLOUR")
    issue = db.scalar(select(StockMovement).where(StockMovement.item_id == flour.id,
                                                  StockMovement.kind == "production_issue"))
    # (100 x 42 + 60 x 45) / 160 = 43.125 / kg -> 40 kg = Rs 1,725.00
    assert issue.value_paise == -172500
    assert stock.value(db, flour.id) == 690000 - 172500


def test_issuing_everything_leaves_zero_value(db):
    spice = _item(db, "SPICE")
    stock.post(db, item=spice, qty=-5000, kind="adjustment", source_type="adjustment")
    assert stock.balance(db, spice.id) == 0 and stock.value(db, spice.id) == 0


def test_variance_lines(db):
    run_scenario(db, log=lambda *_: None)
    run = db.scalar(select(production.ProductionRun))
    v = {l["item"].code: l for l in production.variance(db, run)["lines"]}
    assert v["OIL"]["pct"] == Decimal("5.00") and v["OIL"]["flagged"]
    assert v["FLOUR"]["pct"] == 0 and not v["FLOUR"]["flagged"]
    assert v["POUCH200"]["planned"] == 188 and v["POUCH200"]["actual"] == 194 and v["POUCH200"]["flagged"]
    assert v["CARTON20"]["planned"] == 9 and not v["CARTON20"]["flagged"]  # 9 full cartons for 188 packets


def test_fefo_dispatch_takes_opening_batch_first(db):
    run_scenario(db, log=lambda *_: None)
    inv = sales.open_items(db, _cust(db, "Retailer A").id)[0]["invoice"]
    got = {b["batch_no"]: b["qty"] for b in sales.dispatch_batches(db, inv)}
    assert got["MUR-OPENING"] == 50 and sum(got.values()) == 150


def test_offline_resend_is_idempotent(db):
    s, c, mur = _user(db, "salesA"), _cust(db, "Retailer A"), _item(db, "MUR200")
    a = sales.create_order(db, s, c, [{"product": mur, "qty": 1, "unit": "carton"}], client_uuid="abc")
    b = sales.create_order(db, s, c, [{"product": mur, "qty": 1, "unit": "carton"}], client_uuid="abc")
    assert a.id == b.id and a.lines[0].qty == 20


def test_invoice_reserves_stock_until_dispatch(db):
    s, c, mur, admin = _user(db, "salesA"), _cust(db, "Retailer A"), _item(db, "MUR200"), _user(db, "admin")
    o1 = sales.create_order(db, s, c, [{"product": mur, "qty": 40}])
    sales.approve(db, admin, o1)
    sales.raise_invoice(db, admin, o1)  # 50 on hand, 40 reserved
    o2 = sales.create_order(db, s, c, [{"product": mur, "qty": 20}])
    sales.approve(db, admin, o2)
    assert sales.stock_check(db, o2)[0]["shortage"] == 10
    with pytest.raises(sales.ShortageError):
        sales.raise_invoice(db, admin, o2)


def test_credit_note_reverses_invoice_and_returns_stock(db):
    run_scenario(db, log=lambda *_: None)
    admin, mur = _user(db, "admin"), _item(db, "MUR200")
    inv = sales.open_items(db, _cust(db, "Retailer A").id)[0]["invoice"]
    cn = sales.cancel_invoice(db, admin, inv, "Wrong customer")
    assert cn.total_paise == 787500 and cn.stock_returned
    assert stock.balance(db, mur.id) == 238
    assert sales.outstanding(db, inv.customer_id) == -400000  # Rs 4,000 now an advance
    reg = reports.sales_register(db, inv.date, inv.date)
    assert reg.totals[10] == 0  # invoice + credit note net to zero


def test_every_report_builds_and_stock_identity_holds(db):
    run_scenario(db, log=lambda *_: None)
    from datetime import date
    from app.export import to_csv, to_pdf, to_xlsx
    start, end = date.today().replace(day=1), date.today()
    for key, (_, fn) in reports.REPORTS.items():
        rep = fn(db, start, end)
        assert to_csv(rep, "p") and to_xlsx(rep, "p", "c")[:2] == b"PK" and to_pdf(rep, "p", "c")[:4] == b"%PDF"
    ss = reports.stock_summary(db, start, end)
    assert all(r[-1] == "OK" for r in ss.rows)
    gst = {r[0] + r[1]: r for r in reports.gst_summary(db, start, end).rows}
    assert gst["GSTR-3B 3.1(a)Outward taxable supplies (net of credit notes)"][4] == 750000
