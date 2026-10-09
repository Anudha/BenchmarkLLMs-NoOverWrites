"""Scoring from CSV predictions (long format, see csvio.PRED_COLUMNS).

A file with `result` rows is scored with recall@K and MRR@K; a file with
`answer` rows is scored with answer accuracy; a file with both gets both.
Queries with an `error` row are excluded and counted; queries with no rows
at all count as misses."""

from __future__ import annotations

from collections import defaultdict

from .matching import match

DIMENSIONS = ("vertical", "category", "field", "bucket", "style", "city")


def _hit_rank(gold: dict, results: list[dict], k: int) -> int | None:
    for i, r in enumerate(results[:k]):
        if match(gold, f"{r.get('title', '')} {r.get('snippet', '')}", r.get("url")):
            return i
    return None


def score(queries: list[dict], gold: list[dict], preds: dict[str, dict], kinds: set[str], k: int = 5) -> dict:
    g = {x["query_id"]: x for x in gold}
    rows, errors = [], 0
    for q in queries:
        qid = q["query_id"]
        pred = preds.get(qid, {"results": [], "answer": None, "error": None})
        if pred.get("error"):
            errors += 1
            continue
        row = {d: q[d] for d in ("query_id",) + DIMENSIONS}
        if "result" in kinds:
            rank = _hit_rank(g[qid], pred["results"], k)
            row["recall"] = 1.0 if rank is not None else 0.0
            row["rr"] = 1.0 / (rank + 1) if rank is not None else 0.0
        if "answer" in kinds:
            row["correct"] = 1.0 if match(g[qid], pred.get("answer") or "") else 0.0
        rows.append(row)
    return {"k": k, "num_scored": len(rows), "errors": errors, "overall": aggregate(rows),
            "by": {d: group(rows, d) for d in DIMENSIONS}, "rows": rows}


def aggregate(rows: list[dict]) -> dict:
    out: dict = {"n": len(rows)}
    for m, name in (("recall", "recall_at_k"), ("rr", "mrr_at_k"), ("correct", "answer_acc")):
        vals = [r[m] for r in rows if m in r]
        out[name] = round(sum(vals) / len(vals), 4) if vals else ""
    return out


def group(rows: list[dict], dim: str) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r[dim]].append(r)
    return {key: aggregate(v) for key, v in sorted(groups.items())}


def report_rows(rep: dict) -> list[dict]:
    """Flatten a report into CSV rows: one overall row plus one per group."""
    out = [{"dimension": "overall", "group": "all", **rep["overall"]}]
    for dim, groups in rep["by"].items():
        out += [{"dimension": dim, "group": key, **agg} for key, agg in groups.items()]
    return out
