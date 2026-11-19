from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import HOME, verify_password
from app.db import get_db
from app.models import User
from app.seed import USERS
from app.web.deps import go, render

router = APIRouter()


@router.get("/login")
def login_page(request: Request, next: str = ""):
    return render(request, "login.html", None, next=next, demo_users=USERS)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(""),
          db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == username.strip()))
    if not u or not u.active or not verify_password(password, u.password_hash):
        return render(request, "login.html", None, next=next, demo_users=USERS, error="Wrong username or password")
    request.session.clear()
    request.session["uid"] = u.id
    return go(next if next.startswith("/") and not next.startswith("//") else HOME[u.role])


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    u = db.get(User, uid) if uid else None
    return go(HOME[u.role] if u else "/login")
