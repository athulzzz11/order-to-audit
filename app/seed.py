"""Load the section 5 master data.

    python -m app.seed            # wipe and seed (opening stock dated 1st of this month)
    python -m app.seed --date 2026-11-01
"""
import argparse
from datetime import date, timedelta

from app import db as dbm
from app.auth import hash_password
from app.models import Bom, BomLine, Customer, Item, Supplier, User
from app.money import to_paise
from app.services import stock
from app.services.common import DEFAULT_SETTINGS, set_setting
from app.units import to_base

PASSWORD = "demo123"

USERS = [
    ("salesA", "Salesman A (Ravi)", "salesman"),
    ("salesB", "Salesman B (Kumar)", "salesman"),
    ("admin", "Factory Admin (Meena)", "admin"),
    ("prod", "Production Supervisor (Selvam)", "production"),
    ("audit", "Accountant / Auditor (CA Firm)", "accountant"),
    ("owner", "Owner (Mr. Raghavan)", "owner"),
]

# code, name, kind, base, display, reorder (display units), opening qty, opening rate (Rs per display unit)
MATERIALS = [
    ("FLOUR", "Flour (rice/urad blend)", "raw", "g", "kg", 50, 100, "42"),
    ("OIL", "Cooking oil", "raw", "ml", "l", 15, 30, "140"),
    ("SPICE", "Salt and spice mix", "raw", "g", "kg", 2, 5, "200"),
    ("POUCH200", "200 g pouch", "packaging", "pcs", "pcs", 300, 500, "2.50"),
    ("CARTON20", "Carton (holds 20 packets)", "packaging", "pcs", "pcs", 20, 30, "15"),
]


def seed(db, opening_date: date | None = None):
    opening_date = opening_date or date.today().replace(day=1)
    for k, v in DEFAULT_SETTINGS.items():
        set_setting(db, k, v)
    users = {}
    for u, n, r in USERS:
        users[u] = User(username=u, name=n, role=r, password_hash=hash_password(PASSWORD))
        db.add(users[u])
    db.flush()

    db.add_all([
        Customer(name="Retailer A - Lakshmi Stores", kind="retailer", gstin="33AABCL0000B1Z2", state_code="33",
                 state_name="Tamil Nadu", address="Main Bazaar, Srirangam, Tiruchirappalli", phone="9000000001",
                 salesman_id=users["salesA"].id),
        Customer(name="Distributor B - Karnataka Foods", kind="distributor", gstin="29AABCK0000C1Z3",
                 state_code="29", state_name="Karnataka", address="Jayanagar, Bengaluru", phone="9000000002",
                 salesman_id=users["salesB"].id, credit_days=45),
        Customer(name="Retailer C - Murugan Bakery", kind="retailer", gstin=None, state_code="33",
                 state_name="Tamil Nadu", address="Thillai Nagar, Tiruchirappalli", phone="9000000003",
                 salesman_id=users["salesA"].id),
    ])
    db.add(Supplier(name="Sri Lakshmi Flour Mills", gstin="33AABCS0000D1Z4", state_code="33", phone="9000000010"))

    items = {}
    for code, name, kind, base, disp, reorder, *_ in MATERIALS:
        it = Item(code=code, name=name, kind=kind, base_unit=base, display_unit=disp)
        it.reorder_level = to_base(it, reorder, disp)
        items[code] = it
        db.add(it)
    mur = Item(code="MUR200", name="Murukku 200 g", kind="finished", base_unit="pcs", display_unit="packet",
               hsn="21069099", gst_rate_bp=500, selling_price_paise=to_paise("50"), pack_per_carton=20,
               shelf_life_days=90, batch_code="MUR", reorder_level=100)
    db.add(mur)
    db.flush()

    bom = Bom(product_id=mur.id, product=mur, batch_output=200, note="Standard batch: 40 kg flour -> 200 packets")
    for code, qty, unit, basis in [("FLOUR", 40, "kg", "batch"), ("OIL", 10, "l", "batch"),
                                   ("SPICE", "1.2", "kg", "batch"), ("POUCH200", 200, "pcs", "output"),
                                   ("CARTON20", 10, "pcs", "output")]:
        it = items[code]
        bom.lines.append(BomLine(item_id=it.id, item=it, qty_per_batch=to_base(it, qty, unit), basis=basis))
    db.add(bom)

    for code, name, kind, base, disp, reorder, qty, rate in MATERIALS:
        it = items[code]
        q = to_base(it, qty, disp)
        stock.post(db, item=it, qty=q, kind="opening", source_type="opening", source_ref="OPENING",
                   on=opening_date, value_paise=to_paise(rate) * qty, note="Opening balance")
    # Opening FG: 50 packets. Their cost is not given; assumed Rs 30.00/packet (documented in README).
    stock.post(db, item=mur, qty=50, kind="opening", source_type="opening", source_ref="OPENING",
               on=opening_date, value_paise=50 * to_paise("30"), batch_no="MUR-OPENING",
               expiry=opening_date + timedelta(days=45),
               note="Opening balance")
    db.commit()
    return users


def reset_and_seed(opening_date: date | None = None):
    dbm.drop_all()
    dbm.create_all()
    db = dbm.SessionLocal()
    try:
        seed(db, opening_date)
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="opening-stock date YYYY-MM-DD (default: 1st of this month)")
    a = ap.parse_args()
    dbm.init()
    reset_and_seed(date.fromisoformat(a.date) if a.date else None)
    print(f"Seeded. Logins (password '{PASSWORD}'): " + ", ".join(u for u, *_ in USERS))
