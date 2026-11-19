"""Unit conversion.

Every item stores stock in one base unit (g, ml or pcs). Users can enter any
compatible unit; we convert to an exact integer of base units or refuse.
'carton' is item-specific: it means `item.pack_per_carton` packets.
"""
from decimal import Decimal

UNITS = {
    "g": ("mass", 1),
    "kg": ("mass", 1000),
    "ml": ("volume", 1),
    "l": ("volume", 1000),
    "pcs": ("count", 1),
    "packet": ("count", 1),
    "carton": ("count", None),  # resolved per item
}
ALIASES = {
    "gm": "g", "gms": "g", "gram": "g", "grams": "g", "kgs": "kg", "kilogram": "kg",
    "litre": "l", "liter": "l", "litres": "l", "ltr": "l", "lt": "l",
    "piece": "pcs", "pieces": "pcs", "pc": "pcs", "nos": "pcs",
    "packets": "packet", "pkt": "packet", "pkts": "packet", "cartons": "carton", "ctn": "carton",
}


class UnitError(ValueError):
    pass


def norm(unit: str) -> str:
    u = (unit or "").strip().lower()
    return ALIASES.get(u, u)


def units_for(item) -> list[str]:
    """Units a user may pick for this item."""
    out = [u for u, (dim, _) in UNITS.items() if dim == item.dimension and u != "carton"]
    if item.dimension == "count" and item.pack_per_carton:
        out.append("carton")
    if item.kind != "finished":
        out = [u for u in out if u != "packet"]
    return out


def factor(item, unit: str) -> int:
    u = norm(unit)
    if u not in UNITS:
        raise UnitError(f"Unknown unit '{unit}'")
    dim, f = UNITS[u]
    if dim != item.dimension:
        raise UnitError(f"'{unit}' is a {dim} unit but {item.name} is measured by {item.dimension}")
    if u == "carton":
        if not item.pack_per_carton:
            raise UnitError(f"{item.name} has no carton size set")
        f = item.pack_per_carton
    return f


def to_base(item, qty, unit: str) -> int:
    """Convert a user quantity to integer base units, refusing anything finer than 1 base unit."""
    q = Decimal(str(qty).strip())
    b = q * factor(item, unit)
    if b != b.to_integral_value():
        raise UnitError(f"{qty} {unit} is finer than 1 {item.base_unit}")
    return int(b)


def from_base(item, qty_base: int, unit: str | None = None) -> Decimal:
    return Decimal(qty_base) / factor(item, unit or item.display_unit)


def fmt_num(d: Decimal) -> str:
    d = d.normalize()
    s = f"{d:f}"
    return s


def fmt_qty(item, qty_base: int, unit: str | None = None, with_unit: bool = True) -> str:
    unit = unit or item.display_unit
    s = fmt_num(from_base(item, qty_base, unit))
    return f"{s} {unit}" if with_unit else s
