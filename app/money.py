"""Money helpers. Money is always integer paise; Decimal is used only mid-calculation."""
from decimal import ROUND_HALF_UP, Decimal


def to_paise(rupees) -> int:
    """'45' -> 4500, '2.50' -> 250. Refuses more than 2 decimal places."""
    d = Decimal(str(rupees).replace(",", "").replace("₹", "").strip() or "0")
    p = d * 100
    if p != p.to_integral_value():
        raise ValueError(f"{rupees} has more than 2 decimal places")
    return int(p)


def round_paise(d: Decimal) -> int:
    """Round half up to the nearest paisa (the usual GST convention)."""
    return int(Decimal(d).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def _indian_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def fmt(paise: int | None, symbol: bool = True) -> str:
    """4787500 -> '₹47,875.00' (Indian digit grouping)."""
    if paise is None:
        return ""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    s = f"{_indian_group(p // 100)}.{p % 100:02d}"
    return f"{sign}{'₹' if symbol else ''}{s}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _words_below_100(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _words(n: int) -> str:
    if n == 0:
        return "Zero"
    out = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand"), (100, "Hundred")):
        if n >= size:
            out.append(f"{_words(n // size)} {name}")
            n %= size
    if n:
        out.append(_words_below_100(n))
    return " ".join(out)


def in_words(paise: int) -> str:
    """787500 -> 'Rupees Seven Thousand Eight Hundred Seventy Five Only'."""
    r, p = divmod(abs(int(paise)), 100)
    s = f"Rupees {_words(r)}"
    if p:
        s += f" and {_words_below_100(p)} Paise"
    return s + " Only"
