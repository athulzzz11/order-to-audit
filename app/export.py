"""Export any Report to CSV, Excel or PDF."""
import csv
import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.money import fmt, rupees
from app.units import fmt_num


def cell_text(v, kind) -> str:
    """Human formatting for HTML and PDF."""
    if v is None:
        return ""
    if kind == "money":
        return fmt(v, symbol=False)
    if kind in ("qty", "pct"):
        return fmt_num(Decimal(v)) if isinstance(v, Decimal) else str(v)
    if kind == "date" and isinstance(v, date):
        return v.strftime("%d-%m-%Y")
    return str(v)


def cell_value(v, kind):
    """Machine value for CSV / Excel: rupees as Decimal, quantities as numbers."""
    if v is None:
        return ""
    if kind == "money":
        return rupees(v)
    if kind in ("qty", "pct"):
        return Decimal(v)
    return v


def _all_rows(rep):
    rows = list(rep.rows)
    if rep.totals:
        rows.append(rep.totals)
    return rows


def to_csv(rep, period: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([rep.title, period])
    w.writerow([c for c, _ in rep.columns])
    for r in _all_rows(rep):
        w.writerow([cell_value(v, k) for v, (_, k) in zip(r, rep.columns)])
    if rep.note:
        w.writerow([])
        w.writerow([rep.note])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows ₹ and Tamil correctly


def to_xlsx(rep, period: str, company: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "".join(ch for ch in rep.title if ch not in "[]:*?/\\")[:31]
    ws.append([company])
    ws.append([rep.title, period])
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"].font = Font(bold=True)
    ws.append([c for c, _ in rep.columns])
    head = ws.max_row
    for c in ws[head]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="7A3E12")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in _all_rows(rep):
        ws.append([float(x) if isinstance(x, Decimal) else x for x in
                   (cell_value(v, k) for v, (_, k) in zip(r, rep.columns))])
        for i, (_, k) in enumerate(rep.columns, start=1):
            cell = ws.cell(row=ws.max_row, column=i)
            if k == "money":
                cell.number_format = "#,##0.00"
            elif k == "date":
                cell.number_format = "dd-mm-yyyy"
    if rep.totals:
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    if rep.note:
        ws.append([])
        ws.append([rep.note])
    for i, (label, _) in enumerate(rep.columns, start=1):
        width = max([len(label)] + [len(cell_text(r[i - 1], rep.columns[i - 1][1])) for r in _all_rows(rep)])
        ws.column_dimensions[get_column_letter(i)].width = min(45, max(10, width + 2))
    ws.freeze_panes = ws.cell(row=head + 1, column=1)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_pdf(rep, period: str, company: str) -> bytes:
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=rep.title)
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=7, leading=8.5)
    head = small.clone("head", textColor=colors.white, fontName="Helvetica-Bold")
    story = [Paragraph(f"<b>{company}</b>", ss["Heading3"]),
             Paragraph(f"{rep.title} &nbsp; | &nbsp; {period}", ss["Heading4"])]
    data = [[Paragraph(c, head) for c, _ in rep.columns]]
    for r in _all_rows(rep):
        data.append([Paragraph(cell_text(v, k).replace("&", "&amp;").replace("<", "&lt;"), small)
                     for v, (_, k) in zip(r, rep.columns)])
    t = Table(data, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7A3E12")),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBF4EC")])]
    for i, (_, k) in enumerate(rep.columns):
        if k in ("money", "qty", "int", "pct"):
            style.append(("ALIGN", (i, 1), (i, -1), "RIGHT"))
    if rep.totals:
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    story.append(t)
    if rep.note:
        story += [Spacer(1, 4 * mm), Paragraph(rep.note, small)]
    if not rep.rows:
        story.append(Paragraph("No entries in this period.", ss["BodyText"]))
    doc.build(story)
    return out.getvalue()
