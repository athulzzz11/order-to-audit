"""Sales loop: order -> verify -> GST invoice -> dispatch -> payment.

Stock rule: an invoice reserves goods until dispatched. "Available" for a new
order = on hand - reserved by invoiced-but-not-dispatched invoices. An invoice
cannot be raised for more than is available, so we never bill goods we lack.
"""
from datetime import date
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (CreditNote, Customer, Invoice, InvoiceLine, Item, Order, OrderLine, Payment,
                        StockMovement, now)
from app.services import stock
from app.services.common import BusinessError, audit, fy_of, next_number, setting
from app.services.gst import split_tax, supply_type
from app.services.production import batches_needed
from app.units import to_base


class ShortageError(BusinessError):
    def __init__(self, rows):
        self.rows = rows
        txt = "; ".join(f"{r['product'].name}: short by {r['shortage']} packets" for r in rows)
        super().__init__(f"Insufficient finished-goods stock. {txt}")


# --------------------------------------------------------------------------- orders

def create_order(db: Session, salesman, customer: Customer, lines: list[dict], client_uuid: str | None = None,
                 note: str | None = None, taken_at=None) -> Order:
    """lines: [{'product': Item|id, 'qty': 150, 'unit': 'packet'}]. Idempotent on client_uuid (offline resend)."""
    if client_uuid:
        existing = db.scalar(select(Order).where(Order.client_uuid == client_uuid))
        if existing:
            return existing
    if not lines:
        raise BusinessError("Add at least one product")
    on = date.today()
    order = Order(number=next_number(db, "SO", on), client_uuid=client_uuid, salesman_id=salesman.id,
                  customer_id=customer.id, customer=customer, status="submitted", note=note,
                  taken_at=taken_at or now())
    merged: dict[int, int] = {}
    for ln in lines:
        p = ln["product"] if isinstance(ln["product"], Item) else db.get(Item, int(ln["product"]))
        if not p or p.kind != "finished":
            raise BusinessError("Unknown product")
        q = to_base(p, ln["qty"], ln.get("unit") or "packet")
        if q <= 0:
            continue
        merged[p.id] = merged.get(p.id, 0) + q
    if not merged:
        raise BusinessError("Quantities must be more than zero")
    for pid, q in merged.items():
        p = db.get(Item, pid)
        order.lines.append(OrderLine(product_id=pid, product=p, qty=q, price_paise=p.selling_price_paise))
    db.add(order)
    db.flush()
    audit(db, salesman, "order_submit", "order", order.id, ref=order.number, customer=customer.name,
          lines=[{"product": l.product.code, "qty": l.qty} for l in order.lines], offline=bool(taken_at))
    return order


def edit_order(db: Session, user, order: Order, changes: dict[int, dict]):
    """changes: {order_line_id: {'qty': int, 'price_paise': int}} - logged before/after."""
    if order.status not in ("submitted", "approved"):
        raise BusinessError(f"Order is {order.status}; it can no longer be edited")
    diff = []
    for ln in list(order.lines):
        ch = changes.get(ln.id)
        if not ch:
            continue
        before = {"qty": ln.qty, "price_paise": ln.price_paise}
        qty = int(ch.get("qty", ln.qty))
        price = int(ch.get("price_paise", ln.price_paise))
        if qty < 0 or price < 0:
            raise BusinessError("Negative values are not allowed")
        if qty == 0:
            order.lines.remove(ln)
        else:
            ln.qty, ln.price_paise = qty, price
        if before != {"qty": qty, "price_paise": price}:
            diff.append({"product": ln.product.code, "before": before, "after": {"qty": qty, "price_paise": price}})
    if not order.lines:
        raise BusinessError("An order needs at least one line; reject it instead")
    if diff:
        audit(db, user, "order_edit", "order", order.id, ref=order.number, changes=diff)


def reserved(db: Session, product_id: int) -> int:
    return db.scalar(select(func.coalesce(func.sum(InvoiceLine.qty), 0)).join(Invoice)
                     .where(InvoiceLine.product_id == product_id, Invoice.status == "posted"))


def stock_check(db: Session, order: Order) -> list[dict]:
    rows = []
    for ln in order.lines:
        on_hand = stock.balance(db, ln.product_id)
        res = reserved(db, ln.product_id)
        avail = on_hand - res
        short = max(0, ln.qty - avail)
        rows.append({"line": ln, "product": ln.product, "ordered": ln.qty, "on_hand": on_hand, "reserved": res,
                     "available": avail, "shortage": short, "ok": short == 0,
                     "batches_needed": batches_needed(db, ln.product, short) if short else 0})
    return rows


def approve(db: Session, user, order: Order):
    if order.status != "submitted":
        raise BusinessError(f"Order is {order.status}")
    order.status = "approved"
    order.approved_by, order.approved_at = user.id, now()
    short = [r for r in stock_check(db, order) if r["shortage"]]
    audit(db, user, "order_approve", "order", order.id, ref=order.number,
          shortage={r["product"].code: r["shortage"] for r in short})


def reject(db: Session, user, order: Order, reason: str):
    if order.status not in ("submitted", "approved"):
        raise BusinessError(f"Order is {order.status}")
    order.status, order.reject_reason = "rejected", reason or "—"
    audit(db, user, "order_reject", "order", order.id, ref=order.number, reason=reason)


# --------------------------------------------------------------------------- invoices

def tax_preview(db: Session, order: Order) -> dict:
    """What the invoice would look like (used to show IGST for an out-of-state order before billing)."""
    kind = supply_type(setting(db, "company_state_code"), order.customer.state_code)
    lines, tot = [], {"taxable": 0, "cgst": 0, "sgst": 0, "igst": 0}
    for ln in order.lines:
        taxable = ln.qty * ln.price_paise
        c, s, i = split_tax(taxable, ln.product.gst_rate_bp, kind)
        lines.append({"line": ln, "taxable": taxable, "cgst": c, "sgst": s, "igst": i})
        for k, v in (("taxable", taxable), ("cgst", c), ("sgst", s), ("igst", i)):
            tot[k] += v
    tot["total"] = tot["taxable"] + tot["cgst"] + tot["sgst"] + tot["igst"]
    return {"supply_type": kind, "lines": lines, **tot}


def raise_invoice(db: Session, user, order: Order, on: date | None = None) -> Invoice:
    if order.status != "approved":
        raise BusinessError(f"Only approved orders can be invoiced (this one is {order.status})")
    short = [r for r in stock_check(db, order) if r["shortage"]]
    if short:
        raise ShortageError(short)
    on = on or date.today()
    prev = tax_preview(db, order)
    c = order.customer
    inv = Invoice(number=next_number(db, "INV", on), fy=fy_of(on), date=on, order_id=order.id, customer_id=c.id,
                  customer=c, customer_gstin=c.gstin, place_of_supply=c.state_code, supply_type=prev["supply_type"],
                  taxable_paise=prev["taxable"], cgst_paise=prev["cgst"], sgst_paise=prev["sgst"],
                  igst_paise=prev["igst"], total_paise=prev["total"], status="posted", created_by=user.id)
    for pl in prev["lines"]:
        ln = pl["line"]
        inv.lines.append(InvoiceLine(product_id=ln.product_id, product=ln.product, hsn=ln.product.hsn, qty=ln.qty,
                                     price_paise=ln.price_paise, taxable_paise=pl["taxable"],
                                     gst_rate_bp=ln.product.gst_rate_bp, cgst_paise=pl["cgst"],
                                     sgst_paise=pl["sgst"], igst_paise=pl["igst"]))
    db.add(inv)
    order.status = "invoiced"
    db.flush()
    audit(db, user, "invoice_post", "invoice", inv.id, ref=inv.number, order=order.number,
          total_paise=inv.total_paise, supply_type=inv.supply_type)
    return inv


def dispatch(db: Session, user, inv: Invoice, vehicle_no: str | None = None, on: date | None = None):
    """Goods leave the factory: FG stock goes down, oldest-expiry batch first."""
    if inv.status != "posted":
        raise BusinessError(f"Invoice is {inv.status}")
    on = on or date.today()
    for ln in inv.lines:
        stock.issue_fefo(db, item=ln.product, qty=ln.qty, kind="sale_dispatch", source_type="invoice",
                         source_id=inv.id, source_ref=inv.number, on=on, user=user)
    inv.status, inv.dispatched_at, inv.vehicle_no = "dispatched", now(), vehicle_no
    if inv.order:
        inv.order.status = "dispatched"
    audit(db, user, "invoice_dispatch", "invoice", inv.id, ref=inv.number, vehicle=vehicle_no)


def dispatch_batches(db: Session, inv: Invoice) -> list[dict]:
    rows = db.scalars(select(StockMovement).where(StockMovement.source_type == "invoice",
                                                  StockMovement.source_id == inv.id)).all()
    return [{"item": m.item, "batch_no": m.batch_no, "expiry": m.expiry, "qty": -m.qty} for m in rows]


def cancel_invoice(db: Session, user, inv: Invoice, reason: str, on: date | None = None) -> CreditNote:
    """Posted invoices are never deleted: issue a full credit note and bring dispatched goods back."""
    if inv.status == "cancelled":
        raise BusinessError("Invoice already cancelled")
    if not reason:
        raise BusinessError("A credit note needs a reason")
    on = on or date.today()
    cn = CreditNote(number=next_number(db, "CN", on), date=on, invoice_id=inv.id, invoice=inv, reason=reason,
                    taxable_paise=inv.taxable_paise, cgst_paise=inv.cgst_paise, sgst_paise=inv.sgst_paise,
                    igst_paise=inv.igst_paise, total_paise=inv.total_paise, created_by=user.id)
    db.add(cn)
    db.flush()
    if inv.status == "dispatched":
        moves = db.scalars(select(StockMovement).where(StockMovement.source_type == "invoice",
                                                       StockMovement.source_id == inv.id)).all()
        for m in moves:
            stock.post(db, item=m.item, qty=-m.qty, value_paise=-m.value_paise, kind="sales_return",
                       source_type="credit_note", source_id=cn.id, source_ref=cn.number, on=on,
                       batch_no=m.batch_no, expiry=m.expiry, user=user, note=reason)
        cn.stock_returned = True
    inv.status = "cancelled"
    if inv.order:
        inv.order.status = "cancelled"
    audit(db, user, "invoice_cancel", "invoice", inv.id, ref=inv.number, credit_note=cn.number, reason=reason)
    return cn


# --------------------------------------------------------------------------- money in

def record_payment(db: Session, user, customer: Customer, amount_paise: int, invoice: Invoice | None = None,
                   mode: str = "upi", reference: str | None = None, on: date | None = None) -> Payment:
    if amount_paise <= 0:
        raise BusinessError("Amount must be positive")
    if invoice and invoice.customer_id != customer.id:
        raise BusinessError("That invoice belongs to another customer")
    on = on or date.today()
    p = Payment(number=next_number(db, "RCT", on), date=on, customer_id=customer.id, customer=customer,
                invoice_id=invoice.id if invoice else None, invoice=invoice, amount_paise=amount_paise, mode=mode,
                reference=reference, user_id=user.id)
    db.add(p)
    db.flush()
    audit(db, user, "payment", "payment", p.id, ref=p.number, customer=customer.name, amount_paise=amount_paise,
          invoice=invoice.number if invoice else None)
    return p


def open_items(db: Session, customer_id: int, as_of: date | None = None) -> list[dict]:
    """Each live invoice with what is still due. Payments tagged to an invoice settle it;
    untagged ('on account') payments settle the oldest invoices first."""
    as_of = as_of or date.max
    invs = db.scalars(select(Invoice).where(Invoice.customer_id == customer_id, Invoice.date <= as_of,
                                            Invoice.status != "cancelled").order_by(Invoice.date, Invoice.id)).all()
    pays = db.scalars(select(Payment).where(Payment.customer_id == customer_id, Payment.date <= as_of)).all()
    due = {i.id: i.total_paise for i in invs}
    loose = 0
    for p in pays:
        if p.invoice_id in due:
            take = min(due[p.invoice_id], p.amount_paise)
            due[p.invoice_id] -= take
            loose += p.amount_paise - take
        else:
            loose += p.amount_paise
    for i in invs:
        take = min(due[i.id], loose)
        due[i.id] -= take
        loose -= take
    return [{"invoice": i, "due": due[i.id]} for i in invs] + ([{"invoice": None, "due": -loose}] if loose else [])


def outstanding(db: Session, customer_id: int, as_of: date | None = None) -> int:
    return sum(r["due"] for r in open_items(db, customer_id, as_of))


def ageing(db: Session, customer_id: int, as_of: date) -> dict:
    b = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0, "advance": 0}
    for r in open_items(db, customer_id, as_of):
        if r["invoice"] is None:
            b["advance"] += r["due"]
            continue
        days = (as_of - r["invoice"].date).days
        key = "0-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        b[key] += r["due"]
    b["total"] = sum(v for k, v in b.items())
    return b

