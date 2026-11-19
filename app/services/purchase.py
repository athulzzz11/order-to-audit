"""Purchases: raw material / packaging receipts. Stock goes up at the taxable rate
(input GST is claimable as credit, so it is not part of stock cost)."""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Item, Purchase, PurchaseLine, Supplier
from app.money import round_paise, to_paise
from app.services import stock
from app.services.common import BusinessError, audit, next_number
from app.units import to_base


def record_purchase(db: Session, user, supplier: Supplier, lines: list[dict], on: date | None = None,
                    supplier_invoice: str | None = None) -> Purchase:
    """lines: [{'item': Item|id, 'qty': '60', 'unit': 'kg', 'rate': '45', 'gst_rate_bp': 0}]  rate = Rs per unit"""
    on = on or date.today()
    pur = Purchase(number=next_number(db, "PUR", on), date=on, supplier_id=supplier.id, supplier=supplier,
                   supplier_invoice=supplier_invoice, user_id=getattr(user, "id", None))
    db.add(pur)
    db.flush()
    for ln in lines:
        item = ln["item"] if isinstance(ln["item"], Item) else db.get(Item, int(ln["item"]))
        if item.kind == "finished":
            raise BusinessError("Finished goods are produced, not purchased")
        unit = ln.get("unit") or item.display_unit
        qty_dec = Decimal(str(ln["qty"]))
        if qty_dec <= 0:
            raise BusinessError("Quantity must be positive")
        qty = to_base(item, qty_dec, unit)
        rate = to_paise(ln["rate"])
        taxable = round_paise(qty_dec * rate)
        gst_bp = int(ln.get("gst_rate_bp") or 0)
        tax = round_paise(Decimal(taxable) * gst_bp / 10000)
        pur.lines.append(PurchaseLine(item_id=item.id, item=item, qty=qty, entered_qty=str(qty_dec),
                                      entered_unit=unit, rate_paise=rate, taxable_paise=taxable,
                                      gst_rate_bp=gst_bp, tax_paise=tax, batch_no=ln.get("batch_no")))
        stock.post(db, item=item, qty=qty, kind="purchase", source_type="purchase", source_id=pur.id,
                   source_ref=pur.number, on=on, value_paise=taxable, batch_no=ln.get("batch_no"), user=user)
    if not pur.lines:
        raise BusinessError("Add at least one line")
    pur.taxable_paise = sum(l.taxable_paise for l in pur.lines)
    pur.tax_paise = sum(l.tax_paise for l in pur.lines)
    audit(db, user, "purchase", "purchase", pur.id, ref=pur.number, supplier=supplier.name,
          lines=[{"item": l.item.code, "qty": l.qty, "rate_paise": l.rate_paise} for l in pur.lines])
    return pur
