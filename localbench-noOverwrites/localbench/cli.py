"""localbench CLI (v3: never overwrites).

Every `generate` run gets a run_id "<date>-r<N>" and its own files. Nothing
is ever replaced: new files are created exclusively, index CSVs are
append-only, and reveal keeps the encrypted file next to the revealed one."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import csvio as C
from .seal import commitment, seal, unseal

RUN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-r(\d+)$")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- run bookkeeping ------------------------------------------------------------

def _existing_run_numbers(d: Path, day: str) -> set[int]:
    nums = set()
    for sub in ("queries", "gold"):
        for p in (d / sub).glob(f"{day}-r*"):
            m = RUN_RE.match(p.name.split(".")[0])
            if m:
                nums.add(int(m.group(2)))
    if (d / "runs.csv").exists():
        for r in C.read_csv(d / "runs.csv"):
            m = RUN_RE.match(r["run_id"])
            if m and m.group(1) == day:
                nums.add(int(m.group(2)))
    return nums


def next_run_id(d: Path, day: str) -> str:
    return f"{day}-r{max(_existing_run_numbers(d, day), default=0) + 1}"


def latest_run(d: Path) -> str:
    idx = d / "runs.csv"
    if not idx.exists():
        sys.exit(f"no runs yet in {d} (run `generate` first)")
    return C.read_csv(idx)[-1]["run_id"]


def run_paths(d: Path, run_id: str) -> tuple[Path, Path]:
    """(queries, revealed-or-plain gold) paths for a run."""
    return d / "queries" / f"{run_id}.csv", d / "gold" / f"{run_id}.csv"


def _resolve(a, need_gold: bool) -> tuple[Path, Path | None]:
    """--queries/--gold win; otherwise --run; otherwise the latest run."""
    d = Path(a.data_dir)
    run_id = getattr(a, "run", None) or (None if a.queries else latest_run(d))
    q = Path(a.queries) if a.queries else run_paths(d, run_id)[0]
    g = None
    if need_gold:
        g = Path(a.gold) if a.gold else run_paths(d, run_id or q.stem)[1]
        if not g.exists():
            sys.exit(f"gold not found: {g} (sealed runs are readable after `reveal`; "
                     "or pass --gold with a --gold-plain-out copy)")
    return q, g


# ---- commands ------------------------------------------------------------------------

def cmd_generate(a):
    from .categories import resolve_verticals
    from .generate import generate

    day = a.date or today()
    d = Path(a.data_dir)
    if a.gold_plain_out and Path(a.gold_plain_out).exists():
        sys.exit(f"refusing to overwrite {a.gold_plain_out}; pick a new path")
    verticals = resolve_verticals(a.verticals)
    cities = None
    if a.city:
        from .cities import resolve_cities
        cities = resolve_cities(a.city)

    run_id = next_run_id(d, day)
    queries, gold, summary, errors = generate(
        day, verticals, n=a.n, fresh_share=a.fresh_share, cities_per_day=a.cities, cities=cities,
        salt=os.environ.get("SEED_SALT", ""), verify=not a.no_verify, run_id=run_id,
    )
    for e in errors:
        print(f"warning: {e}", file=sys.stderr)
    if not queries:
        sys.exit("no queries generated")

    # Claim the run by creating its queries file exclusively. If another
    # process took this run_id meanwhile, stop rather than overwrite.
    q_path, plain_gold = run_paths(d, run_id)
    try:
        C.write_csv(q_path, queries, C.QUERY_COLUMNS)
    except FileExistsError:
        sys.exit(f"run {run_id} already exists; rerun to get the next run number")

    g_bytes = C.to_bytes(gold, C.GOLD_COLUMNS)
    digest = commitment(g_bytes)
    C.write_new(d / "gold" / f"{run_id}.sha256", (digest + "\n").encode())
    key = os.environ.get("GOLD_KEY")
    if key:
        gold_file = d / "gold" / f"{run_id}.csv.enc"
        C.write_new(gold_file, seal(g_bytes, key))
    else:
        print("warning: GOLD_KEY unset, writing gold unsealed", file=sys.stderr)
        gold_file = plain_gold
        C.write_new(gold_file, g_bytes)
    if a.gold_plain_out:
        C.write_new(a.gold_plain_out, g_bytes)

    C.append_csv(d / "days.csv", summary, C.DAYS_COLUMNS)
    C.append_csv(d / "runs.csv", [{
        "run_id": run_id, "date": day, "created_at": now_stamp(), "n_queries": len(queries),
        "verticals": ";".join(verticals), "cities": summary[0]["cities"] if summary else "",
        "sealed": bool(key), "sha256": digest,
        "queries_file": q_path.relative_to(d).as_posix(), "gold_file": gold_file.relative_to(d).as_posix(),
    }], C.RUNS_COLUMNS)

    print(f"{run_id}: {len(queries)} queries -> {q_path}")
    for s in summary:
        short = "" if s["n_queries"] >= a.n else "  (short: small pool)"
        print(f"  {s['vertical']:<14} {s['n_queries']:>3}  fresh={s['n_fresh']:<3} "
              f"pool={s['pool_stable']}+{s['pool_fresh']}  {s['fields']}{short}")


def cmd_reveal(a):
    key = os.environ.get("GOLD_KEY")
    if not key:
        print("GOLD_KEY unset; nothing to reveal")
        return
    d = Path(a.data_dir) / "gold"
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=a.embargo_days)).isoformat()
    for enc in sorted(d.glob("*.csv.enc")):
        run_id = enc.name[: -len(".csv.enc")]
        m = RUN_RE.match(run_id)
        if not m or m.group(1) > cutoff:
            continue
        out = d / f"{run_id}.csv"
        if out.exists():
            continue  # already revealed; the .enc stays as the committed artifact
        plain = unseal(enc.read_bytes(), key)
        if commitment(plain) != (d / f"{run_id}.sha256").read_text().strip():
            sys.exit(f"commitment mismatch for {run_id}")
        C.write_new(out, plain)
        print(f"revealed {run_id}")


def cmd_latest(a):
    d = Path(a.data_dir)
    run_id = latest_run(d)
    q, g = run_paths(d, run_id)
    print(run_id)
    print(f"queries: {q}")
    print(f"gold:    {g if g.exists() else '(sealed until reveal)'}")


def _filter(queries: list[dict], verticals: str | None) -> list[dict]:
    if not verticals:
        return queries
    keep = set(verticals.split(","))
    return [q for q in queries if q["vertical"] in keep]


def cmd_run(a):
    from .engines import run_engine

    if Path(a.out).exists():
        sys.exit(f"refusing to overwrite {a.out}; pick a new path")
    q_path, _ = _resolve(a, need_gold=False)
    queries = _filter(C.read_csv(q_path), a.verticals)
    preds = run_engine(a.engine, queries, k=a.k)
    C.write_csv(a.out, C.preds_to_rows(preds), C.PRED_COLUMNS)
    print(f"{q_path.stem}: wrote {a.out}")


def cmd_score(a):
    from .score import report_rows, score

    for p in (a.out, a.rows_out):
        if p and Path(p).exists():
            sys.exit(f"refusing to overwrite {p}; pick a new path")
    q_path, g_path = _resolve(a, need_gold=True)
    queries = _filter(C.read_csv(q_path), a.verticals)
    preds, kinds = C.rows_to_preds(C.read_csv(a.preds))
    rep = score(queries, C.read_csv(g_path), preds, kinds, k=a.k)
    rows = report_rows(rep)
    if a.out:
        C.write_csv(a.out, rows, C.REPORT_COLUMNS)
    if a.rows_out:
        C.write_csv(a.rows_out, rep["rows"], ["query_id", "vertical", "category", "field", "bucket",
                                              "style", "city", "recall", "rr", "correct"])
    sys.stdout.write(C.to_bytes(rows, C.REPORT_COLUMNS).decode())
    print(f"# run={q_path.stem} scored={rep['num_scored']} errors={rep['errors']} k={rep['k']}", file=sys.stderr)


def cmd_baseline(a):
    """Run every engine whose API key is set; each evaluation gets its own
    timestamped files, and history.csv is appended."""
    from .engines import run_engine
    from .score import report_rows, score

    keys = {"brave": "BRAVE_API_KEY", "claude": "ANTHROPIC_API_KEY"}
    q_path, g_path = _resolve(a, need_gold=True)
    queries, gold = C.read_csv(q_path), C.read_csv(g_path)
    d = Path(a.data_dir)
    run_id, day = queries[0]["run_id"], queries[0]["date"]
    for eng, env in keys.items():
        if not os.environ.get(env):
            continue
        stamp = now_stamp()
        tag = f"{run_id}.{eng}.{stamp.replace(':', '').replace('-', '')}"
        rows = C.preds_to_rows(run_engine(eng, queries, k=a.k))
        C.write_csv(d / "evals" / f"{tag}.preds.csv", rows, C.PRED_COLUMNS)
        preds, kinds = C.rows_to_preds(rows)
        rep = score(queries, gold, preds, kinds, k=a.k)
        C.write_csv(d / "evals" / f"{tag}.report.csv", report_rows(rep), C.REPORT_COLUMNS)
        base = {"run_id": run_id, "date": day, "evaluated_at": stamp, "engine": eng}
        hist = [{**base, "vertical": "all", "errors": rep["errors"], **rep["overall"]}]
        hist += [{**base, "vertical": v, **agg} for v, agg in rep["by"]["vertical"].items()]
        C.append_csv(d / "history.csv", hist, C.HISTORY_COLUMNS)
        print(eng, rep["overall"])


# ---- argument parsing -----------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser("localbench")
    sp = p.add_subparsers(dest="cmd", required=True)

    g = sp.add_parser("generate", help="create a new run: queries + sealed gold")
    g.add_argument("--data-dir", default="data")
    g.add_argument("--date")
    g.add_argument("--verticals", default="all", help="'all' or comma list, e.g. restaurants,lodging")
    g.add_argument("--n", type=int, default=20, help="businesses per vertical")
    g.add_argument("--fresh-share", type=float, default=0.3)
    g.add_argument("--cities", type=int, default=6, help="how many cities to sample (ignored with --city)")
    g.add_argument("--city", help="use these cities instead of sampling, e.g. mountain_view or mountain_view,palo_alto")
    g.add_argument("--no-verify", action="store_true")
    g.add_argument("--gold-plain-out", help="also write readable gold here (must be a new file)")
    g.set_defaults(fn=cmd_generate)

    r = sp.add_parser("reveal", help="decrypt gold past its embargo (keeps the .enc)")
    r.add_argument("--data-dir", default="data")
    r.add_argument("--embargo-days", type=int, default=1)
    r.set_defaults(fn=cmd_reveal)

    lt = sp.add_parser("latest", help="print the newest run id and its files")
    lt.add_argument("--data-dir", default="data")
    lt.set_defaults(fn=cmd_latest)

    def add_selection(x, gold: bool):
        x.add_argument("--data-dir", default="data")
        x.add_argument("--run", help="run id, e.g. 2026-09-30-r1 (default: latest run)")
        x.add_argument("--queries", help="explicit queries CSV (overrides --run)")
        if gold:
            x.add_argument("--gold", help="explicit gold CSV (overrides --run)")
        x.add_argument("--k", type=int, default=5)

    ru = sp.add_parser("run", help="run an engine, write predictions CSV")
    add_selection(ru, gold=False)
    ru.add_argument("--engine", required=True)
    ru.add_argument("--verticals")
    ru.add_argument("--out", required=True)
    ru.set_defaults(fn=cmd_run)

    s = sp.add_parser("score", help="score a predictions CSV")
    add_selection(s, gold=True)
    s.add_argument("--preds", required=True)
    s.add_argument("--verticals")
    s.add_argument("--out", help="write the report CSV here (must be a new file)")
    s.add_argument("--rows-out", help="write per-query results CSV here (must be a new file)")
    s.set_defaults(fn=cmd_score)

    b = sp.add_parser("baseline", help="run all keyed engines, append history.csv")
    add_selection(b, gold=True)
    b.set_defaults(fn=cmd_baseline)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except FileExistsError as ex:
        sys.exit(f"refusing to overwrite {ex.filename}")


if __name__ == "__main__":
    main()
