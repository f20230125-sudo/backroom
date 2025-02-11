"""Invented Dubai trading partners and AED wholesale price bands, never real businesses."""

import random
from decimal import Decimal

from backroom.models import Account, Address, Item, Outlet, Platform, Supplier, SupplierStationery

SEED = 20261027
BUYER_NAME = "Saffron & Salt Group L.L.C."
OUTLETS = [
    Outlet(
        id="OUT-JBR",
        name="Saffron & Salt, JBR Walk",
        trading=True,
        address=Address(street="Unit 14, JBR Walk, Dubai", emirate="DU"),
    ),
    Outlet(
        id="OUT-DIFC",
        name="Saffron & Salt, DIFC",
        trading=True,
        address=Address(street="Unit 8, Gate Courtyard, Dubai", emirate="DU"),
    ),
    Outlet(
        id="OUT-AQK",
        name="Central Kitchen, Al Quoz",
        trading=False,
        address=Address(street="Warehouse 21, Al Quoz, Dubai", emirate="DU"),
    ),
]
_SUPPLIERS = [
    ("Gulf Fresh Produce", "produce"),
    ("Gulf Fresh Fish", "seafood"),
    ("Dune Orchard Produce", "produce"),
    ("Pearl Meadow Dairy", "dairy"),
    ("Copper Dhow Dry Goods", "dry"),
    ("Al Quoz Pantry", "dry"),
    ("Crescent Pasture Meats", "meat"),
    ("Coral Basket Seafood", "seafood"),
    ("Amber Grain Provisions", "dry"),
    ("Dune Spring Beverages", "beverages"),
    ("Dune Springs Packaging", "packaging"),
    ("Palm Loom Linen", "linen"),
    ("Palm Bloom Cleaning", "cleaning"),
    ("Silver Ladle Packaging", "packaging"),
    ("Saffron Crate Spices", "dry"),
    ("Salt Crate Seasonings", "dry"),
    ("Mirage Cup Beverages", "beverages"),
    ("Mirage Cap Supplies", "packaging"),
]
_ADDRESSES = [
    ("Warehouse {n}, Al Quoz Industrial Area 3, Dubai", "DU"),
    ("Plot 598-{plot}, Dubai Investments Park 2, Dubai", "DU"),
    ("Shed {n}, Ras Al Khor Industrial Area 2, Dubai", "DU"),
    ("Unit {n}, Dubai Wholesale City, Dubai", "DU"),
    ("Store {n}, Al Aweer Fruit and Vegetable Market, Dubai", "DU"),
    ("Warehouse {n}, Umm Ramool, Dubai", "DU"),
    ("Unit {n}, Sharjah Industrial Area 6, Sharjah", "SH"),
    ("Warehouse {n}, Sharjah Industrial Area 15, Sharjah", "SH"),
    ("Shed {n}, Sharjah Industrial Area 10, Sharjah", "SH"),
]
# Each trader has its own stationery, including independent invoice/credit counters.
_NUMBER_FORMATS = [
    "GFP/{yy}/{seq:05}",
    "GFF-{year}-{seq:05}",
    "TI {seq:05}",
    "PMD-{yy}{month:02}-{seq:04}",
    "CD-{yy}{month:02}-{seq:04}",
    "AQP/INV/{seq:06}",
    "CPM {yy}/{seq:05}",
    "CBS-{seq:06}",
    "AGP/{year}/{seq:04}",
    "DSB-{seq:05}/{yy}",
    "DSP/INV-{seq:05}",
    "PLL-{year}/{seq:04}",
    "PBC {seq:06}",
    "SLP-{yy}-{seq:05}",
    "SCS/{seq:05}/{yy}",
    "SCT-{seq:06}",
    "MCB/{yy}{month:02}/{seq:04}",
    "MCS-INV-{seq:05}",
]


def masters(seed: int = SEED) -> tuple[list[Supplier], str]:
    """Keep master sampling independent of sales and guarantee legal-entity TRN uniqueness."""
    rng = random.Random(seed)  # noqa: S311 -- synthetic stationery, never credentials.
    used: set[str] = set()

    def trn() -> str:
        while True:
            value = f"1{rng.randrange(10**12):012}03"
            if value not in used:
                used.add(value)
                return value

    buyer_trn = trn()
    # Fifteen Dubai suppliers plus three in Sharjah, shuffled rather than cycling locations.
    addresses = _ADDRESSES[:6] * 2 + _ADDRESSES[:3] + _ADDRESSES[6:]
    rng.shuffle(addresses)
    suppliers = []
    for i, ((name, category), (template, emirate), number_format) in enumerate(
        zip(_SUPPLIERS, addresses, _NUMBER_FORMATS, strict=True), 1
    ):
        street = template.format(n=rng.randint(3, 86), plot=rng.randint(1000, 1999))
        if rng.choice([True, False]):
            street += f", PO Box {rng.randint(10000, 999999)}"
        prefix = "".join(word[0] for word in name.split())
        suppliers.append(
            Supplier(
                id=f"SUP-{i:02}",
                legal_name=f"{name} Trading L.L.C.",
                trade_name=name,
                address=Address(street=street, emirate=emirate),
                trn=trn(),
                payment_terms_days=(14, 30, 45)[i % 3],
                category=category,
                stationery=SupplierStationery(
                    invoice_format=number_format,
                    credit_note_format=f"CN-{prefix}/{{yy}}/{{seq:04}}",
                    invoice_sequence_start=rng.randint(
                        73, 9200 if "seq:04" in number_format else 78000
                    ),
                    credit_note_sequence_start=rng.randint(8, 900),
                    description_style=("sku", "plain", "pack")[(i - 1) % 3],
                    item_code_prefix=prefix,
                    show_item_codes=i % 3 != 0,
                    phone=f"+971 {6 if emirate == 'SH' else 4} 555 {rng.randint(1000, 9999)}",
                    email=f"accounts@{name.lower().replace(' ', '-')}.example",
                    bank_name=rng.choice(
                        ["Dune Ledger Bank", "Pearl Quay Bank", "Copper Palm Bank"]
                    ),
                    iban="AE" + "".join(str(rng.randrange(10)) for _ in range(21)),
                    show_contact=i % 4 != 0,
                    show_bank=i % 3 != 1,
                ),
            )
        )
    return suppliers, buyer_trn


def item_code(supplier: Supplier, item_id: str) -> str:
    return f"{supplier.stationery.item_code_prefix}-{item_id[4:]}"


def item_description(supplier: Supplier, item: Item, variant: str) -> str:
    """Variants stay verbatim on plain stationery; SKU stationery adds trade pack detail."""
    pack = {
        "produce": "5KG CRATE",
        "meat": "CHILLED / KG",
        "seafood": "FROZEN / KG",
        "dairy": "1L X 12",
        "dry": "CATERING PACK",
        "beverages": "12 X 1L",
        "packaging": "BOX 500 PCS",
        "cleaning": "5L CAN",
        "linen": "1 PC",
    }[item.category]
    pack = {
        "Butter Portions": "100 X 10G",
        "Soft Cheese": "2KG TUB",
        "Feta": "4KG TIN",
        "Halloumi": "1KG PACK",
        "Plain Yoghurt": "1KG TUB",
        "Olive Oil": "5L TIN",
        "Sunflower Oil": "5L CAN",
        "Black Tea": "100 TEA BAGS",
        "Coffee Beans": "1KG BAG",
    }.get(item.name, pack)
    if supplier.stationery.description_style == "sku":
        label = variant if item.id in VARIANTS else variant.upper()
        return f"{label} - {pack}"
    if supplier.stationery.description_style == "pack":
        return f"{variant} ({pack.lower()})"
    return variant


SUPPLIERS, BUYER_TRN = masters()
PLATFORMS = [
    Platform(id=p, name=f"Platform {p}", commission_rate=Decimal(rate))
    for p, rate in (("A", "0.25"), ("B", "0.22"), ("C", "0.28"))
]
ACCOUNTS = [
    Account(code=code, name=name)
    for code, name in (
        ("400", "Sales dine-in"),
        ("410", "Sales delivery"),
        ("500", "Commission expense"),
        ("510", "Food cost"),
        ("520", "Packaging"),
        ("530", "Cleaning and linen"),
        ("100", "Bank"),
    )
]
_BASKETS = {
    "produce": (
        "KGM",
        "4",
        "16",
        [
            "Tomato Roma",
            "Cucumber",
            "Red Onion",
            "Lemon",
            "Baby Spinach",
            "Aubergine",
            "Carrot",
            "Potato",
            "Parsley",
            "Mint",
        ],
    ),
    "meat": (
        "KGM",
        "18",
        "65",
        [
            "Chicken Breast",
            "Lamb Shoulder",
            "Beef Mince",
            "Chicken Thigh",
            "Lamb Mince",
            "Beef Striploin",
        ],
    ),
    "seafood": ("KGM", "18", "65", ["White Fish Fillet", "Prawns"]),
    "dairy": (
        "LTR",
        "6",
        "24",
        [
            "Whole Milk",
            "Cooking Cream",
            "Plain Yoghurt",
            "Laban",
            "Butter Portions",
            "Soft Cheese",
            "Feta",
            "Halloumi",
        ],
    ),
    "dry": (
        "BX",
        "20",
        "90",
        [
            "Basmati Rice",
            "Plain Flour",
            "Chickpeas",
            "Red Lentils",
            "Olive Oil",
            "Sunflower Oil",
            "Cumin",
            "Black Pepper",
            "Sea Salt",
            "Sugar",
        ],
    ),
    "beverages": (
        "CS",
        "24",
        "80",
        [
            "Still Water",
            "Sparkling Water",
            "Orange Juice",
            "Apple Juice",
            "Black Tea",
            "Coffee Beans",
            "Mango Juice",
            "Lemon Drink",
        ],
    ),
    "packaging": (
        "BX",
        "25",
        "100",
        [
            "Kraft Bowl",
            "Kraft Bowl Lid",
            "Paper Bag",
            "Napkin",
            "Cutlery Set",
            "Delivery Seal",
            "Foil Tray",
            "Paper Cup",
        ],
    ),
    "cleaning": (
        "LTR",
        "10",
        "40",
        [
            "Dishwash Liquid",
            "Surface Sanitiser",
            "Floor Cleaner",
            "Hand Soap",
            "Degreaser",
            "Rinse Aid",
        ],
    ),
    "linen": ("H87", "8", "30", ["Service Cloth", "Table Linen"]),
}
ITEMS = [
    Item(
        id=f"ITM-{i:03}",
        name=name,
        category=category,
        unit={
            "Butter Portions": "BX",
            "Soft Cheese": "KGM",
            "Feta": "KGM",
            "Halloumi": "KGM",
            "Plain Yoghurt": "KGM",
            "Olive Oil": "LTR",
            "Sunflower Oil": "LTR",
            "Coffee Beans": "KGM",
        }.get(name, unit),
        price_min=Decimal(low),
        price_max=Decimal(high),
    )
    for i, (category, unit, low, high, name) in enumerate(
        (
            (category, unit, low, high, name)
            for category, (unit, low, high, names) in _BASKETS.items()
            for name in names
        ),
        1,
    )
]

# All items are standard-rated; no assumed zero-rating of ordinary domestic food supplies.
VARIANTS = {
    "OUT-JBR": ["JBR", "JBR Walk", "S&S JBR", "Saffron & Salt, JBR Walk"],
    "OUT-DIFC": ["DIFC", "S&S DIFC", "Outlet 3"],
    "OUT-AQK": ["Al Quoz", "Central Kitchen", "Central Kitchen, Al Quoz"],
    "SUP-01": ["Gulf Fresh Produce", "GULF FRESH PRODUCE TRDG LLC", "Gulf Fresh"],
    "SUP-02": ["Gulf Fresh Fish", "GULF FRESH FISH TRDG LLC", "Gulf Fresh Fish Trading"],
    "SUP-03": ["Dune Orchard", "Dune Orchard Produce", "DUNE ORCHARD TRDG"],
    "SUP-04": ["Pearl Meadow", "Pearl Meadow Dairy", "PEARL MEADOW DAIRY LLC"],
    "SUP-05": ["Copper Dhow", "Copper Dhow Dry Goods", "COPPER DHOW TRDG"],
    "SUP-06": ["Al Quoz Pantry", "AL QUOZ PANTRY TRDG", "Al Quoz Pantry LLC"],
    "ITM-001": ["Roma Tomatoes", "TOMATO-ROMA 5KG"],
    "ITM-002": ["CUCUMBER", "Cucumber fresh"],
    "ITM-003": ["Onion red", "RED ONIONS"],
    "ITM-004": ["Lemons", "LEMON FRESH"],
    "ITM-005": ["Spinach baby", "BABY SPINACH LEAF"],
    "ITM-006": ["Eggplant", "AUBERGINE FRESH"],
}
