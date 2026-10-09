"""Example engines. Add your own: a function (query:str, k:int) -> dict with
"results" and/or "answer" (or "error"). Results pointing at this benchmark's
own repo are dropped so no engine can score by finding the answer key."""

from __future__ import annotations

import os
import re
import time

import requests

REPO = os.environ.get("LOCALBENCH_REPO", "")  # e.g. "your-org/localbench"
BLOCKED = [d for d in os.environ.get(
    "LOCALBENCH_BLOCKED_DOMAINS", "github.com,raw.githubusercontent.com,huggingface.co").split(",") if d]


def _drop_self(results: list[dict]) -> list[dict]:
    if not REPO:
        return results
    return [r for r in results if REPO.lower() not in (r.get("url") or "").lower()]


def brave(query: str, k: int) -> dict:
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": k},
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    web = r.json().get("web", {}).get("results", [])
    res = [{"url": x.get("url"), "title": x.get("title", ""),
            "snippet": " ".join([x.get("description", "")] + x.get("extra_snippets", []))} for x in web]
    return {"results": _drop_self(res)}


def claude(query: str, k: int) -> dict:
    """Model-with-search: scores both the final answer and the pages it retrieved."""
    body = {
        "model": os.environ.get("LOCALBENCH_CLAUDE_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 1024,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5,
                   "blocked_domains": BLOCKED}],
        "messages": [{"role": "user", "content":
            f"Use web search to answer this question about a local business.\n\n{query}\n\n"
            "End your reply with one line: ANSWER: <just the answer>"}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages", json=body, timeout=180,
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    content = r.json().get("content", [])
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    m = re.findall(r"ANSWER:\s*(.+)", text)
    results = []
    for b in content:
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            results += [{"url": x.get("url"), "title": x.get("title", ""), "snippet": ""}
                        for x in b["content"] if x.get("type") == "web_search_result"]
    return {"answer": m[-1].strip() if m else text.strip()[-300:], "results": _drop_self(results)}


ENGINES = {"brave": brave, "claude": claude}


def run_engine(name: str, queries: list[dict], k: int = 5, pause: float = 0.5) -> list[dict]:
    fn = ENGINES[name]
    out = []
    for q in queries:  # sequential, so latency is comparable across engines
        t0 = time.time()
        try:
            pred = fn(q["query"], k)
        except Exception as ex:
            pred = {"error": str(ex)}
        pred.update(query_id=q["query_id"], engine=name, latency_s=round(time.time() - t0, 3))
        out.append(pred)
        time.sleep(pause)
    return out
