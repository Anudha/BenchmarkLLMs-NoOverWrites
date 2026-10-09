"""Cross-check OSM gold against the business's own website. OSM tags can be
stale; a fact that also appears on the business's site is far more likely to
be what a good search engine should surface today."""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from .matching import address_match, phone_match
from .osm import UA

TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)


def html_to_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", raw)))


def verify_one(b, timeout: float = 10.0) -> dict:
    out = {"site_ok": False, "phone_on_site": False, "address_on_site": False, "final_url": None}
    if not b.host:
        return out
    url = b.website if b.website.startswith("http") else "https://" + b.website
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, allow_redirects=True)
    except requests.RequestException:
        return out
    ctype = r.headers.get("content-type", "")
    if r.status_code >= 400 or "html" not in ctype:
        return out
    raw = r.text[:3_000_000]
    text = html_to_text(raw)
    out["site_ok"] = True
    out["final_url"] = r.url
    phone = getattr(b, "phone_digits", "")
    if phone:
        # raw html too, to catch tel: links
        out["phone_on_site"] = phone_match(phone, text) or phone_match(phone, raw)
    if getattr(b, "housenumber", None):  # trails have no street address
        out["address_on_site"] = address_match(b.housenumber, b.street, text)
    return out


def verify_many(bs: list, workers: int = 16) -> dict[int, dict]:
    with ThreadPoolExecutor(workers) as ex:
        return dict(zip(((b.osm_type, b.osm_id) for b in bs), ex.map(verify_one, bs)))


def confidence(v: dict) -> str:
    if v.get("phone_on_site") or v.get("address_on_site"):
        return "high"
    return "medium" if v.get("site_ok") else "low"
