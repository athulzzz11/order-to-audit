"""Monthly reports for accounts and audit.

Every report returns the same shape (title, columns, rows, totals), so one
exporter handles CSV, Excel and PDF for all of them. Money cells hold paise
ints; qty cells hold Decimals in the display unit.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (AuditLog, CreditNote, Customer, Invoice, Item, Payment, ProductionRun, Purchase,
                        StockMovement)
from app.services import costing, production, sales, stock
from app.services.common import setting
from app.units import from_base


@dataclass
class Report:
    key: str
    title: str
    columns: list[tuple[str, str]]  # (label, kind) kind: text|money|qty|int|pct|date|flag
    rows: list[list] = field(default_factory=list)
    totals: list | None = None
    note: str | None = None


def _sum_cols(rows, columns):
    t = []
    for i, (_, kind) in enumerate(columns):
        t.append(sum((r[i] or 0) for r in rows) if kind == "money" else None)
    t[0] = "Total"
    return t


# --------------------------------------------------------------------------- sales

def sales_register(db: Session, start: date, end: date) -> Report:
    cols = [("Doc no", "text"), ("Date", "date"), ("Customer", "text"), ("GSTIN", "text"), ("Place of supply", "text"),
            ("Type", "text"), ("Taxable", "money"), ("CGST", "money"), ("SGST", "money"), ("IGST", "money"),
            ("Total", "money"), ("Status", "text")]
    rows = []
    for i in db.scalars(select(Invoice).where(Invoice.date.between(start, end)).order_by(Invoice.date, Invoice.id)):
        rows.append([i.number, i.date, i.customer.name, i.customer_gstin or "B2C", i.place_of_supply, "Invoice",
                     i.taxable_paise, i.cgst_paise, i.sgst_paise, i.igst_paise, i.total_paise, i.status])
    for c in db.scalars(select(CreditNote).where(CreditNote.date.between(start, end)).order_by(CreditNote.id)):
        i = c.invoice
        rows.append([c.number, c.date, i.customer.name, i.customer_gstin or "B2C", i.place_of_supply,
                     f"Credit note vs {i.number}", -c.taxable_paise, -c.cgst_paise, -c.sgst_paise, -c.igst_paise,
                     -c.total_paise, c.reason])
    rows.sort(key=lambda r: (r[1], r[0]))
    return Report("sales_register", "Sales register", cols, rows, _sum_cols(rows, cols),
                  "Cancelled invoices stay in the register; their credit notes appear as negative rows.")


def _net_lines(db, start, end):
    """(customer, product, qty, taxable) net of credit notes in the period."""
    out = []
    for i in db.scalars(select(Invoice).where(Invoice.date.between(start, end))):
        for l in i.lines:
            out.append((i.customer, l.product, l.qty, l.taxable_paise, l.cgst_paise + l.sgst_paise + l.igst_paise))
    for c in db.scalars(select(CreditNote).where(CreditNote.date.between(start, end))):
        for l in c.invoice.lines:
            out.append((c.invoice.customer, l.product, -l.qty, -l.taxable_paise,
                        -(l.cgst_paise + l.sgst_paise + l.igst_paise)))
    return out


def sales_by_customer(db: Session, start: date, end: date) -> Report:
    cols = [("Customer", "text"), ("Type", "text"), ("State", "text"), ("Packets", "int"), ("Taxable", "money"),
            ("GST", "money"), ("Gross", "money")]
    agg = {}
    for cust, _, q, t, g in _net_lines(db, start, end):
        a = agg.setdefault(cust.id, [cust.name, cust.kind, cust.state_name, 0, 0, 0, 0])
        a[3] += q
        a[4] += t
        a[5] += g
        a[6] += t + g
    rows = sorted(agg.values(), key=lambda r: -r[4])
    return Report("sales_by_customer", "Customer-wise sales", cols, rows, _sum_cols(rows, cols))


def sales_by_product(db: Session, start: date, end: date) -> Report:
    cols = [("Product", "text"), ("HSN", "text"), ("Packets", "int"), ("Taxable", "money"), ("GST", "money"),
            ("Avg price / packet", "money")]
    agg = {}
    for _, p, q, t, g in _net_lines(db, start, end):
        a = agg.setdefault(p.id, [p.name, p.hsn, 0, 0, 0, 0])
        a[2] += q
        a[3] += t
        a[4] += g
    for a in agg.values():
        a[5] = a[3] // a[2] if a[2] else 0
    rows = list(agg.values())
    t = _sum_cols(rows, cols)
    t[5] = None
    return Report("sales_by_product", "Product-wise sales", cols, rows, t)


def gst_summary(db: Session, start: date, end: date) -> Report:
    """Outward supplies arranged by GSTR-1 table, with the GSTR-3B 3.1(a) line."""
    cols = [("Return / table", "text"), ("Description", "text"), ("Rate %", "pct"), ("Docs", "int"),
            ("Taxable", "money"), ("IGST", "money"), ("CGST", "money"), ("SGST", "money"), ("Total tax", "money")]
    buckets = {}

    def add(key, desc, rate, sign, line, doc):
        b = buckets.setdefault((key, desc, rate), {"docs": set(), "t": 0, "i": 0, "c": 0, "s": 0})
        b["docs"].add(doc)
        b["t"] += sign * line.taxable_paise
        b["i"] += sign * line.igst_paise
        b["c"] += sign * line.cgst_paise
        b["s"] += sign * line.sgst_paise

    hsn = {}
    for inv in db.scalars(select(Invoice).where(Invoice.date.between(start, end))):
        b2b = bool(inv.customer_gstin)
        if b2b:
            key, desc = "GSTR-1 Table 4A", f"B2B {'intra' if inv.supply_type == 'intra' else 'inter'}-state"
        else:
            key, desc = "GSTR-1 Table 7", f"B2C (others) {inv.supply_type}-state"
        for l in inv.lines:
            add(key, desc, l.gst_rate_bp, 1, l, inv.number)
            h = hsn.setdefault((l.hsn, l.gst_rate_bp), [0, 0, 0, 0, 0])
            h[0] += l.qty; h[1] += l.taxable_paise; h[2] += l.igst_paise; h[3] += l.cgst_paise; h[4] += l.sgst_paise
    for cn in db.scalars(select(CreditNote).where(CreditNote.date.between(start, end))):
        reg = "registered" if cn.invoice.customer_gstin else "unregistered"
        for l in cn.invoice.lines:
            add("GSTR-1 Table 9B", f"Credit notes ({reg})", l.gst_rate_bp, -1, l, cn.number)
            h = hsn.setdefault((l.hsn, l.gst_rate_bp), [0, 0, 0, 0, 0])
            h[0] -= l.qty; h[1] -= l.taxable_paise; h[2] -= l.igst_paise; h[3] -= l.cgst_paise; h[4] -= l.sgst_paise
    rows = []
    for (key, desc, rate), b in sorted(buckets.items()):
        rows.append([key, desc, Decimal(rate) / 100, len(b["docs"]), b["t"], b["i"], b["c"], b["s"],
                     b["i"] + b["c"] + b["s"]])
    for (code, rate), h in sorted(hsn.items()):
        rows.append(["GSTR-1 Table 12", f"HSN {code} - {h[0]} packets", Decimal(rate) / 100, None, h[1], h[2], h[3],
                     h[4], h[2] + h[3] + h[4]])
    net = [sum(r[i] for r in rows if r[0] != "GSTR-1 Table 12") for i in (4, 5, 6, 7)]
    rows.append(["GSTR-3B 3.1(a)", "Outward taxable supplies (net of credit notes)", None, None, *net, sum(net[1:])])
    return Report("gst_summary", "GST summary of outward supplies", cols, rows, None,
                  "Table 12 (HSN) repeats the same supplies by HSN code, so it is not added into 3.1(a).")


# --------------------------------------------------------------------------- purchases

def purchase_register(db: Session, start: date, end: date) -> Report:
    cols = [("Purchase no", "text"), ("Date", "date"), ("Supplier", "text"), ("Supplier inv", "text"),
            ("Item", "text"), ("Qty", "qty"), ("Unit", "text"), ("Rate", "money"), ("Taxable", "money"),
            ("GST", "money"), ("Total", "money")]
    rows = []
    for p in db.scalars(select(Purchase).where(Purchase.date.between(start, end)).order_by(Purchase.date, Purchase.id)):
        for l in p.lines:
            rows.append([p.number, p.date, p.supplier.name, p.supplier_invoice or "", l.item.name,
                         Decimal(l.entered_qty), l.entered_unit, l.rate_paise, l.taxable_paise, l.tax_paise,
                         l.taxable_paise + l.tax_paise])
    t = _sum_cols(rows, cols)
    t[7] = None
    return Report("purchase_register", "Purchase register", cols, rows, t)


# --------------------------------------------------------------------------- stock

KIND_LABEL = {"raw": "Raw material", "packaging": "Packaging", "finished": "Finished goods"}
IN_KINDS = ("opening", "purchase", "production_output", "sales_return")


def stock_summary(db: Session, start: date, end: date, kind: str | None = None) -> Report:
    cols = [("Group", "text"), ("Item", "text"), ("Unit", "text"), ("Opening", "qty"), ("Receipts", "qty"),
            ("Issued to production", "qty"), ("Sales dispatch", "qty"), ("Adjustments", "qty"), ("Closing", "qty"),
            ("Closing value", "money"), ("Reorder level", "qty"), ("Check", "flag")]
    rows = []
    q = select(Item).order_by(Item.kind.desc(), Item.code)
    if kind:
        q = q.where(Item.kind == kind)
    for it in db.scalars(q):
        opening = stock.balance(db, it.id, upto=start - timedelta(days=1))
        mv = db.scalars(select(StockMovement).where(StockMovement.item_id == it.id,
                                                    StockMovement.date.between(start, end))).all()
        rec = sum(m.qty for m in mv if m.kind in IN_KINDS)
        iss = -sum(m.qty for m in mv if m.kind == "production_issue")
        sal = -sum(m.qty for m in mv if m.kind == "sale_dispatch")
        adj = sum(m.qty for m in mv if m.kind == "adjustment")
        closing = stock.balance(db, it.id, upto=end)
        ok = opening + rec - iss - sal + adj == closing
        cv = stock.value(db, it.id, upto=end)
        u = it.display_unit
        rows.append([KIND_LABEL[it.kind], it.name, u, *(from_base(it, x) for x in (opening, rec, iss, sal, adj, closing)),
                     cv, from_base(it, it.reorder_level), "OK" if ok else "MISMATCH"])
    t = _sum_cols(rows, cols)
    return Report("stock_summary", "Stock summary", cols, rows, t,
                  "Opening + receipts - issues - sales +/- adjustments = closing, recomputed from the ledger for "
                  "every item. Values at moving weighted-average cost.")


def stock_ledger(db: Session, start: date, end: date, item_id: int | None = None) -> Report:
    cols = [("Date", "date"), ("Item", "text"), ("Movement", "text"), ("Source document", "text"), ("Batch", "text"),
            ("Expiry", "date"), ("In", "qty"), ("Out", "qty"), ("Balance", "qty"), ("Unit", "text"),
            ("Value", "money"), ("Note", "text")]
    rows = []
    q = select(Item).order_by(Item.kind.desc(), Item.code)
    if item_id:
        q = q.where(Item.id == item_id)
    for it in db.scalars(q):
        bal = stock.balance(db, it.id, upto=start - timedelta(days=1))
        rows.append([start, it.name, "Opening balance", "", "", None, None, None, from_base(it, bal),
                     it.display_unit, stock.value(db, it.id, upto=start - timedelta(days=1)), ""])
        for m in db.scalars(select(StockMovement).where(StockMovement.item_id == it.id,
                                                        StockMovement.date.between(start, end))
                            .order_by(StockMovement.date, StockMovement.id)):
            bal += m.qty
            rows.append([m.date, it.name, m.kind.replace("_", " "), m.source_ref or "", m.batch_no or "", m.expiry,
                         from_base(it, m.qty) if m.qty > 0 else None,
                         from_base(it, -m.qty) if m.qty < 0 else None,
                         from_base(it, bal), it.display_unit, m.value_paise, m.note or ""])
    return Report("stock_ledger", "Stock ledger", cols, rows, None,
                  "Every movement with the document that caused it. Value is signed (+ in, - out).")


# --------------------------------------------------------------------------- production & costing

def production_report(db: Session, start: date, end: date) -> Report:
    cols = [("Run", "text"), ("Date", "date"), ("Product", "text"), ("Batch", "text"), ("Line", "text"),
            ("Unit", "text"), ("Planned", "qty"), ("Actual", "qty"), ("of which wasted", "qty"), ("Variance", "qty"),
            ("Variance %", "pct"), ("Flag", "flag")]
    rows = []
    for r in db.scalars(select(ProductionRun).where(ProductionRun.date.between(start, end),
                                                    ProductionRun.status != "cancelled").order_by(ProductionRun.id)):
        v = production.variance(db, r)
        for l in v["lines"]:
            it = l["item"]
            f = lambda x: None if x is None else from_base(it, x)  # noqa: E731
            rows.append([r.number, r.date, r.product.name, r.batch_no or "in progress",
                         f"{it.name} ({'per batch' if l['basis'] == 'batch' else 'per packet' if l['basis'] == 'output' else 'not in BOM'})",
                         it.display_unit, f(l["planned"]), f(l["actual"]), f(l["wasted"]), f(l["diff"]), l["pct"],
                         "FLAG" if l["flagged"] else "ok"])
        if v["yield"]:
            y = v["yield"]
            rows.append([r.number, r.date, r.product.name, r.batch_no, "YIELD (good packets)", "packet",
                         Decimal(y["expected"]), Decimal(y["actual"]), None, Decimal(y["diff"]), y["pct"],
                         "FLAG" if y["flagged"] else "ok"])
        if r.scrap_note:
            rows.append([r.number, r.date, r.product.name, r.batch_no, f"Scrap note: {r.scrap_note}", "", None, None,
                         None, None, None, ""])
    tol = Decimal(setting(db, "variance_tolerance_bp")) / 100
    return Report("production", "Production report: planned vs actual", cols, rows, None,
                  f"Flagged when |variance| > {tol}%. Packaging is planned per good packet (whole cartons only).")


def costing_report(db: Session, start: date, end: date) -> Report:
    cols = [("Run", "text"), ("Date", "date"), ("Product", "text"), ("Good packets", "int"), ("Raw material", "money"),
            ("Packaging", "money"), ("Overheads", "money"), ("Total cost", "money"), ("Cost / packet", "money"),
            ("Selling price", "money"), ("Margin / packet", "money"), ("Margin %", "pct")]
    rows = []
    for r in db.scalars(select(ProductionRun).where(ProductionRun.date.between(start, end),
                                                    ProductionRun.status == "completed").order_by(ProductionRun.id)):
        c = costing.run_cost(db, r)
        p100 = lambda d: None if d is None else int(d * 100)  # noqa: E731
        rows.append([r.number, r.date, r.product.name, c["good_output"], c["raw_paise"], c["packaging_paise"],
                     c["overhead_paise"], c["total_paise"], p100(c["cost_per_packet"]), p100(c["selling_price"]),
                     p100(c["margin"]), c["margin_pct"]])
    t = _sum_cols(rows, cols)
    for i in (8, 9, 10):
        t[i] = None
    return Report("costing", "Cost per packet", cols, rows, t,
                  "Materials at moving weighted-average cost (actual quantities incl. wastage). Overheads entered "
                  "against a run go to that run; month-level overheads are shared by good packets.")


# --------------------------------------------------------------------------- money

def collections(db: Session, start: date, end: date) -> Report:
    cols = [("Receipt", "text"), ("Date", "date"), ("Customer", "text"), ("Against invoice", "text"),
            ("Mode", "text"), ("Reference", "text"), ("Amount", "money")]
    rows = [[p.number, p.date, p.customer.name, p.invoice.number if p.invoice else "On account", p.mode,
             p.reference or "", p.amount_paise]
            for p in db.scalars(select(Payment).where(Payment.date.between(start, end)).order_by(Payment.date, Payment.id))]
    return Report("collections", "Payments and collections", cols, rows, _sum_cols(rows, cols))


def outstanding(db: Session, start: date, end: date) -> Report:
    cols = [("Customer", "text"), ("State", "text"), ("Credit days", "int"), ("0-30 days", "money"),
            ("31-60 days", "money"), ("61-90 days", "money"), ("90+ days", "money"), ("Advance", "money"),
            ("Outstanding", "money")]
    rows = []
    for c in db.scalars(select(Customer).order_by(Customer.name)):
        a = sales.ageing(db, c.id, end)
        if any(a.values()):
            rows.append([c.name, c.state_name, c.credit_days, a["0-30"], a["31-60"], a["61-90"], a["90+"],
                         a["advance"], a["total"]])
    return Report("outstanding", f"Outstanding receivables with ageing (as of {end:%d %b %Y})", cols, rows,
                  _sum_cols(rows, cols), "Ageing by invoice date. Untagged payments settle the oldest invoice first.")


def audit_trail(db: Session, start: date, end: date) -> Report:
    cols = [("When", "text"), ("User", "text"), ("Action", "text"), ("Document", "text"), ("Detail", "text")]
    rows = [[f"{a.ts:%Y-%m-%d %H:%M}", a.user.name if a.user else "system", a.action, a.ref or "", a.detail or ""]
            for a in db.scalars(select(AuditLog).where(AuditLog.ts >= start, AuditLog.ts < end + timedelta(days=1))
                                .order_by(AuditLog.id))]
    return Report("audit_trail", "Audit trail", cols, rows)


REPORTS = {
    "sales_register": ("Sales register", sales_register),
    "sales_by_customer": ("Customer-wise sales", sales_by_customer),
    "sales_by_product": ("Product-wise sales", sales_by_product),
    "gst_summary": ("GST summary (GSTR-1 / 3B)", gst_summary),
    "purchase_register": ("Purchase register", purchase_register),
    "stock_summary": ("Stock summary", stock_summary),
    "stock_ledger": ("Stock ledger", stock_ledger),
    "production": ("Production: planned vs actual", production_report),
    "costing": ("Cost per packet", costing_report),
    "collections": ("Payments and collections", collections),
    "outstanding": ("Outstanding with ageing", outstanding),
    "audit_trail": ("Audit trail", audit_trail),
}
