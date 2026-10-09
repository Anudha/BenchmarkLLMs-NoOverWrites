"""Bike trails from OpenStreetMap.

Two sources, merged by name:
  1. Bicycle route relations (route=bicycle) that touch the city bbox.
     National/international long-distance routes (network icn/ncn) are
     skipped: they're thousands of km and too heavy to download.
  2. Named cycleways and bike-designated paths, grouped by name. These are
     fetched from a padded bbox so a trail that runs past the city edge is
     measured more completely.

A relation and a path group with the same name are the same trail; the
relation wins because its membership is complete.

Trail facts used as labels:
  length_km   - the relation's `distance` tag if present, else computed from
                geometry (each way counted once)
  surface     - asphalt/concrete -> paved; gravel/compacted -> gravel;
                dirt/ground -> dirt (majority over segments for path groups)
  operator, website, from/to - straight from OSM tags when present"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .cities import City
from .matching import fold, host_of, is_shared_host
from .osm import run_overpass

MIN_KM, MAX_KM = 1.0, 300.0
PAD_DEG = 0.1  # ~10 km padding for path groups

SURFACE_CLASS = {
    **dict.fromkeys(["asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes",
                     "paving_stones", "chipseal"], "paved"),
    **dict.fromkeys(["gravel", "fine_gravel", "compacted", "pebblestone", "unpaved:gravel"], "gravel"),
    **dict.fromkeys(["dirt", "ground", "earth", "unpaved", "grass", "sand", "mud", "woodchips"], "dirt"),
}


@dataclass
class Trail:
    osm_type: str
    osm_id: int
    name: str
    category: str          # bike_route | bike_path
    city: str
    city_slug: str
    length_km: float
    length_source: str     # tag | relation_geometry | path_geometry
    surface: str | None    # paved | gravel | dirt
    surface_tag: str | None
    operator: str | None
    website: str | None
    from_: str | None
    to: str | None
    network: str | None
    lat: float | None
    lon: float | None
    osm_timestamp: str | None
    fresh: bool = False
    vertical: str = "bike_trails"
    label: str = "bike trail"
    kind: str = "trail"
    is_chain: bool = False

    @property
    def host(self) -> str | None:
        h = host_of(self.website)
        return h if h and not is_shared_host(h) else None

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


# ---- geometry ---------------------------------------------------------------------

def haversine_km(a: dict, b: dict) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def line_km(geom: list[dict]) -> float:
    return sum(haversine_km(a, b) for a, b in zip(geom, geom[1:]))


def parse_distance_tag(v: str | None) -> float | None:
    """OSM `distance` is km by default; also accepts '12 mi' / '12.5 km'."""
    if not v:
        return None
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(km|mi|miles?)?\s*$", v.lower())
    if not m:
        return None
    x = float(m.group(1).replace(",", "."))
    return x * 1.609344 if m.group(2) and m.group(2).startswith("mi") else x


def surface_class(tag: str | None) -> str | None:
    return SURFACE_CLASS.get((tag or "").split(";")[0].strip().lower())


def _in_bbox(pt: dict, bbox) -> bool:
    s, w, n, e = bbox
    return s <= pt["lat"] <= n and w <= pt["lon"] <= e


def _first(v: str | None) -> str | None:
    v = (v or "").split(";")[0].strip()
    return v or None


# ---- Overpass ------------------------------------------------------------------------

def build_trail_query(city: City, newer: str | None = None, ids_only: bool = False, timeout: int = 180) -> str:
    s, w, n, e = city.bbox
    ps, pw, pn, pe = s - PAD_DEG, w - PAD_DEG, n + PAD_DEG, e + PAD_DEG
    nc = f'(newer:"{newer}")' if newer else ""
    out = "out tags;" if ids_only else "out meta geom;"
    return (
        f"[out:json][timeout:{timeout}];\n(\n"
        f'  rel["route"="bicycle"]["name"]["network"!~"^(icn|ncn)$"]{nc}({s},{w},{n},{e});\n'
        f'  way["highway"="cycleway"]["name"]{nc}({ps},{pw},{pn},{pe});\n'
        f'  way["highway"="path"]["bicycle"="designated"]["name"]{nc}({ps},{pw},{pn},{pe});\n'
        f");\n{out}"
    )


def parse_trails(elements: list[dict], city: City, fresh_rel_ids: set[int] = frozenset(),
                 fresh_way_ids: set[int] = frozenset()) -> list[Trail]:
    trails: dict[str, Trail] = {}

    # 1. relations
    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if len(name) < 3:
            continue
        seen, km, pts = set(), 0.0, []
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("geometry") and m["ref"] not in seen:
                seen.add(m["ref"])
                geom = [p for p in m["geometry"] if p]
                km += line_km(geom)
                pts += geom
        tagged = parse_distance_tag(tags.get("distance"))
        length, source = (tagged, "tag") if tagged else (km, "relation_geometry")
        center = pts[len(pts) // 2] if pts else {}
        trails[fold(name)] = Trail(
            osm_type="relation", osm_id=el["id"], name=name, category="bike_route",
            city=city.name, city_slug=city.slug, length_km=round(length, 2), length_source=source,
            surface=surface_class(tags.get("surface")), surface_tag=_first(tags.get("surface")),
            operator=_first(tags.get("operator")), website=_first(tags.get("website")),
            from_=_first(tags.get("from")), to=_first(tags.get("to")), network=tags.get("network"),
            lat=center.get("lat"), lon=center.get("lon"), osm_timestamp=el.get("timestamp"),
            fresh=el["id"] in fresh_rel_ids,
        )

    # 2. named paths, grouped by name
    groups: dict[str, list[dict]] = defaultdict(list)
    for el in elements:
        if el.get("type") == "way" and el.get("geometry") and (el.get("tags") or {}).get("name"):
            groups[fold(el["tags"]["name"].strip())].append(el)
    for key, ways in groups.items():
        if key in trails:
            continue  # relation already covers this trail
        if not any(_in_bbox(p, city.bbox) for w in ways for p in w["geometry"] if p):
            continue  # only in the padding: belongs to a neighboring area
        uniq = {w["id"]: w for w in ways}.values()
        km = sum(line_km([p for p in w["geometry"] if p]) for w in uniq)
        surfaces = Counter(surface_class(w["tags"].get("surface")) for w in uniq)
        surfaces.pop(None, None)
        tag_of = lambda k: next((_first(w["tags"].get(k)) for w in uniq if w["tags"].get(k)), None)
        first = min(uniq, key=lambda w: w["id"])
        mid = first["geometry"][len(first["geometry"]) // 2]
        trails[key] = Trail(
            osm_type="way", osm_id=first["id"], name=first["tags"]["name"].strip(), category="bike_path",
            city=city.name, city_slug=city.slug, length_km=round(km, 2), length_source="path_geometry",
            surface=surfaces.most_common(1)[0][0] if surfaces else None,
            surface_tag=tag_of("surface"), operator=tag_of("operator"), website=tag_of("website"),
            from_=None, to=None, network=None, lat=mid.get("lat"), lon=mid.get("lon"),
            osm_timestamp=max((w.get("timestamp") or "") for w in uniq) or None,
            fresh=any(w["id"] in fresh_way_ids for w in uniq),
        )

    return [t for t in trails.values() if MIN_KM <= t.length_km <= MAX_KM]


def fetch_trails(city: City, since: str) -> list[Trail]:
    """Two Overpass calls: full geometry, then ids edited since `since`."""
    elements = run_overpass(build_trail_query(city))
    recent = run_overpass(build_trail_query(city, newer=since, ids_only=True))
    fresh_rel = {e["id"] for e in recent if e.get("type") == "relation"}
    fresh_way = {e["id"] for e in recent if e.get("type") == "way"}
    return parse_trails(elements, city, fresh_rel, fresh_way)
