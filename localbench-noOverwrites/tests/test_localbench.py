import os

import pytest

from localbench import csvio as C
from localbench.categories import CATEGORIES, VERTICALS, resolve_verticals
from localbench.cities import BY_SLUG
from localbench.cli import main
from localbench.generate import generate
from localbench.matching import address_match, name_match, phone_match, website_match
from localbench.osm import build_query, parse_element
from localbench.score import score
from localbench.seal import commitment, seal, unseal


# ---- matchers --------------------------------------------------------------

def test_phone_formats():
    assert phone_match("14155551234", "Call (415) 555-1234 today")
    assert phone_match("14155551234", "+1 415.555.1234")
    assert not phone_match("14155551234", "(415) 555-9999")
    assert phone_match("442071234567", "020 7123 4567")


def test_address():
    assert address_match("1234", "Valencia Street", "Find us at 1234 Valencia St, SF")
    assert not address_match("1234", "Valencia Street", "1234 Mission St")
    assert address_match("12", "Hauptstraße", "Hauptstr. 12, 10827 Berlin")
    assert not address_match("12", "Main Street", "open 12 hours on Main Street")
    assert address_match("1234", "Valencia Street", "1234-B N. Valencia St.")
    assert address_match("12", "Rue de Rivoli", "12, rue de Rivoli, 75004 Paris")


def test_name_and_website():
    assert name_match("The Joe's Pho Kitchen", "Joe's Pho Kitchen - Yelp")
    assert not name_match("Joe's Pho Kitchen", "Pho Kitchen Downtown")
    assert website_match("joespho.com", "", "https://www.joespho.com/menu")
    assert not website_match("joespho.com", "notjoespho.com")


# ---- registry ----------------------------------------------------------------

def test_registry():
    assert len(VERTICALS) >= 10
    assert CATEGORIES["dentist"].tags == (("amenity", "dentist"), ("healthcare", "dentist"))
    assert CATEGORIES["hairdresser"].label == "hair salon"
    assert len({c.key for cs in VERTICALS.values() for c in cs}) == len(CATEGORIES)  # keys unique
    assert resolve_verticals("restaurants,lodging") == ["restaurants", "lodging"]


def test_query_builder_dedupes_tags():
    cats = [CATEGORIES["dentist"], CATEGORIES["restaurant"]]
    q = build_query(BY_SLUG["berlin"], cats, newer="2026-09-01T00:00:00Z")
    assert q.count('["healthcare"="dentist"]') == 1 and '(newer:"2026-09-01T00:00:00Z")' in q


def test_multi_tag_category():
    el = {"type": "node", "id": 1, "lat": 0, "lon": 0,
          "tags": {"healthcare": "dentist", "name": "Smile Studio", "addr:housenumber": "5",
                   "addr:street": "Oak St", "phone": "+1 415 555 0000"}}
    b = parse_element(el, BY_SLUG["san_francisco"], list(CATEGORIES.values()))
    assert b.category == "dentist" and b.vertical == "health" and b.label == "dental practice"


# ---- end to end ------------------------------------------------------------------

FAKE_TAGS = [("amenity", "restaurant"), ("tourism", "hotel"), ("shop", "hairdresser"), ("shop", "bakery")]


def _el(i, name, k, v, **extra):
    tags = {k: v, "name": name, "addr:housenumber": str(100 + i % 900), "addr:street": "Valencia Street",
            "phone": f"+1 415 555 {1000 + i % 9000}", "website": f"https://{name.lower().replace(' ', '')}.com",
            "cuisine": "thai", **extra}
    return {"type": "node", "id": i, "lat": 37.7, "lon": -122.4, "tags": tags, "timestamp": "2026-09-20T00:00:00Z"}


def fake_fetcher(city, cats, newer=None):
    base = 100_000 * (list(BY_SLUG).index(city.slug) + 1)
    els = []
    for t, (k, v) in enumerate(FAKE_TAGS):
        off = base + 10_000 * t
        if newer:
            els += [_el(off + i, f"New {v} {city.slug} {i}", k, v) for i in range(3)]
        else:
            els += [_el(off + 100 + i, f"Old {v} {city.slug} {i}", k, v) for i in range(15)]
            els.append(_el(off + 999, f"Chain {v}", k, v, brand="Chain"))
    return [b for e in els if (b := parse_element(e, city, cats))]


def test_generate_multi_vertical():
    vs = ["restaurants", "lodging", "personal_care", "food_drink", "makers"]
    q, g, summary, errors = generate("2026-09-29", vs, n=20, verify=False, fetcher=fake_fetcher, salt="t")
    per = {s["vertical"]: s for s in summary}
    for v in ("restaurants", "lodging", "personal_care", "food_drink"):
        assert per[v]["n_queries"] == 20 and per[v]["n_fresh"] == 6
    assert per["makers"]["n_queries"] == 0  # no fake makers: reported, not fatal
    assert len({x["query_id"] for x in q}) == len(q) == 80
    assert not any("Chain" in x["query"] for x in q)
    assert any("hair salon" in x["query"] for x in q)  # labels used in wording
    q2, *_ = generate("2026-09-29", vs, n=20, verify=False, fetcher=fake_fetcher, salt="t")
    q3, *_ = generate("2026-09-30", vs, n=20, verify=False, fetcher=fake_fetcher, salt="t")
    assert q == q2 and q != q3


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run the CLI against fake Overpass data in a temp data dir."""
    import localbench.generate as G

    orig = G.generate
    monkeypatch.setattr(G, "generate", lambda *a, **k: orig(*a, **{**k, "fetcher": fake_fetcher}))
    data = tmp_path / "data"

    def run(*args):
        main([args[0], "--data-dir", str(data), *args[1:]])
    run.data = data
    run.tmp = tmp_path
    return run


GEN = ("generate", "--date", "2026-09-28", "--verticals", "restaurants,lodging", "--no-verify")


def test_cli_csv_roundtrip(cli, monkeypatch):
    monkeypatch.setenv("GOLD_KEY", "secret")
    cli(*GEN)
    data = cli.data
    assert (data / "queries" / "2026-09-28-r1.csv").exists()
    assert (data / "gold" / "2026-09-28-r1.csv.enc").exists()
    assert not (data / "gold" / "2026-09-28-r1.csv").exists()  # sealed
    cli("reveal")
    assert (data / "gold" / "2026-09-28-r1.csv").exists()
    assert (data / "gold" / "2026-09-28-r1.csv.enc").exists()  # v3: .enc is kept
    gold = C.read_csv(data / "gold" / "2026-09-28-r1.csv")
    queries = C.read_csv(data / "queries" / "2026-09-28-r1.csv")
    assert len(queries) == len(gold) == 40 and {q["run_id"] for q in queries} == {"2026-09-28-r1"}

    preds = [{"query_id": x["query_id"], "engine": "oracle", "answer": x["answer"],
              "results": [{"url": "https://example.org", "title": x["answer"], "snippet": ""}]} for x in gold]
    C.write_csv(cli.tmp / "preds.csv", C.preds_to_rows(preds), C.PRED_COLUMNS)
    cli("score", "--run", "2026-09-28-r1", "--preds", str(cli.tmp / "preds.csv"),
        "--out", str(cli.tmp / "report.csv"))
    rep = {(r["dimension"], r["group"]): r for r in C.read_csv(cli.tmp / "report.csv")}
    assert rep[("overall", "all")]["answer_acc"] == "1.0"
    assert ("vertical", "lodging") in rep


def _snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_rerun_same_day_keeps_everything(cli):
    cli(*GEN)
    before = _snapshot(cli.data)
    cli(*GEN)
    after = _snapshot(cli.data)
    # every file from run 1 is still there, byte for byte (index CSVs only grew)
    for p, content in before.items():
        if p.name in ("runs.csv", "days.csv"):
            assert after[p].startswith(content)
        else:
            assert after[p] == content, p
    q1 = C.read_csv(cli.data / "queries" / "2026-09-28-r1.csv")
    q2 = C.read_csv(cli.data / "queries" / "2026-09-28-r2.csv")
    assert not {x["query_id"] for x in q1} & {x["query_id"] for x in q2}  # ids never collide
    runs = C.read_csv(cli.data / "runs.csv")
    assert [r["run_id"] for r in runs] == ["2026-09-28-r1", "2026-09-28-r2"]
    days = C.read_csv(cli.data / "days.csv")
    assert {r["run_id"] for r in days} == {"2026-09-28-r1", "2026-09-28-r2"}


def test_mixed_sealed_and_plain_runs_do_not_collide(cli, monkeypatch):
    monkeypatch.setenv("GOLD_KEY", "secret")
    cli(*GEN)
    monkeypatch.delenv("GOLD_KEY")
    cli(*GEN)
    gold = cli.data / "gold"
    assert (gold / "2026-09-28-r1.csv.enc").exists() and not (gold / "2026-09-28-r1.csv").exists()
    assert (gold / "2026-09-28-r2.csv").exists()
    monkeypatch.setenv("GOLD_KEY", "secret")
    cli("reveal")
    cli("reveal")  # second reveal is a no-op, not an error
    assert (gold / "2026-09-28-r1.csv").exists()


def test_outputs_refuse_to_overwrite(cli):
    cli(*GEN)
    existing = cli.tmp / "exists.csv"
    existing.write_text("keep me")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli(*GEN, "--gold-plain-out", str(existing))
    assert not (cli.data / "queries" / "2026-09-28-r2.csv").exists()  # failed before writing anything
    preds = cli.tmp / "p.csv"
    C.write_csv(preds, [], C.PRED_COLUMNS)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli("score", "--preds", str(preds), "--out", str(existing))
    assert existing.read_text() == "keep me"
    with pytest.raises(FileExistsError):
        C.write_csv(existing, [], C.PRED_COLUMNS)


def test_latest_and_run_ids(cli, capsys):
    cli(*GEN)
    cli(*GEN)
    capsys.readouterr()  # discard generate output
    cli("latest")
    assert capsys.readouterr().out.startswith("2026-09-28-r2")


def test_empty_and_error_predictions():
    q, g, *_ = generate("2026-09-29", ["restaurants"], n=5, verify=False, fetcher=fake_fetcher, salt="t")
    rows = C.preds_to_rows([{"query_id": q[0]["query_id"], "error": "timeout"},
                            {"query_id": q[1]["query_id"], "results": []}])
    rows.append({"query_id": q[2]["query_id"], "kind": "result", "rank": "1", "url": "https://x.org",
                 "title": "nothing", "snippet": ""})
    preds, kinds = C.rows_to_preds(C.from_bytes(C.to_bytes(rows, C.PRED_COLUMNS)))
    rep = score(q, g, preds, kinds)
    assert rep["errors"] == 1 and rep["num_scored"] == 4 and rep["overall"]["recall_at_k"] == 0.0


def test_seal_roundtrip():
    data = b"query_id,answer\nabc,1\n"
    assert unseal(seal(data, "pw"), "pw") == data
    assert commitment(data) == commitment(unseal(seal(data, "pw"), "pw"))


# ---- city selection ---------------------------------------------------------------

def test_explicit_city():
    from localbench.cities import DAILY_POOL, resolve_cities

    mv = resolve_cities("mountain_view")
    assert BY_SLUG["mountain_view"] not in DAILY_POOL  # named-only, not in random rotation
    q, g, summary, _ = generate("2026-09-29", ["restaurants"], n=15, verify=False,
                                fetcher=fake_fetcher, salt="t", cities=mv)
    assert len(q) == 15 and {x["city"] for x in q} == {"mountain_view"}
    assert summary[0]["cities"] == "mountain_view"


def test_addr_city_filter():
    mv = BY_SLUG["mountain_view"]
    cats = list(CATEGORIES.values())
    base = {"amenity": "restaurant", "name": "Taqueria Uno", "addr:housenumber": "1",
            "addr:street": "Castro Street", "phone": "+1 650 555 0101"}
    el = lambda **t: {"type": "node", "id": 7, "lat": 37.39, "lon": -122.08, "tags": {**base, **t}}
    assert parse_element(el(**{"addr:city": "Mountain View"}), mv, cats).city == "Mountain View"
    assert parse_element(el(**{"addr:city": "Palo Alto"}), mv, cats) is None
    assert parse_element(el(), mv, cats).city == "Mountain View"  # untagged: kept


def test_unknown_city_message():
    from localbench.cities import resolve_cities

    with pytest.raises(SystemExit, match="unknown city"):
        resolve_cities("atlantis")
