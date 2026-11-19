"""Reports, owner dashboard, audit log and the in-app benchmark check."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import benchmark
from app.db import get_db
from app.export import to_csv, to_pdf, to_xlsx
from app.models import Customer, Invoice, Item, Order, ProductionRun
from app.services import costing, production, reports, sales, stock
from app.services.common import BusinessError, setting
from app.web.deps import area, flash, go, render

router = APIRouter()


def _period(start: str, end: str):
    s = date.fromisoformat(start) if start else date.today().replace(day=1)
    e = date.fromisoformat(end) if end else date.today()
    return s, e


@router.get("/reports")
def index(request: Request, start: str = "", end: str = "", user=Depends(area("reports")),
          db: Session = Depends(get_db)):
    s, e = _period(start, end)
    return render(request, "reports.html", user, reports=reports.REPORTS, start=s, end=e)


@router.get("/reports/{key}")
def show(key: str, request: Request, start: str = "", end: str = "", format: str = "html", item_id: int | None = None,
         user=Depends(area("reports")), db: Session = Depends(get_db)):
    s, e = _period(start, end)
    _, fn = reports.REPORTS[key]
    rep = fn(db, s, e, item_id) if key == "stock_ledger" else fn(db, s, e)
    period = f"{s:%d-%m-%Y} to {e:%d-%m-%Y}"
    company = f"{setting(db, 'company_name')} | GSTIN {setting(db, 'company_gstin')}"
    fname = f"{key}_{s:%Y%m%d}_{e:%Y%m%d}"
    if format == "csv":
        return Response(to_csv(rep, period), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    if format == "xlsx":
        return Response(to_xlsx(rep, period, company),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
    if format == "pdf":
        return Response(to_pdf(rep, period, company), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'})
    items = db.scalars(select(Item).order_by(Item.kind.desc(), Item.code)).all() if key == "stock_ledger" else None
    return render(request, "report.html", user, rep=rep, start=s, end=e, key=key, reports=reports.REPORTS,
                  items=items, item_id=item_id)


@router.get("/audit")
def audit_log(request: Request, start: str = "", end: str = "", user=Depends(area("audit")),
              db: Session = Depends(get_db)):
    s, e = _period(start, end)
    return render(request, "report.html", user, rep=reports.audit_trail(db, s, e), start=s, end=e, key="audit_trail",
                  reports=reports.REPORTS)


@router.get("/dashboard")
def dashboard(request: Request, user=Depends(area("dashboard")), db: Session = Depends(get_db)):
    today = date.today()
    start, end = costing.month_bounds(today)
    t0 = datetime.combine(today, datetime.min.time())
    todays = db.scalars(select(Order).where(Order.received_at >= t0)).all()
    pending = db.scalar(select(func.count(Order.id)).where(Order.status.in_(("submitted", "approved"))))
    month_sales = db.scalar(select(func.coalesce(func.sum(Invoice.taxable_paise), 0))
                            .where(Invoice.date.between(start, end), Invoice.status != "cancelled"))
    items = db.scalars(select(Item).order_by(Item.kind.desc(), Item.code)).all()
    stock_rows = [{"item": i, "bal": stock.balance(db, i.id), "low": stock.balance(db, i.id) < i.reorder_level,
                   "reserved": sales.reserved(db, i.id) if i.kind == "finished" else 0} for i in items]
    alerts = []
    for r in db.scalars(select(ProductionRun).where(ProductionRun.date.between(start, end),
                                                    ProductionRun.status == "completed")):
        v = production.variance(db, r)
        for l in v["lines"]:
            if l["flagged"]:
                alerts.append({"run": r, "what": l["item"].name, "pct": l["pct"]})
        if v["yield"] and v["yield"]["flagged"]:
            alerts.append({"run": r, "what": "Yield", "pct": v["yield"]["pct"]})
    outs = sorted(({"c": c, "a": sales.ageing(db, c.id, today)} for c in db.scalars(select(Customer))),
                  key=lambda x: -x["a"]["total"])
    shortages = []
    for o in db.scalars(select(Order).where(Order.status.in_(("submitted", "approved")))):
        for r in sales.stock_check(db, o):
            if r["shortage"]:
                shortages.append({"order": o, **r})
    return render(request, "dashboard.html", user, todays=todays, pending=pending, month_sales=month_sales,
                  stock_rows=stock_rows, alerts=alerts, costs=costing.product_costs(db, start, end),
                  outs=[x for x in outs if x["a"]["total"]], total_out=sum(x["a"]["total"] for x in outs),
                  shortages=shortages)


@router.get("/benchmark")
def bench(request: Request, user=Depends(area("benchmark")), db: Session = Depends(get_db)):
    rows = benchmark.checks(db)
    return render(request, "benchmark.html", user, rows=rows, passed=sum(r["ok"] for r in rows))


@router.post("/benchmark/reset")
def bench_reset(request: Request, user=Depends(area("dashboard")), db: Session = Depends(get_db)):
    """Wipe everything and reload section 5 master data, so the demo can be re-run by hand."""
    from app import db as dbm
    from app.seed import seed
    db.close()
    dbm.drop_all()
    dbm.create_all()
    s = dbm.SessionLocal()
    try:
        users = seed(s)
        request.session["uid"] = users[user.username].id
    finally:
        s.close()
    flash(request, "Demo data reset to the section 5 opening position")
    return go("/benchmark")


@router.post("/benchmark/run")
def bench_run(request: Request, user=Depends(area("dashboard")), db: Session = Depends(get_db)):
    """Play all 9 steps automatically on the current data (use right after a reset)."""
    log = []
    try:
        benchmark.run_scenario(db, log=log.append)
        flash(request, "Scenario played: " + " | ".join(log[:8]))
    except BusinessError as e:
        db.rollback()
        flash(request, f"Could not run (reset first?): {e}", "error")
    return go("/benchmark")
