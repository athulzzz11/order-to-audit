"""Shared web plumbing: current user, role checks, templates, flash messages."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import HOME, ROLES
from app.db import get_db
from app.export import cell_text
from app.models import User
from app.money import fmt
from app.units import fmt_num, fmt_qty, from_base

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# Who may open which area (read) and who may change things in it (write).
VIEW = {
    "orders": {"admin", "owner", "accountant"},
    "production": {"production", "admin", "owner", "accountant"},
    "purchases": {"production", "admin", "owner", "accountant"},
    "stock": {"production", "admin", "owner", "accountant"},
    "masters": {"admin", "production", "owner"},
    "reports": {"accountant", "owner", "admin"},
    "dashboard": {"owner", "admin"},
    "audit": {"accountant", "owner", "admin"},
    "benchmark": {"admin", "production", "accountant", "owner"},
}
WRITE = {
    "orders": {"admin"},
    "production": {"production"},
    "purchases": {"production", "admin"},
    "stock": {"production", "admin"},
    "masters": {"admin"},
    "bom": {"admin", "production"},
}

NAV = [  # (area, label, url)
    ("dashboard", "Dashboard", "/dashboard"),
    ("orders", "Orders", "/orders"),
    ("orders", "Invoices", "/invoices"),
    ("orders", "Payments", "/payments"),
    ("production", "Production", "/production"),
    ("purchases", "Purchases", "/purchases"),
    ("stock", "Stock", "/stock"),
    ("masters", "Masters", "/masters"),
    ("reports", "Reports", "/reports"),
    ("audit", "Audit log", "/audit"),
    ("benchmark", "Benchmark check", "/benchmark"),
]


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or not user.active:
        raise LoginRequired()
    return user


def area(name: str, write: bool = False):
    """Dependency: allow only roles that may view (or write) an area."""
    allowed = (WRITE if write else VIEW)[name]

    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise Forbidden()
        return user
    return dep


def can(user, name: str, write: bool = False) -> bool:
    return user is not None and user.role in (WRITE if write else VIEW).get(name, set())


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append([kind, msg])


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or fallback, status_code=303)


def go(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    msgs = request.session.pop("flash", [])
    nav = [(label, url) for a, label, url in NAV if can(user, a)] if user else []
    return templates.TemplateResponse(request, name, {"user": user, "nav": nav, "flashes": msgs,
                                                      "can": lambda a, w=False: can(user, a, w),
                                                      "role_name": ROLES.get(user.role) if user else "",
                                                      "today": date.today(), **ctx},
                                      status_code=status_code)


def _qty(v, item=None, unit=None):
    if v is None:
        return ""
    if item is not None:
        return fmt_qty(item, int(v), unit)
    return fmt_num(Decimal(v))


templates.env.filters.update(
    money=lambda p: fmt(p) if p is not None else "",
    qty=_qty,
    d=lambda d: d.strftime("%d %b %Y") if d else "",
    dt=lambda d: d.strftime("%d %b, %H:%M") if d else "",
    cell=cell_text,
    num=lambda v, item: fmt_num(from_base(item, int(v))),
    pct=lambda p: "" if p is None else f"{p:+}%",
)
templates.env.globals["HOME"] = HOME
