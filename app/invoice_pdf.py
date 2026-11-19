"""GST tax invoice as PDF (reportlab)."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.money import fmt, in_words
from app.services.common import setting
from app.services.gst import STATES
from app.services.sales import dispatch_batches


def _r(p):
    return fmt(p, symbol=False)


def invoice_pdf(db, inv, credit_note=None) -> bytes:
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=inv.number)
    ss = getSampleStyleSheet()
    n = ss["BodyText"].clone("n", fontSize=8.5, leading=11)
    b = n.clone("b", fontName="Helvetica-Bold")
    title = "CREDIT NOTE" if credit_note else "TAX INVOICE"
    c = inv.customer
    pos = f"{inv.place_of_supply} - {STATES.get(inv.place_of_supply, c.state_name)}"
    story = [
        Table([[Paragraph(f"<font size=14><b>{setting(db, 'company_name')}</b></font><br/>"
                          f"{setting(db, 'company_address')}<br/>GSTIN: <b>{setting(db, 'company_gstin')}</b> &nbsp; "
                          f"State: {setting(db, 'company_state_name')} ({setting(db, 'company_state_code')}) &nbsp; "
                          f"Ph: {setting(db, 'company_phone')}", n),
                Paragraph(f"<font size=13><b>{title}</b></font><br/>"
                          + (f"No: <b>{credit_note.number}</b><br/>Date: {credit_note.date:%d-%m-%Y}<br/>"
                             f"Against invoice: {inv.number} dt {inv.date:%d-%m-%Y}<br/>Reason: {credit_note.reason}"
                             if credit_note else
                             f"Invoice no: <b>{inv.number}</b><br/>Date: {inv.date:%d-%m-%Y}<br/>"
                             f"Order: {inv.order.number if inv.order else '-'}<br/>Vehicle: {inv.vehicle_no or '-'}"),
                          n)]], colWidths=[118 * mm, 64 * mm]),
        Spacer(1, 3 * mm),
        Table([[Paragraph(f"<b>Bill to / Ship to</b><br/>{c.name}<br/>{c.address or ''}<br/>"
                          f"GSTIN: {inv.customer_gstin or 'Unregistered'}<br/>State: {c.state_name} ({c.state_code})", n),
                Paragraph(f"<b>Place of supply</b><br/>{pos}<br/><br/><b>Supply</b>: "
                          f"{'Intra-state (CGST + SGST)' if inv.supply_type == 'intra' else 'Inter-state (IGST)'}<br/>"
                          f"Reverse charge: No", n)]], colWidths=[118 * mm, 64 * mm],
              style=[("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey)]),
        Spacer(1, 3 * mm),
    ]
    intra = inv.supply_type == "intra"
    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Total"]
    rows = [[Paragraph(h, b) for h in head]]
    for i, l in enumerate(inv.lines, 1):
        rate = Decimal(l.gst_rate_bp) / 100
        tax = [f"{_r(l.cgst_paise)}\n@{rate / 2}%", f"{_r(l.sgst_paise)}\n@{rate / 2}%"] if intra else \
            [f"{_r(l.igst_paise)}\n@{rate}%"]
        rows.append([i, Paragraph(l.product.name, n), l.hsn, f"{l.qty} pkt", _r(l.price_paise), _r(l.taxable_paise),
                     *tax, _r(l.taxable_paise + l.cgst_paise + l.sgst_paise + l.igst_paise)])
    tot = ["", Paragraph("<b>Total</b>", n), "", f"{sum(l.qty for l in inv.lines)} pkt", "", _r(inv.taxable_paise)] + \
          ([_r(inv.cgst_paise), _r(inv.sgst_paise)] if intra else [_r(inv.igst_paise)]) + [_r(inv.total_paise)]
    rows.append(tot)
    widths = [7, 50, 18, 16, 16, 22] + ([19, 19] if intra else [24]) + [22]
    t = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3E3D3")),
                           ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 3 * mm),
              Paragraph(f"<b>Amount in words:</b> {in_words(inv.total_paise)}", n),
              Paragraph(f"<b>Grand total: Rs. {_r(inv.total_paise)}</b>", n)]
    bt = dispatch_batches(db, inv)
    if bt:
        story.append(Paragraph("<b>Batches dispatched:</b> " + "; ".join(
            f"{x['batch_no']} x {x['qty']} (exp {x['expiry']:%d-%m-%Y})" if x["expiry"] else f"{x['batch_no']} x {x['qty']}"
            for x in bt), n))
    story += [Spacer(1, 8 * mm),
              Table([[Paragraph("Declaration: We declare that this invoice shows the actual price of the goods "
                                "described and that all particulars are true and correct.", n),
                      Paragraph(f"For {setting(db, 'company_name')}<br/><br/><br/>Authorised signatory", n)]],
                    colWidths=[118 * mm, 64 * mm]),
              Paragraph("<i>Computer-generated document. Demo data only.</i>", n)]
    doc.build(story)
    return out.getvalue()
