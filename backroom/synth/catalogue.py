"""Invented Dubai trading partners and AED wholesale price bands, never real businesses."""

from decimal import Decimal

from backroom.models import Account, Address, Item, Outlet, Platform, Supplier

BUYER_NAME = "Saffron & Salt Group L.L.C."
BUYER_TRN = "100000900000003"
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
    ("Gulf Fresh Fish", "meat"),
    ("Dune Orchard Produce", "produce"),
    ("Pearl Meadow Dairy", "dairy"),
    ("Copper Dhow Dry Goods", "dry"),
    ("Al Quoz Pantry", "dry"),
    ("Crescent Pasture Meats", "meat"),
    ("Coral Basket Seafood", "meat"),
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
SUPPLIERS = [
    Supplier(
        id=f"SUP-{i:02}",
        legal_name=f"{name} Trading L.L.C.",
        trade_name=name,
        address=Address(
            street=f"Warehouse {i + 30}, Industrial Lane {i}, "
            + ("Sharjah" if i % 6 == 0 else "Dubai"),
            emirate="SH" if i % 6 == 0 else "DU",
        ),
        trn=f"1{i:012}03",
        payment_terms_days=(14, 30, 45)[i % 3],
        category=category,
    )
    for i, (name, category) in enumerate(_SUPPLIERS, 1)
]
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
            "White Fish Fillet",
            "Prawns",
            "Chicken Thigh",
            "Lamb Mince",
            "Beef Striploin",
        ],
    ),
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
        unit=unit,
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
