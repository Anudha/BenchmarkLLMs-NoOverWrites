"""Verticals and categories.

Each category has:
  key    - stable id used in data files
  label  - wording used inside questions ("Which {label} is at ...?")
  tags   - one or more OSM (key, value) pairs; any match counts

To add a category, append a line to the vertical it belongs to. To add a
vertical, add a new entry to VERTICALS. Nothing else needs to change."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    tags: tuple[tuple[str, str], ...]
    vertical: str


def _c(key: str, label: str, *tags: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    return key, label, tuple(tuple(t.split("=", 1)) for t in tags)  # type: ignore[misc]


_SPEC: dict[str, list] = {
    "restaurants": [
        _c("restaurant", "restaurant", "amenity=restaurant"),
    ],
    "food_drink": [
        _c("cafe", "café", "amenity=cafe"),
        _c("bar", "bar", "amenity=bar"),
        _c("pub", "pub", "amenity=pub"),
        _c("ice_cream", "ice cream shop", "amenity=ice_cream", "shop=ice_cream"),
        _c("biergarten", "beer garden", "amenity=biergarten"),
        _c("bakery", "bakery", "shop=bakery"),
        _c("butcher", "butcher shop", "shop=butcher"),
        _c("deli", "deli", "shop=deli"),
        _c("pastry", "pastry shop", "shop=pastry"),
        _c("confectionery", "candy shop", "shop=confectionery"),
        _c("wine_shop", "wine shop", "shop=wine"),
        _c("coffee_shop", "coffee roaster or coffee shop", "shop=coffee"),
        _c("tea_shop", "tea shop", "shop=tea"),
        _c("cheese_shop", "cheese shop", "shop=cheese"),
        _c("seafood_shop", "fish market", "shop=seafood"),
        _c("chocolate_shop", "chocolate shop", "shop=chocolate"),
    ],
    "lodging": [
        _c("hotel", "hotel", "tourism=hotel"),
        _c("guest_house", "guest house", "tourism=guest_house"),
        _c("hostel", "hostel", "tourism=hostel"),
        _c("motel", "motel", "tourism=motel"),
    ],
    "personal_care": [
        _c("hairdresser", "hair salon", "shop=hairdresser"),
        _c("beauty", "beauty salon", "shop=beauty"),
        _c("massage", "massage studio", "shop=massage"),
        _c("tattoo", "tattoo studio", "shop=tattoo"),
        _c("cosmetics", "cosmetics shop", "shop=cosmetics"),
        _c("gym", "gym", "leisure=fitness_centre"),
    ],
    "health": [
        _c("dentist", "dental practice", "amenity=dentist", "healthcare=dentist"),
        _c("doctors", "doctor's office", "amenity=doctors", "healthcare=doctor"),
        _c("clinic", "clinic", "amenity=clinic", "healthcare=clinic"),
        _c("vet", "veterinary clinic", "amenity=veterinary"),
        _c("optician", "optician", "shop=optician", "healthcare=optometrist"),
        _c("physio", "physiotherapy practice", "healthcare=physiotherapist"),
    ],
    "retail": [
        _c("books", "bookstore", "shop=books"),
        _c("florist", "florist", "shop=florist"),
        _c("gift", "gift shop", "shop=gift"),
        _c("jewelry", "jewelry store", "shop=jewelry"),
        _c("bicycle", "bike shop", "shop=bicycle"),
        _c("antiques", "antique shop", "shop=antiques"),
        _c("second_hand", "thrift store", "shop=second_hand", "shop=charity"),
        _c("art", "art shop", "shop=art"),
        _c("music", "record store", "shop=music"),
        _c("instruments", "musical instrument shop", "shop=musical_instrument"),
        _c("toys", "toy store", "shop=toys"),
        _c("pet", "pet store", "shop=pet"),
        _c("frame", "framing shop", "shop=frame"),
        _c("outdoor", "outdoor gear shop", "shop=outdoor"),
        _c("fabric", "fabric store", "shop=fabric"),
        _c("furniture", "furniture store", "shop=furniture"),
        _c("garden_centre", "garden center", "shop=garden_centre"),
        _c("hardware", "hardware store", "shop=hardware"),
    ],
    "clothing": [
        _c("clothes", "clothing store", "shop=clothes"),
        _c("shoes", "shoe store", "shop=shoes"),
        _c("boutique", "boutique", "shop=boutique"),
    ],
    "services": [
        _c("laundry", "laundromat", "shop=laundry"),
        _c("dry_cleaning", "dry cleaner", "shop=dry_cleaning"),
        _c("copyshop", "print shop", "shop=copyshop"),
        _c("tailor", "tailor", "shop=tailor", "craft=tailor"),
        _c("shoe_repair", "shoe repair shop", "shop=shoe_repair", "craft=shoemaker"),
        _c("travel_agency", "travel agency", "shop=travel_agency"),
        _c("funeral_home", "funeral home", "shop=funeral_directors"),
        _c("photo", "photo studio", "shop=photo", "craft=photographer"),
        _c("locksmith", "locksmith", "shop=locksmith", "craft=locksmith"),
    ],
    "auto": [
        _c("car_repair", "auto repair shop", "shop=car_repair"),
        _c("tyres", "tire shop", "shop=tyres"),
        _c("car_parts", "auto parts store", "shop=car_parts"),
        _c("car_dealer", "car dealership", "shop=car"),
        _c("car_wash", "car wash", "amenity=car_wash"),
    ],
    "entertainment": [
        _c("cinema", "movie theater", "amenity=cinema"),
        _c("theatre", "theater", "amenity=theatre"),
        _c("nightclub", "nightclub", "amenity=nightclub"),
        _c("arts_centre", "arts center", "amenity=arts_centre"),
        _c("museum", "museum", "tourism=museum"),
        _c("gallery", "art gallery", "tourism=gallery"),
        _c("bowling", "bowling alley", "leisure=bowling_alley"),
        _c("escape_room", "escape room", "leisure=escape_game"),
    ],
    "makers": [
        _c("brewery", "brewery", "craft=brewery"),
        _c("winery", "winery", "craft=winery"),
        _c("distillery", "distillery", "craft=distillery"),
    ],
    "schools": [
        _c("driving_school", "driving school", "amenity=driving_school"),
        _c("language_school", "language school", "amenity=language_school"),
        _c("music_school", "music school", "amenity=music_school"),
    ],
}

VERTICALS: dict[str, list[Category]] = {
    v: [Category(key, label, tags, v) for key, label, tags in cats] for v, cats in _SPEC.items()
}
CATEGORIES: dict[str, Category] = {c.key: c for cats in VERTICALS.values() for c in cats}


def resolve_verticals(spec: str) -> list[str]:
    """'all' or a comma list like 'restaurants,lodging'."""
    if spec.strip() == "all":
        return list(VERTICALS)
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in VERTICALS]
    if unknown:
        raise SystemExit(f"unknown vertical(s): {unknown}; choose from {list(VERTICALS)}")
    return names
