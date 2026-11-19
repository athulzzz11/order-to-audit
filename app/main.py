"""FastAPI application: wires routers, sessions and error pages."""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app import db as dbm
from app.models import User
from app.web import auth_routes, mobile, production_routes, report_routes, sales_routes, stock_routes
from app.web.deps import Forbidden, LoginRequired, render


def create_app(seed_if_empty: bool = True) -> FastAPI:
    if dbm.engine is None:
        dbm.init()
    dbm.create_all()
    if seed_if_empty:
        s = dbm.SessionLocal()
        try:
            if not s.scalar(select(User.id).limit(1)):
                from app.seed import seed
                seed(s)
        finally:
            s.close()

    app = FastAPI(title="Order-to-Audit", docs_url="/api/docs")
    app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
                       max_age=60 * 60 * 24 * 30, same_site="lax")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    for r in (auth_routes, mobile, sales_routes, production_routes, stock_routes, report_routes):
        app.include_router(r.router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(Path(__file__).parent / "static" / "icon.svg", media_type="image/svg+xml")

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "login required"}, status_code=401)
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "not allowed for your role"}, status_code=403)
        return render(request, "error.html", None, message="Your role cannot open this page.", code=403,
                      status_code=403)

    return app


app = create_app() if os.environ.get("OTA_NO_AUTOAPP") != "1" else None
