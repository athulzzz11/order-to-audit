from decimal import Decimal

import pytest

from app.models import Item
from app.money import fmt, in_words, round_paise, to_paise
from app.services.gst import split_tax
from app.units import UnitError, fmt_qty, from_base, to_base

flour = Item(name="Flour", kind="raw", base_unit="g", display_unit="kg")
oil = Item(name="Oil", kind="raw", base_unit="ml", display_unit="l")
mur = Item(name="Murukku", kind="finished", base_unit="pcs", display_unit="packet", pack_per_carton=20)


def test_mass_volume_count_conversions():
    assert to_base(flour, "1.2", "kg") == 1200
    assert to_base(flour, 500, "g") == 500
    assert to_base(oil, "10.5", "litre") == 10500
    assert to_base(mur, 3, "carton") == 60
    assert from_base(oil, 19500) == Decimal("19.5")
    assert fmt_qty(flour, 120000) == "120 kg"


def test_incompatible_or_too_fine_units_are_refused():
    with pytest.raises(UnitError):
        to_base(flour, 1, "l")
    with pytest.raises(UnitError):
        to_base(flour, "0.0005", "kg")
    with pytest.raises(UnitError):
        to_base(Item(name="Pouch", kind="packaging", base_unit="pcs", display_unit="pcs"), 1, "carton")


def test_money_is_exact_paise():
    assert to_paise("2.50") == 250
    assert to_paise("45") == 4500
    with pytest.raises(ValueError):
        to_paise("1.005")
    assert round_paise(Decimal("0.5")) == 1  # half up, not banker's
    assert fmt(787500) == "₹7,875.00"
    assert fmt(1234567890) == "₹1,23,45,678.90"
    assert in_words(787500) == "Rupees Seven Thousand Eight Hundred Seventy Five Only"


def test_gst_split():
    assert split_tax(750000, 500, "intra") == (18750, 18750, 0)
    assert split_tax(600000, 500, "inter") == (0, 0, 30000)
    # 2.5% of Rs 0.25 = 0.625 paise -> 1 paisa each side
    assert split_tax(25, 500, "intra") == (1, 1, 0)
