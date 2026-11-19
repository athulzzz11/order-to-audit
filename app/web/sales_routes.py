"""Admin desk: verify orders, raise GST invoices, dispatch, credit notes, payments."""
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.invoice_pdf import invoice_pdf
from app.models import CreditNote, Customer, Invoice, Order, Payment
from app.money import fmt, to_paise
from app.services import sales
from app.services.common import BusinessError
from app.web.deps import area, back, flash, go, render

router = APIRouter()
signer = URLSafeSerializer(os.environ.get("SECRET_KEY", "dev-secret-change-me"), salt="invoice-share")


def _do(request, db, fn, ok_msg, fallback="/orders"):
    """Run a business action in one transaction; show its error instead of a stack trace."""
    try:
        result = fn()
        db.commit()
        if ok_msg:
            flash(request, ok_msg(result) if callable(ok_msg) else ok_msg)
        return result
    except (BusinessError, ValueError) as e:
        db.rollback()
        flash(request, str(e), "error")
        return None


# --------------------------------------------------------------------------- orders

@router.get("/orders")
def orders(request: Request, status: str = "open", user=Depends(area("orders")), db: Session = Depends(get_db)):
    q = select(Order).order_by(Order.id.desc())
    if status == "open":
        q = q.where(Order.status.in_(("submitted", "approved", "invoiced")))
    elif status != "all":
        q = q.where(Order.status == status)
    rows = db.scalars(q).all()
    return render(request, "orders.html", user, orders=rows, status=status,
                  checks={o.id: sales.stock_check(db, o) for o in rows if o.status in ("submitted", "approved")})


@router.get("/orders/{oid}")
def order_detail(oid: int, request: Request, user=Depends(area("orders")), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    return render(request, "order.html", user, o=o, check=sales.stock_check(db, o), preview=sales.tax_preview(db, o))


@router.post("/orders/{oid}/edit")
async def order_edit(oid: int, request: Request, user=Depends(area("orders", True)), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    form = await request.form()
    changes = {}
    try:
        for ln in o.lines:
            changes[ln.id] = {"qty": int(form.get(f"qty_{ln.id}", ln.qty)),
                              "price_paise": to_paise(form.get(f"price_{ln.id}", ln.price_paise / 100))}
    except ValueError as e:
        flash(request, f"Bad number: {e}", "error")
        return back(request)
    _do(request, db, lambda: sales.edit_order(db, user, o, changes), "Order updated (change logged)")
    return go(f"/orders/{oid}")


@router.post("/orders/{oid}/approve")
def order_approve(oid: int, request: Request, user=Depends(area("orders", True)), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    _do(request, db, lambda: sales.approve(db, user, o), f"{o.number} approved")
    return go(f"/orders/{oid}")


@router.post("/orders/{oid}/reject")
def order_reject(oid: int, request: Request, reason: str = Form(""), user=Depends(area("orders", True)),
                 db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    _do(request, db, lambda: sales.reject(db, user, o, reason), f"{o.number} rejected")
    return go(f"/orders/{oid}")


@router.post("/orders/{oid}/invoice")
def order_invoice(oid: int, request: Request, user=Depends(area("orders", True)), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    inv = _do(request, db, lambda: sales.raise_invoice(db, user, o), lambda i: f"Invoice {i.number} raised")
    return go(f"/invoices/{inv.id}" if inv else f"/orders/{oid}")


# --------------------------------------------------------------------------- invoices

@router.get("/invoices")
def invoices(request: Request, user=Depends(area("orders")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Invoice).order_by(Invoice.id.desc())).all()
    cns = db.scalars(select(CreditNote).order_by(CreditNote.id.desc())).all()
    return render(request, "invoices.html", user, invoices=rows, credit_notes=cns)


@router.get("/invoices/{iid}")
def invoice_detail(iid: int, request: Request, user=Depends(area("orders")), db: Session = Depends(get_db)):
    inv = db.get(Invoice, iid)
    cn = db.scalar(select(CreditNote).where(CreditNote.invoice_id == iid))
    pays = db.scalars(select(Payment).where(Payment.invoice_id == iid)).all()
    due = next((r["due"] for r in sales.open_items(db, inv.customer_id) if r["invoice"] is not None
                and r["invoice"].id == iid), 0)
    link = str(request.base_url).rstrip("/") + f"/i/{signer.dumps(inv.id)}.pdf"
    msg = (f"Dear {inv.customer.name}, your invoice {inv.number} dated {inv.date:%d-%m-%Y} for {fmt(inv.total_paise)} "
           f"is ready: {link}")
    phone = "91" + (inv.customer.phone or "")[-10:] if inv.customer.phone else ""
    return render(request, "invoice.html", user, inv=inv, cn=cn, payments=pays, due=due,
                  batches=sales.dispatch_batches(db, inv), share_link=link,
                  wa=f"https://wa.me/{phone}?text={quote(msg)}",
                  mail=f"mailto:?subject={quote('Invoice ' + inv.number)}&body={quote(msg)}")


@router.post("/invoices/{iid}/dispatch")
def invoice_dispatch(iid: int, request: Request, vehicle_no: str = Form(""), user=Depends(area("orders", True)),
                     db: Session = Depends(get_db)):
    inv = db.get(Invoice, iid)
    _do(request, db, lambda: sales.dispatch(db, user, inv, vehicle_no or None),
        f"{inv.number} dispatched - finished-goods stock reduced (oldest batch first)")
    return go(f"/invoices/{iid}")


@router.post("/invoices/{iid}/cancel")
def invoice_cancel(iid: int, request: Request, reason: str = Form(""), user=Depends(area("orders", True)),
                   db: Session = Depends(get_db)):
    inv = db.get(Invoice, iid)
    _do(request, db, lambda: sales.cancel_invoice(db, user, inv, reason),
        lambda cn: f"Credit note {cn.number} issued" + (" and stock returned" if cn.stock_returned else ""))
    return go(f"/invoices/{iid}")


def _pdf(db, inv, cn=None):
    data = invoice_pdf(db, inv, cn)
    name = (cn.number if cn else inv.number).replace("/", "-")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})


@router.get("/invoices/{iid}/pdf")
def invoice_pdf_view(iid: int, user=Depends(area("orders")), db: Session = Depends(get_db)):
    return _pdf(db, db.get(Invoice, iid))


@router.get("/credit-notes/{cid}/pdf")
def cn_pdf_view(cid: int, user=Depends(area("orders")), db: Session = Depends(get_db)):
    cn = db.get(CreditNote, cid)
    return _pdf(db, cn.invoice, cn)


@router.get("/i/{token}.pdf")
def shared_pdf(token: str, db: Session = Depends(get_db)):
    """Signed link for the customer (WhatsApp/email) - no login needed, cannot be guessed."""
    try:
        iid = signer.loads(token)
    except BadSignature:
        return Response("Invalid link", status_code=404)
    return _pdf(db, db.get(Invoice, iid))


# --------------------------------------------------------------------------- payments

@router.get("/payments")
def payments(request: Request, customer_id: int | None = None, user=Depends(area("orders")),
             db: Session = Depends(get_db)):
    custs = db.scalars(select(Customer).order_by(Customer.name)).all()
    summary = [{"c": c, "outstanding": sales.outstanding(db, c.id), "open": sales.open_items(db, c.id)} for c in custs]
    pays = db.scalars(select(Payment).order_by(Payment.id.desc()).limit(100)).all()
    return render(request, "payments.html", user, customers=custs, summary=summary, payments=pays,
                  selected=customer_id)


@router.post("/payments")
def payment_create(request: Request, customer_id: int = Form(...), amount: str = Form(...),
                   invoice_id: str = Form(""), mode: str = Form("upi"), reference: str = Form(""),
                   user=Depends(area("orders", True)), db: Session = Depends(get_db)):
    c = db.get(Customer, customer_id)
    inv = db.get(Invoice, int(invoice_id)) if invoice_id else None
    try:
        amt = to_paise(amount)
    except ValueError as e:
        flash(request, str(e), "error")
        return back(request)
    _do(request, db, lambda: sales.record_payment(db, user, c, amt, inv, mode, reference or None),
        lambda p: f"Receipt {p.number}: {fmt(p.amount_paise)} from {c.name}. "
                  f"Outstanding now {fmt(sales.outstanding(db, c.id))}")
    return go("/payments")
