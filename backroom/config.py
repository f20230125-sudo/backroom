"""Business tolerances and contracted rates live together so checks cannot drift."""

from decimal import Decimal

MONEY_TOLERANCE = Decimal("0.01")
SALES_TOLERANCE = Decimal("1.00")
COMMISSION_RATES = {"A": Decimal("0.25"), "B": Decimal("0.22"), "C": Decimal("0.28")}
COMMISSION_VAT_RATE = Decimal("0.05")
VAT_RATE = Decimal("0.05")
