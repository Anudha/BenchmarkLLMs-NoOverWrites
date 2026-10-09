"""Free engine stack.

  ddgs       - keyless web search via the `ddgs` metasearch library
               (DuckDuckGo by default; set LOCALBENCH_DDGS_BACKEND to e.g.
               "brave", "google", "mojeek" or "auto"). Unofficial: it scrapes
               public result pages, so it can be rate-limited.
  groq       - Groq Compound, a model with built-in web search. Free API key
               (GROQ_API_KEY), no card; free tier ~250 requests/day.
               Scored on its final answer and on the pages it searched.
  nominatim  - OpenStreetMap's own search. The gold labels come from OSM, so
               this is a reference ceiling, not a competitor. It gets a light
               query rewrite (question words removed), since it's a geocoder.

Add your own: a function (query: str, k: int) -> {"results": [...]} and/or
{"answer": ...} (or {"error": ...}), registered in ENGINES."""

from __future__ import annotations

import os
import re
import time

import requests

REPO = os.environ.get("LOCALBENCH_REPO", "")  # e.g. "your-org/localbench"
BLOCKED = [d for d in os.environ.get(
    "LOCALBENCH_BLOCKED_DOMAINS", "github.com,raw.githubusercontent.com,huggingface.co").split(",") if d]
UA = os.environ.get("LOCALBENCH_UA", "localbench/0.4 (github.com/YOUR_ORG/localbench)")


def _drop_self(results: list[dict]) -> list[dict]:
    """Never let an engine score by finding this benchmark's own files."""
    if not REPO:
        return results
    return [r for r in results if REPO.lower() not in (r.get("url") or "").lower()]


# ---- ddgs --------------------------------------------------------------------------------

def ddgs_to_results(raw: list[dict]) -> list[dict]:
    return [{"url": r.get("href") or r.get("url"), "title": r.get("title", ""),
             "snippet": r.get("body") or r.get("snippet", "")} for r in raw or []]


def ddgs(query: str, k: int) -> dict:
    from ddgs import DDGS  # imported lazily so tests don't need network libs

    backend = os.environ.get("LOCALBENCH_DDGS_BACKEND", "duckduckgo")
    raw = DDGS(timeout=15).text(query, max_results=k, backend=backend)
    return {"results": _drop_self(ddgs_to_results(raw))}


# ---- groq ----------------------------------------------------------------------------------

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PROMPT = ("Use web search to answer this question about a local place.\n\n{q}\n\n"
          "End your reply with one line: ANSWER: <just the answer>")


def parse_groq(body: dict) -> dict:
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    text = msg.get("content") or ""
    found = re.findall(r"ANSWER:\s*(.+)", text)
    results = []
    for tool in msg.get("executed_tools") or []:
        sr = tool.get("search_results")
        items = sr.get("results", []) if isinstance(sr, dict) else (sr or [])
        for x in items:
            if isinstance(x, dict) and x.get("url"):
                results.append({"url": x["url"], "title": x.get("title", ""),
                                "snippet": (x.get("content") or "")[:1000]})
    return {"answer": found[-1].strip() if found else text.strip()[-300:], "results": _drop_self(results)}


def groq(query: str, k: int) -> dict:
    body = {"model": os.environ.get("LOCALBENCH_GROQ_MODEL", "groq/compound"),
            "messages": [{"role": "user", "content": PROMPT.format(q=query)}],
            "search_settings": {"exclude_domains": BLOCKED}}
    headers = {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}
    r = requests.post(GROQ_URL, json=body, headers=headers, timeout=120)
    if r.status_code == 400 and "search_settings" in r.text:  # older API: retry without it
        body.pop("search_settings")
        r = requests.post(GROQ_URL, json=body, headers=headers, timeout=120)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return parse_groq(r.json())


# ---- nominatim -------------------------------------------------------------------------

_STRIP = sorted([
    "what is the phone number for", "what is the street address of the", "what is the official website of",
    "what is the official website for the", "which organization operates or maintains the",
    "how long is the", "is the", "which bike trail runs from", "is located at", "give its name",
    "phone number", "official website", "address", "bike trail length", "bike trail surface paved gravel",
    "bike trail operator", "paved, gravel, or dirt", "bike trail", "which", "the ", " near ", " to ",
], key=len, reverse=True)


def nominatim_query(q: str) -> str:
    s = " " + q.lower() + " "
    for p in _STRIP:
        s = s.replace(p, " ")
    return re.sub(r"[?,.]", " ", re.sub(r"\s+", " ", s)).strip()


def nominatim_to_results(raw: list[dict]) -> list[dict]:
    out = []
    for x in raw or []:
        a, t = x.get("address") or {}, x.get("extratags") or {}
        city = a.get("city") or a.get("town") or a.get("village") or ""
        bits = [x.get("name") or "", f"{a.get('house_number', '')} {a.get('road', '')}, {city}".strip(" ,")]
        for label, key in (("Phone", "phone"), ("Phone", "contact:phone"), ("Website", "website"),
                           ("Website", "contact:website"), ("Surface", "surface"),
                           ("Operator", "operator"), ("Distance", "distance")):
            if t.get(key):
                bits.append(f"{label}: {t[key]}{' km' if key == 'distance' else ''}")
        out.append({"url": f"https://www.openstreetmap.org/{x.get('osm_type')}/{x.get('osm_id')}",
                    "title": x.get("display_name", ""), "snippet": ". ".join(b for b in bits if b)})
    return out


def nominatim(query: str, k: int) -> dict:
    r = requests.get("https://nominatim.openstreetmap.org/search", timeout=30, headers={"User-Agent": UA},
                     params={"q": nominatim_query(query), "format": "jsonv2", "addressdetails": 1,
                             "extratags": 1, "limit": k})
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return {"results": nominatim_to_results(r.json())}


# ---- registry ---------------------------------------------------------------------------------

ENGINES = {"ddgs": ddgs, "groq": groq, "nominatim": nominatim}
REQUIRES = {"groq": "GROQ_API_KEY"}            # engines that need a key
PAUSE = {"ddgs": 2.0, "groq": 2.5, "nominatim": 1.2}  # seconds between queries (free-tier limits)


def available(names: list[str]) -> tuple[list[str], list[str]]:
    """Split requested engines into (runnable, skipped-for-missing-key)."""
    ok, skipped = [], []
    for n in names:
        if n not in ENGINES:
            raise SystemExit(f"unknown engine {n}; choose from {list(ENGINES)}")
        (skipped if REQUIRES.get(n) and not os.environ.get(REQUIRES[n]) else ok).append(n)
    return ok, skipped


def run_engine(name: str, queries: list[dict], k: int = 5) -> list[dict]:
    fn = ENGINES[name]
    out = []
    for q in queries:  # sequential, so latency is comparable across engines
        t0 = time.time()
        try:
            pred = fn(q["query"], k)
        except Exception as ex:
            pred = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        pred.update(query_id=q["query_id"], engine=name, latency_s=round(time.time() - t0, 3))
        out.append(pred)
        time.sleep(PAUSE.get(name, 1.0))
    return out
