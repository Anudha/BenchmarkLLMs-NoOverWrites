"""CSV schemas and helpers. Every data file the benchmark writes is CSV.

No-overwrite rule: files are created with exclusive mode ("x"), so writing
to a path that already exists raises FileExistsError instead of replacing
it. Index files (runs.csv, days.csv, history.csv) are append-only.

All values are read back as strings. If you load these files elsewhere, read
them as text (e.g. pandas `dtype=str`) so phone digits keep leading zeros."""

from __future__ import annotations

import csv
import io
from pathlib import Path

QUERY_COLUMNS = [
    "query_id", "run_id", "date", "vertical", "category", "query", "field", "style", "bucket", "city",
]

GOLD_COLUMNS = [
    "query_id", "run_id", "date", "vertical", "category", "field", "answer",
    # the gold value for the asked field (only that field's column is filled)
    "gold_phone_digits", "gold_host", "gold_housenumber", "gold_street", "gold_name",
    "gold_length_km", "gold_surface", "gold_operator",
    # evidence
    "confidence", "site_ok", "phone_on_site", "address_on_site",
    "osm_url", "osm_timestamp",
    # the full record of the business or trail
    "entity_kind", "entity_name", "entity_website", "entity_city", "entity_lat", "entity_lon",
    "entity_phone", "entity_housenumber", "entity_street", "entity_postcode", "entity_cuisine",
    "trail_length_km", "trail_length_source", "trail_surface", "trail_operator",
    "trail_from", "trail_to", "trail_network",
]

# Long format: one row per retrieved result, one row for a final answer,
# or one row for an error. `kind` is result | answer | error.
PRED_COLUMNS = [
    "query_id", "engine", "kind", "rank", "url", "title", "snippet", "answer", "error", "latency_s",
]

DAYS_COLUMNS = [
    "run_id", "date", "vertical", "n_queries", "n_fresh", "pool_stable", "pool_fresh", "fields", "cities",
]

HISTORY_COLUMNS = [
    "run_id", "date", "evaluated_at", "engine", "vertical", "n", "errors", "recall_at_k", "mrr_at_k", "answer_acc",
]

# One row per generate run: the append-only index of all labeled data.
RUNS_COLUMNS = [
    "run_id", "date", "created_at", "n_queries", "verticals", "cities",
    "sealed", "sha256", "queries_file", "gold_file",
]

REPORT_COLUMNS = ["dimension", "group", "n", "recall_at_k", "mrr_at_k", "answer_acc"]


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def to_bytes(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: _clean(r.get(c)) for c in columns})
    return buf.getvalue().encode("utf-8")


def from_bytes(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def write_new(path, data: bytes) -> None:
    """Create a file that must not already exist (raises FileExistsError)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)


def write_csv(path, rows: list[dict], columns: list[str]) -> None:
    write_new(path, to_bytes(rows, columns))


def read_csv(path) -> list[dict]:
    return from_bytes(Path(path).read_bytes())


def append_csv(path, rows: list[dict], columns: list[str]) -> None:
    """Append rows, writing the header only when the file is new."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists() or p.stat().st_size == 0
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({c: _clean(r.get(c)) for c in columns})


def preds_to_rows(preds: list[dict]) -> list[dict]:
    """Engine outputs ({results, answer, error}) -> long-format CSV rows."""
    rows = []
    for p in preds:
        base = {"query_id": p["query_id"], "engine": p.get("engine", ""), "latency_s": p.get("latency_s", "")}
        if "error" in p:
            rows.append({**base, "kind": "error", "error": p["error"]})
            continue
        for i, r in enumerate(p.get("results", []), 1):
            rows.append({**base, "kind": "result", "rank": i, "url": r.get("url"),
                         "title": r.get("title"), "snippet": r.get("snippet")})
        if "answer" in p:
            rows.append({**base, "kind": "answer", "answer": p["answer"]})
    return rows


def rows_to_preds(rows: list[dict]) -> tuple[dict[str, dict], set[str]]:
    """Long-format CSV rows -> {query_id: {results, answer, error}} plus the
    set of kinds present in the file (which decides what gets scored)."""
    out: dict[str, dict] = {}
    kinds: set[str] = set()
    for r in rows:
        kind = r.get("kind", "")
        kinds.add(kind)
        p = out.setdefault(r["query_id"], {"results": [], "answer": None, "error": None})
        if kind == "result":
            p["results"].append((int(r.get("rank") or 0), r))
        elif kind == "answer":
            p["answer"] = r.get("answer", "")
        elif kind == "error":
            p["error"] = r.get("error") or "error"
    for p in out.values():
        p["results"] = [r for _, r in sorted(p["results"], key=lambda x: x[0])]
    return out, kinds
