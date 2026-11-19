"""Salesman mobile app (offline-first PWA) and its small JSON API."""
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Invoice, Item, Order, User
from app.money import fmt
from app.services import sales
from app.services.common import BusinessError
from app.web.deps import Forbidden, current_user, render

router = APIRouter()
STATIC = Path(__file__).parent.parent / "static"


def salesman(user: User = Depends(current_user)) -> User:
    if user.role != "salesman":
        raise Forbidden()
    return user


@router.get("/m/")
def app_shell(request: Request, user: User = Depends(salesman)):
    return render(request, "mobile.html", user)


@router.get("/m/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/m/manifest.json")
def manifest():
    return FileResponse(STATIC / "manifest.json", media_type="application/manifest+json")


def _order_json(o: Order) -> dict:
    return {"id": o.id, "number": o.number, "client_uuid": o.client_uuid, "customer": o.customer.name,
            "status": o.status, "taken_at": o.taken_at.isoformat(timespec="minutes"),
            "invoice": o.invoice.number if o.invoice else None,
            "total": fmt(o.invoice.total_paise) if o.invoice else fmt(sum(l.qty * l.price_paise for l in o.lines)),
            "lines": [{"product": l.product.name, "qty": l.qty} for l in o.lines],
            "reject_reason": o.reject_reason}


@router.get("/api/m/bootstrap")
def bootstrap(user: User = Depends(salesman), db: Session = Depends(get_db)):
    """Everything the phone needs to work offline: my customers, products, my orders, my sales."""
    custs = db.scalars(select(Customer).where(Customer.salesman_id == user.id).order_by(Customer.name)).all()
    prods = db.scalars(select(Item).where(Item.kind == "finished", Item.active.is_(True)).order_by(Item.name)).all()
    orders = db.scalars(select(Order).where(Order.salesman_id == user.id).order_by(Order.id.desc()).limit(50)).all()
    start = date.today().replace(day=1)
    by_cust = []
    for c in custs:
        invs = db.scalars(select(Invoice).where(Invoice.customer_id == c.id, Invoice.date >= start,
                                                Invoice.status != "cancelled")).all()
        by_cust.append({"customer": c.name, "invoices": len(invs),
                        "packets": sum(l.qty for i in invs for l in i.lines),
                        "sales": fmt(sum(i.total_paise for i in invs)),
                        "outstanding": fmt(sales.outstanding(db, c.id))})
    return {
        "user": {"name": user.name, "username": user.username},
        "customers": [{"id": c.id, "name": c.name, "kind": c.kind, "state": c.state_name} for c in custs],
        "products": [{"id": p.id, "name": p.name, "price": p.selling_price_paise, "price_fmt": fmt(p.selling_price_paise),
                      "per_carton": p.pack_per_carton} for p in prods],
        "orders": [_order_json(o) for o in orders],
        "sales_this_month": by_cust,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


class LineIn(BaseModel):
    product_id: int
    qty: float
    unit: str = "packet"


class OrderIn(BaseModel):
    client_uuid: str
    customer_id: int
    lines: list[LineIn]
    note: str | None = None
    taken_at: datetime | None = None


@router.post("/api/m/orders")
def submit_order(body: OrderIn, user: User = Depends(salesman), db: Session = Depends(get_db)):
    """Idempotent: the phone may resend the same order (same client_uuid) after a dropped connection."""
    c = db.get(Customer, body.customer_id)
    if not c or c.salesman_id != user.id:
        return JSONResponse({"error": "Not your customer"}, status_code=400)
    try:
        o = sales.create_order(db, user, c, [{"product": l.product_id, "qty": str(l.qty).removesuffix(".0"),
                                              "unit": l.unit} for l in body.lines],
                               client_uuid=body.client_uuid, note=body.note,
                               taken_at=body.taken_at.replace(tzinfo=None) if body.taken_at else None)
        db.commit()
    except BusinessError as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    return _order_json(o)
