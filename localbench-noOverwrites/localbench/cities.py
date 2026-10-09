"""City registry. Bboxes are (south, west, north, east).

Cities with in_daily_pool=True are what the daily run samples from. Others
(e.g. smaller Bay Area cities) are only used when asked for by name with
`--city`. `addr_city`, when set, drops businesses whose OSM addr:city says
they're in a different city; useful where a bbox spills into neighbors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    country: str
    bbox: tuple[float, float, float, float]
    addr_city: str | None = None
    in_daily_pool: bool = True


CITIES: list[City] = [
    City("san_francisco", "San Francisco", "US", (37.70, -122.52, 37.81, -122.36)),
    City("san_jose", "San Jose", "US", (37.28, -121.95, 37.40, -121.83)),
    City("los_angeles", "Los Angeles", "US", (33.95, -118.45, 34.12, -118.20)),
    City("seattle", "Seattle", "US", (47.55, -122.42, 47.70, -122.28)),
    City("portland", "Portland", "US", (45.47, -122.72, 45.57, -122.60)),
    City("denver", "Denver", "US", (39.68, -105.05, 39.78, -104.93)),
    City("austin", "Austin", "US", (30.20, -97.83, 30.35, -97.68)),
    City("chicago", "Chicago", "US", (41.78, -87.72, 41.97, -87.58)),
    City("new_orleans", "New Orleans", "US", (29.90, -90.13, 29.99, -90.03)),
    City("philadelphia", "Philadelphia", "US", (39.90, -75.23, 40.00, -75.13)),
    City("new_york", "New York", "US", (40.70, -74.02, 40.88, -73.91)),
    City("boston", "Boston", "US", (42.33, -71.13, 42.39, -71.03)),
    City("toronto", "Toronto", "CA", (43.63, -79.45, 43.70, -79.35)),
    City("vancouver", "Vancouver", "CA", (49.26, -123.14, 49.30, -123.09)),
    City("london", "London", "GB", (51.48, -0.20, 51.54, -0.05)),
    City("dublin", "Dublin", "IE", (53.33, -6.29, 53.36, -6.24)),
    City("amsterdam", "Amsterdam", "NL", (52.35, 4.87, 52.39, 4.92)),
    City("paris", "Paris", "FR", (48.84, 2.32, 48.88, 2.38)),
    City("berlin", "Berlin", "DE", (52.49, 13.36, 52.55, 13.45)),
    City("sydney", "Sydney", "AU", (-33.92, 151.17, -33.85, 151.23)),
    City("melbourne", "Melbourne", "AU", (-37.83, 144.94, -37.79, 144.99)),
    City("singapore", "Singapore", "SG", (1.27, 103.83, 1.31, 103.87)),
]

# Available by name only (--city), not in the random daily rotation.
CITIES += [
    City("mountain_view", "Mountain View", "US", (37.355, -122.120, 37.430, -122.035),
         addr_city="Mountain View", in_daily_pool=False),
    City("palo_alto", "Palo Alto", "US", (37.380, -122.190, 37.465, -122.090),
         addr_city="Palo Alto", in_daily_pool=False),
    City("sunnyvale", "Sunnyvale", "US", (37.340, -122.060, 37.420, -121.980),
         addr_city="Sunnyvale", in_daily_pool=False),
    City("santa_clara", "Santa Clara", "US", (37.320, -122.000, 37.400, -121.930),
         addr_city="Santa Clara", in_daily_pool=False),
]

BY_SLUG = {c.slug: c for c in CITIES}
DAILY_POOL = [c for c in CITIES if c.in_daily_pool]


def resolve_cities(spec: str) -> list[City]:
    """Comma list of slugs, e.g. 'mountain_view' or 'mountain_view,palo_alto'."""
    slugs = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [s for s in slugs if s not in BY_SLUG]
    if unknown:
        raise SystemExit(f"unknown city {unknown}; choose from {sorted(BY_SLUG)} "
                         "or add one to localbench/cities.py")
    return [BY_SLUG[s] for s in slugs]
