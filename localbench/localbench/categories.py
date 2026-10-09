"""Verticals and categories.

Three verticals:
  coffee_shops     - independent coffee shops (amenity=cafe)
  grocery_produce  - stores that sell fresh produce: produce markets,
                     independent grocery stores, farm stands
  bike_trails      - named bike trails: OSM bicycle route relations plus
                     named cycleways / bike-designated paths

Businesses and trails are different kinds of things with different facts,
so each vertical has a `kind`: "business" verticals ask phone / address /
website / name; "trail" verticals ask length / surface / website /
operator / name (see generate.TEMPLATES).

To add a business category, append a line to its vertical. To add a
business vertical, add an entry to _SPEC and VERTICAL_KIND."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    tags: tuple[tuple[str, str], ...]
    vertical: str
    # Skip elements whose tag (key) has its first value in this set,
    # e.g. cafes whose cuisine is bubble tea aren't coffee shops.
    exclude: tuple[tuple[str, frozenset], ...] = field(default=())


def _c(key: str, label: str, *tags: str, exclude: dict | None = None):
    return (key, label, tuple(tuple(t.split("=", 1)) for t in tags),
            tuple((k, frozenset(v)) for k, v in (exclude or {}).items()))


NOT_COFFEE = {"bubble_tea", "tea", "ice_cream", "juice", "crepe", "frozen_yogurt", "dessert", "cake", "pancake"}

_SPEC: dict[str, list] = {
    "coffee_shops": [
        _c("coffee_shop", "coffee shop", "amenity=cafe", exclude={"cuisine": NOT_COFFEE}),
    ],
    "grocery_produce": [
        _c("produce_market", "produce market", "shop=greengrocer"),
        _c("grocery_store", "grocery store", "shop=supermarket"),
        _c("farm_stand", "farm stand", "shop=farm"),
    ],
    # Trail tags are informational here; trails.py builds its own query
    # because trails need geometry (for length) rather than an address.
    "bike_trails": [
        _c("bike_route", "bike trail", "route=bicycle"),
        _c("bike_path", "bike trail", "highway=cycleway", "highway=path"),
    ],
}

VERTICAL_KIND: dict[str, str] = {
    "coffee_shops": "business",
    "grocery_produce": "business",
    "bike_trails": "trail",
}

VERTICALS: dict[str, list[Category]] = {
    v: [Category(key, label, tags, v, ex) for key, label, tags, ex in cats] for v, cats in _SPEC.items()
}
CATEGORIES: dict[str, Category] = {c.key: c for cats in VERTICALS.values() for c in cats}


def business_categories(verticals: list[str]) -> list[Category]:
    return [c for v in verticals if VERTICAL_KIND[v] == "business" for c in VERTICALS[v]]


def resolve_verticals(spec: str) -> list[str]:
    """'all' or a comma list like 'coffee_shops,bike_trails'."""
    if spec.strip() == "all":
        return list(VERTICALS)
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in VERTICALS]
    if unknown:
        raise SystemExit(f"unknown vertical(s): {unknown}; choose from {list(VERTICALS)}")
    return names
