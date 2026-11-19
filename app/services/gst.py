"""GST maths.

Intra-state (factory state == place of supply): CGST + SGST, each half the rate.
Inter-state: IGST at the full rate. Tax is computed per invoice line and
rounded half-up to the paisa; invoice totals are sums of line amounts.
"""
from decimal import Decimal

from app.money import round_paise

STATES = {
    "29": "Karnataka", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "36": "Telangana",
    "37": "Andhra Pradesh", "27": "Maharashtra", "07": "Delhi",
}


def supply_type(company_state: str, place_of_supply: str) -> str:
    return "intra" if company_state == place_of_supply else "inter"


def split_tax(taxable_paise: int, rate_bp: int, kind: str) -> tuple[int, int, int]:
    """Return (cgst, sgst, igst) in paise. rate_bp 500 = 5%."""
    if kind == "intra":
        half = round_paise(Decimal(taxable_paise) * rate_bp / 2 / 10000)
        return half, half, 0
    return 0, 0, round_paise(Decimal(taxable_paise) * rate_bp / 10000)
