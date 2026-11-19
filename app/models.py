"""Data model.

Conventions
-----------
* Quantities are integers in the item's *base unit*: grams (mass), millilitres
  (volume) or pieces (count). 40 kg flour is stored as 40000, 10.5 L oil as 10500.
* Money is integer paise. Rs 45.00 is 4500.
* Rates (GST, tolerance) are integer basis points. 5% is 500.
* Stock is never stored as a number on the item. The only source of truth is
  StockMovement; every balance is SUM(qty) over that ledger.
"""
import datetime as dt

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

BASE_DIMENSION = {"g": "mass", "ml": "volume", "pcs": "count"}


def now():
    return dt.datetime.now()


# --------------------------------------------------------------------------- people

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))  # salesman|admin|production|accountant|owner
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="retailer")  # retailer|distributor
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str] = mapped_column(String(2))  # GST state code, e.g. 33 = Tamil Nadu
    state_name: Mapped[str] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    salesman_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    credit_days: Mapped[int] = mapped_column(Integer, default=30)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str] = mapped_column(String(2), default="33")
    phone: Mapped[str | None] = mapped_column(String(20))


# --------------------------------------------------------------------------- items & recipes

class Item(Base):
    """Anything that has a stock balance: raw material, packaging or finished product."""
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))  # raw|packaging|finished
    base_unit: Mapped[str] = mapped_column(String(4))  # g|ml|pcs  (storage unit)
    display_unit: Mapped[str] = mapped_column(String(10))  # kg|l|pcs|packet (UI/report unit)
    reorder_level: Mapped[int] = mapped_column(BigInteger, default=0)  # base units
    # finished goods only
    hsn: Mapped[str | None] = mapped_column(String(8))
    gst_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    selling_price_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    pack_per_carton: Mapped[int | None] = mapped_column(Integer)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    batch_code: Mapped[str | None] = mapped_column(String(6))  # prefix for production batch numbers
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def dimension(self) -> str:
        return BASE_DIMENSION[self.base_unit]

    @property
    def tracks_batches(self) -> bool:
        return self.kind == "finished"


class Bom(Base):
    """Recipe for one batch of a finished product."""
    __tablename__ = "boms"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    batch_output: Mapped[int] = mapped_column(Integer)  # expected good packets per batch
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(200))
    product: Mapped[Item] = relationship()
    lines: Mapped[list["BomLine"]] = relationship(back_populates="bom", cascade="all, delete-orphan",
                                                  order_by="BomLine.id")


class BomLine(Base):
    __tablename__ = "bom_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_per_batch: Mapped[int] = mapped_column(BigInteger)  # base units
    # 'batch'  -> planned = qty_per_batch x batches        (ingredients: flour, oil...)
    # 'output' -> planned = qty_per_batch x good / yield   (packaging: one pouch per packet)
    basis: Mapped[str] = mapped_column(String(8), default="batch")
    bom: Mapped[Bom] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()


# --------------------------------------------------------------------------- the ledger

class StockMovement(Base):
    """One row per change in stock. Balance = SUM(qty); value = SUM(value_paise)."""
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    qty: Mapped[int] = mapped_column(BigInteger)  # signed, base units (+ in, - out)
    value_paise: Mapped[int] = mapped_column(BigInteger)  # signed, at weighted-average cost
    # opening|purchase|production_issue|production_output|sale_dispatch|sales_return|adjustment
    kind: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(20))  # opening|purchase|production|invoice|credit_note|adjustment
    source_id: Mapped[int | None] = mapped_column(Integer)
    source_ref: Mapped[str | None] = mapped_column(String(40))  # human document number
    batch_no: Mapped[str | None] = mapped_column(String(30))
    expiry: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    item: Mapped[Item] = relationship()


# --------------------------------------------------------------------------- purchases

class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    date: Mapped[dt.date] = mapped_column(Date)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    supplier_invoice: Mapped[str | None] = mapped_column(String(40))
    taxable_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    supplier: Mapped[Supplier] = relationship()
    lines: Mapped[list["PurchaseLine"]] = relationship(cascade="all, delete-orphan")


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(BigInteger)  # base units
    entered_qty: Mapped[str] = mapped_column(String(20))
    entered_unit: Mapped[str] = mapped_column(String(10))
    rate_paise: Mapped[int] = mapped_column(BigInteger)  # per entered unit
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    gst_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    batch_no: Mapped[str | None] = mapped_column(String(30))
    item: Mapped[Item] = relationship()


# --------------------------------------------------------------------------- production

class ProductionRun(Base):
    __tablename__ = "production_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    date: Mapped[dt.date] = mapped_column(Date)
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    batches_milli: Mapped[int] = mapped_column(Integer, default=1000)  # 1000 = 1 batch
    expected_output: Mapped[int] = mapped_column(Integer)
    good_output: Mapped[int | None] = mapped_column(Integer)
    scrap_note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="in_progress")  # in_progress|completed|cancelled
    batch_no: Mapped[str | None] = mapped_column(String(30))
    expiry: Mapped[dt.date | None] = mapped_column(Date)
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    product: Mapped[Item] = relationship()
    bom: Mapped[Bom] = relationship()
    lines: Mapped[list["ProductionLine"]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                         order_by="ProductionLine.id")


class ProductionLine(Base):
    """Actual material issued to a run. stock out = used + wasted."""
    __tablename__ = "production_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    used: Mapped[int] = mapped_column(BigInteger)
    wasted: Mapped[int] = mapped_column(BigInteger, default=0)
    value_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # cost of used+wasted
    stage: Mapped[str] = mapped_column(String(8), default="start")  # start|complete
    run: Mapped[ProductionRun] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()


class Overhead(Base):
    """Labour, gas/power etc. Tied to a run (run_id) or to a month (run_id NULL)."""
    __tablename__ = "overheads"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(20))  # labour|gas_power|other
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("production_runs.id"))
    note: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    run: Mapped[ProductionRun | None] = relationship()


# --------------------------------------------------------------------------- sales

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    client_uuid: Mapped[str | None] = mapped_column(String(40), unique=True)  # idempotency key for offline sync
    salesman_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String(12), default="submitted")
    # submitted|approved|rejected|invoiced|dispatched|cancelled
    taken_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)  # on the phone (maybe offline)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)  # reached the server
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String(200))
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    salesman: Mapped[User] = relationship(foreign_keys=[salesman_id])
    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                    order_by="OrderLine.id")
    invoice: Mapped["Invoice | None"] = relationship(back_populates="order", uselist=False)


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer)  # packets
    price_paise: Mapped[int] = mapped_column(BigInteger)  # per packet, before tax
    order: Mapped[Order] = relationship(back_populates="lines")
    product: Mapped[Item] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    fy: Mapped[str] = mapped_column(String(7))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    customer_gstin: Mapped[str | None] = mapped_column(String(15))  # snapshot at time of invoice
    place_of_supply: Mapped[str] = mapped_column(String(2))
    supply_type: Mapped[str] = mapped_column(String(5))  # intra|inter
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="posted")  # posted|dispatched|cancelled
    dispatched_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    order: Mapped[Order | None] = relationship(back_populates="invoice")
    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                      order_by="InvoiceLine.id")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    hsn: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    price_paise: Mapped[int] = mapped_column(BigInteger)
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    gst_rate_bp: Mapped[int] = mapped_column(Integer)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    product: Mapped[Item] = relationship()


class CreditNote(Base):
    """Posted invoices are never deleted; they are reversed with a credit note."""
    __tablename__ = "credit_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    reason: Mapped[str] = mapped_column(String(200))
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    stock_returned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    invoice: Mapped[Invoice] = relationship()


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))  # NULL = on account
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(12), default="upi")  # cash|upi|cheque|neft
    reference: Mapped[str | None] = mapped_column(String(60))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    customer: Mapped[Customer] = relationship()
    invoice: Mapped[Invoice | None] = relationship()


# --------------------------------------------------------------------------- housekeeping

class Sequence(Base):
    """Gapless document numbering, e.g. INV/2026-27 -> 1, 2, 3..."""
    __tablename__ = "sequences"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    ref: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)  # JSON
    user: Mapped[User | None] = relationship()

