"""Generate the deterministic raw data the dbt project seeds from.

The data is synthetic: names are combined from short lists and every value comes from a seeded
random generator, so the files are identical on every run.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "warehouse" / "seeds"
RNG = random.Random(20251)

COUNTRIES = {
    "AMER": ["United States", "Canada", "Brazil", "Mexico"],
    "EMEA": ["United Kingdom", "Germany", "France", "United Arab Emirates"],
    "APAC": ["India", "Japan", "Australia", "Singapore"],
}
REGION_WEIGHTS = {"AMER": 0.42, "EMEA": 0.33, "APAC": 0.25}
SEGMENTS = [("Enterprise", 0.2), ("Mid-Market", 0.35), ("SMB", 0.45)]
FIRST = [
    "Asha",
    "Ben",
    "Chen",
    "Divya",
    "Elena",
    "Farid",
    "Grace",
    "Hiro",
    "Ines",
    "Jonas",
    "Kavya",
    "Liam",
    "Maya",
    "Noah",
    "Omar",
    "Priya",
    "Quinn",
    "Rosa",
    "Sven",
    "Tara",
    "Uma",
    "Victor",
    "Wen",
    "Yusuf",
]
LAST = [
    "Anand",
    "Brooks",
    "Costa",
    "Dubois",
    "Eriksen",
    "Fischer",
    "Gupta",
    "Hayes",
    "Ito",
    "Jensen",
    "Khan",
    "Lopez",
    "Mehta",
    "Nakamura",
    "O'Brien",
    "Patel",
    "Rossi",
    "Singh",
    "Tanaka",
    "Weber",
]
CATEGORIES = {
    "Hardware": ["Edge Router", "Core Switch", "Access Point", "Firewall Appliance", "Storage Node", "Rack Server"],
    "Software": ["Analytics Suite", "Backup Manager", "Identity Hub", "Observability Pack", "Data Catalog", "API Gateway"],
    "Services": ["Implementation", "Migration Sprint", "Architecture Review", "Training Day"],
    "Support": ["Standard Support", "Premium Support", "Managed Operations"],
}
TIERS = [("S", 0.7), ("M", 1.0), ("L", 1.6), ("XL", 2.4)]
BASE_PRICE = {"Hardware": 1800.0, "Software": 950.0, "Services": 2400.0, "Support": 600.0}
CHANNELS = [("Direct", 0.45), ("Partner", 0.35), ("Online", 0.20)]
STATUSES = [("completed", 0.84), ("returned", 0.05), ("cancelled", 0.07), ("open", 0.04)]


def pick(options: list[tuple[str, float]]) -> str:
    return RNG.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def write(name: str, header: list[str], rows: list[list[object]]) -> None:
    with (OUT / f"{name}.csv").open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    customers = []
    for cid in range(1, 801):
        region = pick(list(REGION_WEIGHTS.items()))
        first, last = RNG.choice(FIRST), RNG.choice(LAST)
        handle = f"{first}.{last}".lower().replace("'", "")
        customers.append(
            [
                cid,
                f"{first} {last}",
                f"{handle}{cid}@example.com",
                f"+1-555-{RNG.randint(1000, 9999):04d}",
                pick(SEGMENTS),
                RNG.choice(COUNTRIES[region]),
                region,
                (dt.date(2022, 1, 1) + dt.timedelta(days=RNG.randint(0, 900))).isoformat(),
            ]
        )
    write("raw_customers", ["customer_id", "customer_name", "email", "phone", "segment", "country", "region", "signup_date"], customers)

    products = []
    pid = 0
    for category, names in CATEGORIES.items():
        for name in names:
            for tier, factor in TIERS[: 3 if category in ("Services", "Support") else 4]:
                pid += 1
                price = round(BASE_PRICE[category] * factor * RNG.uniform(0.9, 1.1), 2)
                products.append([pid, f"{name} {tier}", category, price])
    write("raw_products", ["product_id", "product_name", "category", "list_price"], products)

    orders, lines = [], []
    start = dt.date(2024, 1, 1)
    for oid in range(1, 6001):
        customer = RNG.choice(customers)
        day = start + dt.timedelta(days=int(RNG.triangular(0, 730, 600)))
        orders.append([oid, customer[0], day.isoformat(), pick(CHANNELS), pick(STATUSES)])
        for line in range(1, RNG.choice([1, 1, 2, 2, 3, 4]) + 1):
            product = RNG.choice(products)
            qty = RNG.choice([1, 1, 1, 2, 2, 3, 5, 10])
            discount = RNG.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2]) if customer[4] != "SMB" else RNG.choice([0, 0, 0.05])
            lines.append([oid, line, product[0], qty, product[3], discount])
    write("raw_orders", ["order_id", "customer_id", "order_date", "channel", "status"], orders)
    write("raw_order_items", ["order_id", "line_number", "product_id", "quantity", "unit_price", "discount_pct"], lines)


if __name__ == "__main__":
    main()
