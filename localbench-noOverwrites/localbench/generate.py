"""Build one day's query set: up to N businesses per vertical, one
known-answer query each.

Buckets:
  stable  - any eligible business in today's cities
  fresh   - OSM objects edited in the last `fresh_days` days
Fields: phone, address, website (lookups by name) and name (reverse lookup
from category + street address). Styles: keyword or natural question."""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import date as Date
from datetime import timedelta
from typing import Callable

from .categories import VERTICALS
from .cities import DAILY_POOL, City
from .osm import Business, fetch_businesses
from .verify import confidence, verify_many

TEMPLATES = {
    "phone": (
        "{name} {city} phone number",
        "What is the phone number for {name} on {street} in {city}?",
    ),
    "address": (
        "{name} {city} address",
        "What is the street address of the {kind} {name} in {city}?",
    ),
    "website": (
        "{name} {city} official website",
        "What is the official website of {name}, the {kind} in {city}?",
    ),
    "name": (
        "{kind} {housenumber} {street} {city}",
        "Which {kind} is located at {housenumber} {street} in {city}? Give its name.",
    ),
}


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:16], 16)


def kind(b: Business) -> str:
    if b.category == "restaurant" and b.cuisine:
        return f"{b.cuisine} restaurant"
    return b.label


def _eligible(b: Business) -> bool:
    return not b.is_chain and (len(b.phone_digits) >= 7 or b.host is not None)


def _askable(b: Business, v: dict, require_verified: bool) -> list[str]:
    fields = ["address", "name"]
    if len(b.phone_digits) >= 7 and (v.get("phone_on_site") or not require_verified):
        fields.append("phone")
    if b.host and v.get("site_ok"):
        fields.append("website")
    return fields


def gold_row(b: Business, field: str, v: dict, verified: bool) -> dict:
    row = {
        "field": field,
        "vertical": b.vertical, "category": b.category,
        "confidence": confidence(v) if verified else "unverified",
        "site_ok": v.get("site_ok"), "phone_on_site": v.get("phone_on_site"),
        "address_on_site": v.get("address_on_site"),
        "osm_url": b.osm_url, "osm_timestamp": b.osm_timestamp,
        "biz_name": b.name, "biz_phone": b.phone, "biz_website": b.website,
        "biz_housenumber": b.housenumber, "biz_street": b.street, "biz_city": b.city,
        "biz_postcode": b.postcode, "biz_cuisine": b.cuisine, "biz_lat": b.lat, "biz_lon": b.lon,
    }
    if field == "phone":
        row.update(answer=b.phone, gold_phone_digits=b.phone_digits)
    elif field == "website":
        row.update(answer=b.host, gold_host=b.host)
    elif field == "address":
        row.update(answer=f"{b.housenumber} {b.street}", gold_housenumber=b.housenumber, gold_street=b.street)
    else:
        row.update(answer=b.name, gold_name=b.name)
    return row


def _pick(cands: list[Business], k: int, cap: int, city_count: Counter) -> list[Business]:
    """Take k candidates honoring a per-city cap; relax the cap if short."""
    out, seen = [], set()
    for relaxed in (False, True):
        for b in cands:
            if len(out) >= k:
                return out
            if b.osm_id in seen or (not relaxed and city_count[b.city_slug] >= cap):
                continue
            out.append(b)
            seen.add(b.osm_id)
            city_count[b.city_slug] += 1
    return out


def generate(
    day: str,
    verticals: list[str],
    n: int = 20,
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
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Returns (queries, gold, summary_rows, errors).

    Each run has its own run_id (default "<day>-r1"). The sample is seeded by
    run_id, so a second run on the same day draws a different sample, and
    query_ids include run_id so they're unique across every run ever made."""
    run_id = run_id or f"{day}-r1"
    rng = random.Random(_seed(salt, run_id))
    cats = [c for v in verticals for c in VERTICALS[v]]
    # Explicit cities (--city) override the random daily rotation.
    cities = list(cities) if cities else rng.sample(DAILY_POOL, cities_per_day)
    since = (Date.fromisoformat(day) - timedelta(days=fresh_days)).isoformat() + "T00:00:00Z"

    # pools[(vertical, bucket)] -> businesses
    pools: dict[tuple[str, str], list[Business]] = defaultdict(list)
    errors: list[str] = []
    for c in cities:
        try:
            fresh = [b for b in fetcher(c, cats, newer=since) if _eligible(b)]
            fresh_ids = {b.osm_id for b in fresh}
            stable = [b for b in fetcher(c, cats) if _eligible(b) and b.osm_id not in fresh_ids]
        except Exception as ex:  # one bad city shouldn't kill the day
            errors.append(f"{c.slug}: {ex}")
            continue
        for b in fresh:
            pools[(b.vertical, "fresh")].append(b)
        for b in stable:
            pools[(b.vertical, "stable")].append(b)

    # Names repeated anywhere in today's pool are probably untagged chains.
    name_counts = Counter(b.name.lower() for p in pools.values() for b in p)
    for key in pools:
        pools[key] = sorted((b for b in pools[key] if name_counts[b.name.lower()] == 1), key=lambda b: b.osm_id)
        random.Random(_seed(salt, run_id, *key)).shuffle(pools[key])

    live_cities = max(1, len(cities) - len(errors))
    cap = math.ceil(n / live_cities) + 1

    # Over-sample every vertical, then verify all candidates in one parallel batch.
    plan: dict[str, dict[str, list[Business]]] = {}
    for v in verticals:
        n_fresh = min(round(n * fresh_share), len(pools[(v, "fresh")]))
        plan[v] = {
            "fresh": pools[(v, "fresh")][: n_fresh * oversample],
            "stable": pools[(v, "stable")][: (n - n_fresh) * oversample + 5],
            "n_fresh": n_fresh,  # type: ignore[dict-item]
        }
    all_cands = [b for p in plan.values() for bucket in ("fresh", "stable") for b in p[bucket]]
    ver = verify_many(all_cands) if verify else {}
    rank = {"high": 0, "medium": 1, "low": 2}

    def by_conf(bs: list[Business]) -> list[Business]:
        return sorted(bs, key=lambda b: rank[confidence(ver.get(b.osm_id, {}))])

    queries, gold, summary = [], [], []
    for v in verticals:
        p = plan[v]
        city_count: Counter = Counter()
        chosen = [("fresh", b) for b in _pick(by_conf(p["fresh"]), p["n_fresh"], cap, city_count)]
        chosen += [("stable", b) for b in _pick(by_conf(p["stable"]), n - len(chosen), cap, city_count)]

        vrng = random.Random(_seed(salt, run_id, v, "fields"))
        field_count: Counter = Counter()
        style_count: Counter = Counter()
        for bucket, b in chosen:
            vr = ver.get(b.osm_id, {})
            field = min(_askable(b, vr, require_verified), key=lambda f: (field_count[f], vrng.random()))
            field_count[field] += 1
            style = "keyword" if style_count[(field, "keyword")] <= style_count[(field, "natural")] else "natural"
            style_count[(field, style)] += 1
            tmpl = TEMPLATES[field][0 if style == "keyword" else 1]
            q = tmpl.format(name=b.name, city=b.city, street=b.street, housenumber=b.housenumber, kind=kind(b))
            qid = hashlib.sha1(f"{run_id}|{b.osm_type}/{b.osm_id}|{field}".encode()).hexdigest()[:12]
            queries.append({
                "query_id": qid, "run_id": run_id, "date": day, "vertical": v, "category": b.category,
                "query": q, "field": field, "style": style, "bucket": bucket, "city": b.city_slug,
            })
            gold.append({"query_id": qid, "run_id": run_id, "date": day, **gold_row(b, field, vr, verify)})

        summary.append({
            "run_id": run_id, "date": day, "vertical": v, "n_queries": len(chosen),
            "n_fresh": sum(1 for bk, _ in chosen if bk == "fresh"),
            "pool_stable": len(pools[(v, "stable")]), "pool_fresh": len(pools[(v, "fresh")]),
            "fields": ";".join(f"{k}:{c}" for k, c in sorted(field_count.items())),
            "cities": ";".join(c.slug for c in cities),
        })
    return queries, gold, summary, errors
