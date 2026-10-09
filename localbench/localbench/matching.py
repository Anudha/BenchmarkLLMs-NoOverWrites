"""Deterministic answer matchers. Used twice: to verify gold against the
business's own website at generation time, and to score engine output.
Snippet/answer-only checks are a lower bound, the same for every engine."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

TOKEN = re.compile(r"[a-z0-9]+")
PHONE_RE = re.compile(r"\+?\(?\d[\d\s().\-/]{5,}\d")

# Hosts shared by many businesses: a URL here says nothing about which business.
SHARED_HOSTS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "linktr.ee", "yelp.com", "tripadvisor.com", "google.com", "goo.gl",
    "maps.app.goo.gl", "ubereats.com", "doordash.com", "grubhub.com",
    "opentable.com", "resy.com", "toasttab.com", "order.toasttab.com",
    "square.site", "clover.com", "deliveroo.co.uk", "just-eat.co.uk",
    "thefork.com", "lieferando.de", "wolt.com", "menufy.com",
}

STREET_NORM = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "place": "pl", "court": "ct", "terrace": "ter",
    "highway": "hwy", "parkway": "pkwy", "square": "sq", "north": "n", "south": "s",
    "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
    "southeast": "se", "southwest": "sw", "rue": "rue", "strasse": "str",
}
STREET_STOP = {
    "st", "ave", "rd", "blvd", "dr", "ln", "pl", "ct", "ter", "hwy", "pkwy", "sq",
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "the", "of", "str", "rue", "de",
    "du", "la", "le", "des", "van", "straat", "weg", "gracht",
}
NAME_STOP = {
    "the", "restaurant", "restaurante", "ristorante", "and", "de", "la", "le",
    "el", "les", "los", "das", "der", "die", "an", "of",
}


def fold(s: str | None) -> str:
    s = (s or "").replace("ß", "ss").lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def host_of(url: str | None) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_shared_host(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in SHARED_HOSTS)


# ---- field matchers -------------------------------------------------------

def phone_match(gold_digits: str, text: str) -> bool:
    g = gold_digits
    if len(g) < 7:
        return False
    for m in PHONE_RE.finditer(text or ""):
        d = digits(m.group())
        k = min(9, len(g), len(d))
        if k >= 7 and d[-k:] == g[-k:]:
            return True
    return False


def _norm_tok(t: str) -> str:
    t = STREET_NORM.get(t, t)
    # German compounds: "hauptstrasse" / "hauptstr" -> "haupt"
    for suf in ("strasse", "str"):
        if t.endswith(suf) and len(t) > len(suf) + 2:
            return t[: -len(suf)]
    return t


def _skippable(t: str) -> bool:
    return t in STREET_STOP or len(t) == 1


def _street_core(street: str) -> list[str]:
    toks = [_norm_tok(t) for t in TOKEN.findall(fold(street))]
    return [t for t in toks if not _skippable(t)] or toks


def _seq_match(core: list[str], toks: list[str]) -> bool:
    """core tokens appear in order at the start of toks, skipping stop tokens."""
    j = 0
    for t in toks:
        if j == len(core):
            break
        c = core[j]
        if t == c or (len(c) >= 4 and t.startswith(c)):
            j += 1
        elif not _skippable(t):
            return False
    return j == len(core)


def address_match(housenumber: str, street: str, text: str) -> bool:
    """House number immediately followed by the street ("1234 Valencia St")
    or immediately preceded by it ("Hauptstr. 12")."""
    hn_toks = TOKEN.findall(fold(housenumber).split(";")[0])
    if not hn_toks:
        return False
    hn = hn_toks[0]
    core = _street_core(street)
    toks = [_norm_tok(t) for t in TOKEN.findall(fold(text))]
    span = len(core) + 4
    for i, t in enumerate(toks):
        if t != hn:
            continue
        if _seq_match(core, toks[i + 1: i + 1 + span]):
            return True
        if _seq_match(core[::-1], toks[max(0, i - span): i][::-1]):
            return True
    return False


def _name_core(name: str) -> list[str]:
    toks = [t for t in TOKEN.findall(fold(name)) if len(t) > 1]
    core = [t for t in toks if t not in NAME_STOP]
    return core or toks


def name_match(name: str, text: str) -> bool:
    core = _name_core(name)
    if not core:
        return False
    toks = set(TOKEN.findall(fold(text)))
    return all(c in toks for c in core)


def website_match(domain: str, text: str, url: str | None = None) -> bool:
    h = host_of(url)
    if h and (h == domain or h.endswith("." + domain)):
        return True
    pat = rf"(?<![a-z0-9.\-]){re.escape(domain)}(?![a-z0-9\-])"
    return re.search(pat, fold(text)) is not None


LENGTH_RE = re.compile(
    r"(\d{1,4}(?:[.,]\d+)?)\s*-?\s*(km|kms|kilometers?|kilometres?|mi|miles?)\b", re.I)

SURFACE_WORDS = {
    "paved": ["paved", "asphalt", "concrete", "blacktop", "tarmac"],
    "gravel": ["gravel", "crushed stone", "crushed limestone", "decomposed granite", "compacted"],
    "dirt": ["dirt", "unpaved", "natural surface", "singletrack", "single track", "earth"],
}


def length_match(gold_km: str | float, text: str, tol: float = 0.25) -> bool:
    """Any length in the text within +/-tol of the gold length (mi or km)."""
    try:
        g = float(gold_km)
    except (TypeError, ValueError):
        return False
    for m in LENGTH_RE.finditer(text or ""):
        x = float(m.group(1).replace(",", "."))
        km = x * 1.609344 if m.group(2).lower().startswith("mi") else x
        if g > 0 and abs(km - g) / g <= tol:
            return True
    return False


def surface_match(gold_class: str, text: str) -> bool:
    t = fold(text)
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t) for w in SURFACE_WORDS.get(gold_class, []))


def match(gold: dict, text: str, url: str | None = None) -> bool:
    """gold is one flat gold row (CSV); text is title+snippet or a model's answer."""
    f = gold["field"]
    if f == "phone":
        g = gold["gold_phone_digits"]
        return phone_match(g, text) or phone_match(g, url or "")
    if f == "website":
        return website_match(gold["gold_host"], text, url)
    if f == "address":
        return address_match(gold["gold_housenumber"], gold["gold_street"], text)
    if f == "name":
        return name_match(gold["gold_name"], text)
    if f == "length":
        return length_match(gold["gold_length_km"], text)
    if f == "surface":
        return surface_match(gold["gold_surface"], text)
    if f == "operator":
        return name_match(gold["gold_operator"], text)
    raise ValueError(f"unknown field {f}")
