"""Fetch local businesses from OpenStreetMap via the keyless Overpass API.

One union query per city covers every selected category, so adding verticals
doesn't multiply load on the shared public Overpass instances."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from .categories import Category
from .cities import City
from .matching import digits, fold, host_of, is_shared_host

OVERPASS_ENDPOINTS = os.environ.get(
    "OVERPASS_ENDPOINTS",
    "https://overpass-api.de/api/interpreter,https://overpass.private.coffee/api/interpreter",
).split(",")
UA = os.environ.get("LOCALBENCH_UA", "localbench/0.4 (github.com/YOUR_ORG/localbench)")


@dataclass
class Business:
    osm_type: str
    osm_id: int
    name: str
    category: str
    vertical: str
    label: str
    cuisine: str | None
    housenumber: str
    street: str
    city: str
    city_slug: str
    postcode: str | None
    phone: str | None
    website: str | None
    is_chain: bool
    lat: float | None
    lon: float | None
    osm_timestamp: str | None
    kind: str = "business"

    @property
    def phone_digits(self) -> str:
        return digits(self.phone)

    @property
    def host(self) -> str | None:
        h = host_of(self.website)
        return h if h and not is_shared_host(h) else None

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


def build_query(city: City, categories: list[Category], newer: str | None = None, timeout: int = 180) -> str:
    s, w, n, e = city.bbox
    newer_clause = f'(newer:"{newer}")' if newer else ""
    pairs = sorted({t for c in categories for t in c.tags})
    lines = [
        f'  nwr["{k}"="{v}"]["name"]["addr:housenumber"]["addr:street"]{newer_clause}({s},{w},{n},{e});'
        for k, v in pairs
    ]
    return f"[out:json][timeout:{timeout}];\n(\n" + "\n".join(lines) + "\n);\nout center meta;"


def run_overpass(query: str, retries: int = 3) -> list[dict]:
    last = None
    for attempt in range(retries):
        for url in OVERPASS_ENDPOINTS:
            try:
                r = requests.post(url, data={"data": query}, headers={"User-Agent": UA}, timeout=240)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last = f"{url}: HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as ex:
                last = f"{url}: {ex}"
        time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last}")


def category_of(tags: dict, categories: list[Category]) -> Category | None:
    for c in categories:  # first match wins, in registry order
        if any(tags.get(k) == v for k, v in c.tags):
            return c
    return None


def parse_element(el: dict, city: City, categories: list[Category]) -> Business | None:
    tags = el.get("tags") or {}
    cat = category_of(tags, categories)
    name = (tags.get("name") or "").strip()
    if not cat or len(name) < 3:
        return None
    for k, bad in cat.exclude:
        if (tags.get(k) or "").split(";")[0].strip() in bad:
            return None
    tagged_city = tags.get("addr:city")
    if city.addr_city and tagged_city and fold(tagged_city) != fold(city.addr_city):
        return None  # inside the bbox but addressed to a neighboring city
    phone = (tags.get("phone") or tags.get("contact:phone") or "").split(";")[0].strip() or None
    website = (tags.get("website") or tags.get("contact:website") or tags.get("url") or "").split(";")[0].strip() or None
    center = el.get("center") or {}
    cuisine = (tags.get("cuisine") or "").split(";")[0].replace("_", " ").strip() or None
    return Business(
        osm_type=el["type"], osm_id=el["id"], name=name,
        category=cat.key, vertical=cat.vertical, label=cat.label, cuisine=cuisine,
        housenumber=tags["addr:housenumber"].split(";")[0].strip(),
        street=tags["addr:street"].strip(),
        city=tags.get("addr:city") or city.name, city_slug=city.slug,
        postcode=tags.get("addr:postcode"), phone=phone, website=website,
        is_chain=bool(tags.get("brand") or tags.get("brand:wikidata")),
        lat=el.get("lat", center.get("lat")), lon=el.get("lon", center.get("lon")),
        osm_timestamp=el.get("timestamp"),
    )


def fetch_businesses(city: City, categories: list[Category], newer: str | None = None) -> list[Business]:
    els = run_overpass(build_query(city, categories, newer))
    out = [b for el in els if (b := parse_element(el, city, categories))]
    time.sleep(3)  # be polite to the public Overpass instances
    return out
