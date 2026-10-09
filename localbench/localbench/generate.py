"""Build one run's query set: up to N entities per vertical, one
known-answer query each.

Buckets:
  stable  - any eligible entity in the run's cities
  fresh   - OSM objects edited in the last `fresh_days` days
Business fields: phone, address, website, name (reverse lookup).
Trail fields: length, surface, website, operator, name (reverse lookup
from the trail's endpoints).
Styles: keyword or natural-language question."""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import date as Date
from datetime import timedelta
from typing import Callable

from .categories import VERTICAL_KIND, business_categories
from .cities import DAILY_POOL, City
from .osm import fetch_businesses
from .trails import fetch_trails
from .verify import confidence, verify_many

TEMPLATES = {
    # ---- businesses
    ("business", "phone"): (
        "{name} {city} phone number",
        "What is the phone number for {name} on {street} in {city}?",
    ),
    ("business", "address"): (
        "{name} {city} address",
        "What is the street address of the {kind} {name} in {city}?",
    ),
    ("business", "website"): (
        "{name} {city} official website",
        "What is the official website of {name}, the {kind} in {city}?",
    ),
    ("business", "name"): (
        "{kind} {housenumber} {street} {city}",
        "Which {kind} is located at {housenumber} {street} in {city}? Give its name.",
    ),
    # ---- trails
    ("trail", "length"): (
        "{name} {city} bike trail length",
        "How long is the {name} bike trail near {city}?",
    ),
    ("trail", "surface"): (
        "{name} {city} bike trail surface paved gravel",
        "Is the {name} bike trail near {city} paved, gravel, or dirt?",
    ),
    ("trail", "website"): (
        "{name} {city} bike trail official website",
        "What is the official website for the {name} bike trail near {city}?",
    ),
    ("trail", "operator"): (
        "{name} {city} bike trail operator",
        "Which organization operates or maintains the {name} bike trail near {city}?",
    ),
    ("trail", "name"): (
        "bike trail from {from_} to {to} {city}",
        "Which bike trail runs from {from_} to {to} near {city}? Give its name.",
    ),
}

LENGTH_CONFIDENCE = {"tag": "high", "relation_geometry": "medium", "path_geometry": "low"}


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:16], 16)


def _key(e) -> tuple[str, int]:
    return (e.osm_type, e.osm_id)


def kind_label(e) -> str:
    return e.label


def _eligible(e) -> bool:
    if e.kind == "trail":
        return True  # trails.py already enforces name + length bounds
    return not e.is_chain and (len(e.phone_digits) >= 7 or e.host is not None)


def _askable(e, v: dict, require_verified: bool) -> list[str]:
    if e.kind == "trail":
        fields = ["length"]
        if e.surface:
            fields.append("surface")
        if e.host and (v.get("site_ok") or not v):
            fields.append("website")
        if e.operator:
            fields.append("operator")
        if e.from_ and e.to:
            fields.append("name")
        return fields
    fields = ["address", "name"]
    if len(e.phone_digits) >= 7 and (v.get("phone_on_site") or not require_verified):
        fields.append("phone")
    if e.host and v.get("site_ok"):
        fields.append("website")
    return fields


def _confidence(e, field: str, v: dict, verified: bool) -> str:
    if e.kind == "trail":
        if field == "length":
            return LENGTH_CONFIDENCE[e.length_source]
        if field == "website" and v.get("site_ok"):
            return "high"
        return "medium"  # taken directly from OSM tags
    return confidence(v) if verified else "unverified"


def gold_row(e, field: str, v: dict, verified: bool) -> dict:
    row = {
        "field": field, "vertical": e.vertical, "category": e.category,
        "confidence": _confidence(e, field, v, verified),
        "site_ok": v.get("site_ok"), "phone_on_site": v.get("phone_on_site"),
        "address_on_site": v.get("address_on_site"),
        "osm_url": e.osm_url, "osm_timestamp": e.osm_timestamp,
        "entity_kind": e.kind, "entity_name": e.name, "entity_website": e.website,
        "entity_city": e.city, "entity_lat": e.lat, "entity_lon": e.lon,
    }
    if e.kind == "business":
        row.update(entity_phone=e.phone, entity_housenumber=e.housenumber, entity_street=e.street,
                   entity_postcode=e.postcode, entity_cuisine=e.cuisine)
    else:
        row.update(trail_length_km=e.length_km, trail_length_source=e.length_source,
                   trail_surface=e.surface_tag, trail_operator=e.operator,
                   trail_from=e.from_, trail_to=e.to, trail_network=e.network)

    if field == "phone":
        row.update(answer=e.phone, gold_phone_digits=e.phone_digits)
    elif field == "website":
        row.update(answer=e.host, gold_host=e.host)
    elif field == "address":
        row.update(answer=f"{e.housenumber} {e.street}", gold_housenumber=e.housenumber, gold_street=e.street)
    elif field == "length":
        row.update(answer=f"{e.length_km:.1f} km ({e.length_km / 1.609344:.1f} mi)", gold_length_km=e.length_km)
    elif field == "surface":
        row.update(answer=e.surface, gold_surface=e.surface)
    elif field == "operator":
        row.update(answer=e.operator, gold_operator=e.operator)
    else:
        row.update(answer=e.name, gold_name=e.name)
    return row


def _pick(cands: list, k: int, cap: int, city_count: Counter) -> list:
    """Take k candidates honoring a per-city cap; relax the cap if short."""
    out, seen = [], set()
    for relaxed in (False, True):
        for e in cands:
            if len(out) >= k:
                return out
            if _key(e) in seen or (not relaxed and city_count[e.city_slug] >= cap):
                continue
            out.append(e)
            seen.add(_key(e))
            city_count[e.city_slug] += 1
    return out


def generate(
    day: str,
    verticals: list[str],
    n: int = 20,
    total: int | None = None,
    fresh_share: float = 0.3,
    fresh_days: int = 30,
    cities_per_day: int = 6,
    cities: list[City] | None = None,
    run_id: str | None = None,
    salt: str = "",
    verify: bool = True,
    require_verified: bool = False,
    oversample: int = 2,
    fetcher: Callable = fetch_businesses,
    trail_fetcher: Callable = fetch_trails,
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Returns (queries, gold, summary_rows, errors).

    Each run has its own run_id (default "<day>-r1"). The sample is seeded by
    run_id, so a second run on the same day draws a different sample, and
    query_ids include run_id so they're unique across every run ever made."""
    run_id = run_id or f"{day}-r1"
    rng = random.Random(_seed(salt, run_id))
    cities = list(cities) if cities else rng.sample(DAILY_POOL, cities_per_day)
    since = (Date.fromisoformat(day) - timedelta(days=fresh_days)).isoformat() + "T00:00:00Z"
    biz_cats = business_categories(verticals)
    want_trails = any(VERTICAL_KIND[v] == "trail" for v in verticals)

    pools: dict[tuple[str, str], list] = defaultdict(list)
    errors: list[str] = []
    for c in cities:
        if biz_cats:
            try:
                fresh = [b for b in fetcher(c, biz_cats, newer=since) if _eligible(b)]
                fresh_ids = {b.osm_id for b in fresh}
                stable = [b for b in fetcher(c, biz_cats) if _eligible(b) and b.osm_id not in fresh_ids]
                for b in fresh:
                    pools[(b.vertical, "fresh")].append(b)
                for b in stable:
                    pools[(b.vertical, "stable")].append(b)
            except Exception as ex:  # one bad city shouldn't kill the run
                errors.append(f"{c.slug} businesses: {ex}")
        if want_trails:
            try:
                for t in trail_fetcher(c, since):
                    pools[("bike_trails", "fresh" if t.fresh else "stable")].append(t)
            except Exception as ex:
                errors.append(f"{c.slug} trails: {ex}")

    # Business names repeated anywhere in the pool are probably untagged chains.
    # Trails sharing a name across cities are distinct trails; keep one per name.
    biz_names = Counter(e.name.lower() for p in pools.values() for e in p if e.kind == "business")
    seen_trails: set[str] = set()
    for key in sorted(pools):
        kept = []
        for e in sorted(pools[key], key=_key):
            if e.kind == "business" and biz_names[e.name.lower()] > 1:
                continue
            if e.kind == "trail":
                if e.name.lower() in seen_trails:
                    continue
                seen_trails.add(e.name.lower())
            kept.append(e)
        pools[key] = kept
        random.Random(_seed(salt, run_id, *key)).shuffle(pools[key])

    live_cities = max(1, len({c.slug for c in cities}) - len({e.split()[0] for e in errors}))
    # quota per vertical: n each, or `total` split as evenly as possible
    # (20 over 3 verticals -> 7, 7, 6; the remainder rotates with the run seed)
    if total is not None:
        base, extra = divmod(total, len(verticals))
        lucky = set(random.Random(_seed(salt, run_id, "quota")).sample(verticals, extra))
        quota = {v: base + (v in lucky) for v in verticals}
    else:
        quota = {v: n for v in verticals}
    cap = math.ceil(max(quota.values()) / live_cities) + 1

    # Over-sample every vertical, then verify all candidates in one parallel batch.
    plan: dict[str, dict] = {}
    for v in verticals:
        n = quota[v]
        n_fresh = min(round(n * fresh_share), len(pools[(v, "fresh")]))
        plan[v] = {
            "fresh": pools[(v, "fresh")][: n_fresh * oversample],
            "stable": pools[(v, "stable")][: (n - n_fresh) * oversample + 5],
            "n_fresh": n_fresh,
        }
    to_verify = [e for p in plan.values() for b in ("fresh", "stable") for e in p[b]
                 if e.kind == "business" or e.host]
    ver = verify_many(to_verify) if verify and to_verify else {}
    rank = {"high": 0, "medium": 1, "low": 2}

    def by_conf(es: list) -> list:
        return sorted(es, key=lambda e: rank[confidence(ver.get(_key(e), {}))] if e.kind == "business" else 0)

    queries, gold, summary = [], [], []
    for v in verticals:
        p, n = plan[v], quota[v]
        city_count: Counter = Counter()
        chosen = [("fresh", e) for e in _pick(by_conf(p["fresh"]), p["n_fresh"], cap, city_count)]
        chosen += [("stable", e) for e in _pick(by_conf(p["stable"]), n - len(chosen), cap, city_count)]

        vrng = random.Random(_seed(salt, run_id, v, "fields"))
        field_count: Counter = Counter()
        style_count: Counter = Counter()
        for bucket, e in chosen:
            vr = ver.get(_key(e), {})
            field = min(_askable(e, vr, require_verified), key=lambda f: (field_count[f], vrng.random()))
            field_count[field] += 1
            style = "keyword" if style_count[(field, "keyword")] <= style_count[(field, "natural")] else "natural"
            style_count[(field, style)] += 1
            tmpl = TEMPLATES[(e.kind, field)][0 if style == "keyword" else 1]
            q = tmpl.format(name=e.name, city=e.city, kind=kind_label(e),
                            street=getattr(e, "street", ""), housenumber=getattr(e, "housenumber", ""),
                            from_=getattr(e, "from_", ""), to=getattr(e, "to", ""))
            qid = hashlib.sha1(f"{run_id}|{e.osm_type}/{e.osm_id}|{field}".encode()).hexdigest()[:12]
            queries.append({
                "query_id": qid, "run_id": run_id, "date": day, "vertical": v, "category": e.category,
                "query": q, "field": field, "style": style, "bucket": bucket, "city": e.city_slug,
            })
            gold.append({"query_id": qid, "run_id": run_id, "date": day, **gold_row(e, field, vr, verify)})

        summary.append({
            "run_id": run_id, "date": day, "vertical": v, "n_queries": len(chosen),
            "n_fresh": sum(1 for bk, _ in chosen if bk == "fresh"),
            "pool_stable": len(pools[(v, "stable")]), "pool_fresh": len(pools[(v, "fresh")]),
            "fields": ";".join(f"{k}:{c}" for k, c in sorted(field_count.items())),
            "cities": ";".join(c.slug for c in cities),
        })
    return queries, gold, summary, errors
