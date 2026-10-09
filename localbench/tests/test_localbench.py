import pytest

from localbench import csvio as C
from localbench.categories import CATEGORIES, VERTICALS, resolve_verticals
from localbench.cities import BY_SLUG, DAILY_POOL, resolve_cities
from localbench.cli import main
from localbench.engines import ddgs_to_results, nominatim_query, nominatim_to_results, parse_groq
from localbench.generate import generate
from localbench.matching import (address_match, length_match, match, name_match, phone_match,
                                 surface_match, website_match)
from localbench.osm import build_query, parse_element
from localbench.score import score
from localbench.seal import commitment, seal, unseal
from localbench.trails import Trail, build_trail_query, parse_distance_tag, parse_trails

DAY = "2026-09-28"


# ---- matchers ---------------------------------------------------------------------

def test_business_matchers():
    assert phone_match("14155551234", "Call (415) 555-1234 today")
    assert not phone_match("14155551234", "(415) 555-9999")
    assert phone_match("442071234567", "020 7123 4567")
    assert address_match("1234", "Valencia Street", "Find us at 1234 Valencia St, SF")
    assert not address_match("12", "Main Street", "open 12 hours on Main Street")
    assert address_match("12", "Hauptstraße", "Hauptstr. 12, 10827 Berlin")
    assert name_match("The Joe's Coffee Bar", "Joe's Coffee Bar - Yelp")
    assert website_match("joescoffee.com", "", "https://www.joescoffee.com/menu")


def test_trail_matchers():
    assert length_match(19.3, "The paved trail is 12 miles long")          # 19.3 km
    assert length_match("19.3", "about 20 km end to end")
    assert not length_match(19.3, "a 5-mile loop")
    assert surface_match("paved", "This asphalt trail is great for road bikes")
    assert surface_match("gravel", "Mostly crushed limestone")
    assert not surface_match("dirt", "fully paved path")
    gold = {"field": "operator", "gold_operator": "Santa Clara Valley Water District"}
    assert match(gold, "maintained by the Santa Clara Valley Water District")


# ---- registry ----------------------------------------------------------------------

def test_registry():
    assert list(VERTICALS) == ["coffee_shops", "grocery_produce", "bike_trails"]
    assert resolve_verticals("all") == list(VERTICALS)
    assert CATEGORIES["produce_market"].tags == (("shop", "greengrocer"),)
    with pytest.raises(SystemExit):
        resolve_verticals("restaurants")


def _biz(i, k, v, name, **extra):
    tags = {k: v, "name": name, "addr:housenumber": str(100 + i % 900), "addr:street": "Valencia Street",
            "phone": f"+1 415 555 {1000 + i % 9000}", "website": f"https://{name.lower().replace(' ', '')}.com",
            **extra}
    return {"type": "node", "id": i, "lat": 37.7, "lon": -122.4, "tags": tags, "timestamp": "2026-09-20T00:00:00Z"}


def test_coffee_excludes_bubble_tea_and_chains():
    sf, cats = BY_SLUG["san_francisco"], list(CATEGORIES.values())
    assert parse_element(_biz(1, "amenity", "cafe", "Ritual Coffee"), sf, cats).category == "coffee_shop"
    assert parse_element(_biz(2, "amenity", "cafe", "Boba Place", cuisine="bubble_tea"), sf, cats) is None
    assert parse_element(_biz(3, "amenity", "cafe", "Starbucks", brand="Starbucks"), sf, cats).is_chain


def test_grocery_categories():
    sf, cats = BY_SLUG["san_francisco"], list(CATEGORIES.values())
    assert parse_element(_biz(1, "shop", "greengrocer", "Valencia Produce"), sf, cats).vertical == "grocery_produce"
    assert parse_element(_biz(2, "shop", "farm", "Happy Acres Stand"), sf, cats).label == "farm stand"
    assert parse_element(_biz(3, "shop", "convenience", "Corner Mart"), sf, cats) is None


# ---- trails --------------------------------------------------------------------------

def _line(lat0, lon0, n=11, step=0.01):  # step 0.01 deg lat ~ 1.11 km
    return [{"lat": lat0 + i * step, "lon": lon0} for i in range(n)]  # ~1.11 km per step


def test_parse_trails():
    mv = BY_SLUG["mountain_view"]
    els = [
        {"type": "relation", "id": 9, "timestamp": "2026-09-01T00:00:00Z",
         "tags": {"route": "bicycle", "name": "Bay Trail", "from": "Alviso", "to": "Palo Alto",
                  "surface": "asphalt", "operator": "ABAG"},
         "members": [{"type": "way", "ref": 1, "geometry": _line(37.40, -122.08)},
                     {"type": "way", "ref": 1, "geometry": _line(37.40, -122.08)}]},   # duplicate counted once
        {"type": "way", "id": 20, "tags": {"highway": "cycleway", "name": "Stevens Creek Trail",
                                           "surface": "asphalt"}, "geometry": _line(37.36, -122.07, n=6)},
        {"type": "way", "id": 21, "tags": {"highway": "cycleway", "name": "Stevens Creek Trail",
                                           "surface": "gravel"}, "geometry": _line(37.41, -122.07, n=5)},
        {"type": "way", "id": 30, "tags": {"highway": "cycleway", "name": "Tiny Connector"},
         "geometry": _line(37.39, -122.06, n=2, step=0.005)},                         # ~0.56 km: dropped
        {"type": "way", "id": 40, "tags": {"highway": "cycleway", "name": "Far Away Path"},
         "geometry": _line(37.20, -122.30)},                                           # padding only: dropped
    ]
    trails = {t.name: t for t in parse_trails(els, mv, fresh_way_ids={21})}
    assert set(trails) == {"Bay Trail", "Stevens Creek Trail"}
    bay, scr = trails["Bay Trail"], trails["Stevens Creek Trail"]
    assert bay.osm_type == "relation" and 10.5 < bay.length_km < 11.6 and bay.surface == "paved"
    assert bay.from_ == "Alviso" and not bay.fresh
    assert scr.length_source == "path_geometry" and 9.5 < scr.length_km < 10.5 and scr.fresh
    assert parse_distance_tag("12 mi") == pytest.approx(19.31, 0.01) and parse_distance_tag("8.5") == 8.5


def test_trail_query_skips_national_routes():
    q = build_trail_query(BY_SLUG["berlin"], newer="2026-09-01T00:00:00Z", ids_only=True)
    assert '["network"!~"^(icn|ncn)$"]' in q and "out tags;" in q and '(newer:"2026-09-01' in q


# ---- generation (fake Overpass) ---------------------------------------------------------

FAKE = [("amenity", "cafe"), ("shop", "greengrocer"), ("shop", "supermarket")]


def fake_fetcher(city, cats, newer=None):
    base = 100_000 * (list(BY_SLUG).index(city.slug) + 1)
    els = []
    for t, (k, v) in enumerate(FAKE):
        off = base + 10_000 * t
        if newer:
            els += [_biz(off + i, k, v, f"New {v} {city.slug} {i}") for i in range(3)]
        else:
            els += [_biz(off + 100 + i, k, v, f"Old {v} {city.slug} {i}") for i in range(15)]
            els.append(_biz(off + 999, k, v, f"Chain {v}", brand="Chain"))
    return [b for e in els if (b := parse_element(e, city, cats))]


def fake_trail_fetcher(city, since):
    base = 100_000 * (list(BY_SLUG).index(city.slug) + 1) + 50_000
    out = []
    for i in range(8):
        out.append(Trail(
            osm_type="relation" if i % 2 else "way", osm_id=base + i, name=f"{city.name} Trail {i}",
            category="bike_route" if i % 2 else "bike_path", city=city.name, city_slug=city.slug,
            length_km=3.0 + i, length_source="tag" if i % 2 else "path_geometry",
            surface="paved" if i % 3 else None, surface_tag="asphalt" if i % 3 else None,
            operator="City Parks" if i % 2 else None, website=None,
            from_="North Gate" if i % 4 == 1 else None, to="South Gate" if i % 4 == 1 else None,
            network="lcn", lat=37.0, lon=-122.0, osm_timestamp="2026-09-20T00:00:00Z", fresh=i < 2))
    return out


def gen(day=DAY, run_id=None, **kw):
    kw.setdefault("verticals", list(VERTICALS))
    return generate(day, kw.pop("verticals"), verify=False, fetcher=fake_fetcher,
                    trail_fetcher=fake_trail_fetcher, salt="t", run_id=run_id, **kw)


def test_generate_three_verticals():
    q, g, summary, errors = gen()
    per = {s["vertical"]: s for s in summary}
    assert not errors
    assert per["coffee_shops"]["n_queries"] == 20 and per["coffee_shops"]["n_fresh"] == 6
    assert per["grocery_produce"]["n_queries"] == 20
    assert per["bike_trails"]["n_queries"] == 20
    assert len({x["query_id"] for x in q}) == len(q) == 60
    assert not any("Chain" in x["query"] for x in q)
    trail_fields = {x["field"] for x in q if x["vertical"] == "bike_trails"}
    assert trail_fields <= {"length", "surface", "website", "operator", "name"} and "length" in trail_fields
    assert any("coffee shop" in x["query"] or "Coffee" in x["query"] or x["field"] != "name"
               for x in q if x["vertical"] == "coffee_shops")
    # deterministic per run id, different across runs
    assert gen()[0] == q and gen(run_id=f"{DAY}-r2")[0] != q


def test_trail_gold_and_confidence():
    q, g, *_ = gen(verticals=["bike_trails"])
    for row in g:
        if row["field"] == "length":
            assert row["gold_length_km"] and row["confidence"] in ("high", "low")
            assert match(row, f"The trail runs {float(row['gold_length_km']):.1f} km")


def test_explicit_city_and_addr_filter():
    mv = resolve_cities("mountain_view")
    assert BY_SLUG["mountain_view"] not in DAILY_POOL
    q, *_ = gen(verticals=["coffee_shops"], n=15, cities=mv)
    assert len(q) == 15 and {x["city"] for x in q} == {"mountain_view"}
    base = {"amenity": "cafe", "name": "Castro Coffee", "addr:housenumber": "1",
            "addr:street": "Castro Street", "phone": "+1 650 555 0101"}
    el = lambda **t: {"type": "node", "id": 7, "lat": 37.39, "lon": -122.08, "tags": {**base, **t}}
    cats = list(CATEGORIES.values())
    assert parse_element(el(**{"addr:city": "Palo Alto"}), BY_SLUG["mountain_view"], cats) is None
    assert parse_element(el(), BY_SLUG["mountain_view"], cats).city == "Mountain View"
    with pytest.raises(SystemExit, match="unknown city"):
        resolve_cities("atlantis")


# ---- engines (parsers only; no network) --------------------------------------------------

def test_engine_parsers():
    r = ddgs_to_results([{"title": "Ritual", "href": "https://ritualroasters.com", "body": "(415) 641-1011"}])
    assert r[0]["url"] == "https://ritualroasters.com" and "641" in r[0]["snippet"]

    body = {"choices": [{"message": {
        "content": "It is paved.\nANSWER: paved",
        "executed_tools": [{"type": "search", "search_results": {"results": [
            {"title": "Trail", "url": "https://parks.example.org/trail", "content": "12 miles, asphalt"}]}}]}}]}
    p = parse_groq(body)
    assert p["answer"] == "paved" and p["results"][0]["url"].startswith("https://parks")

    nom = nominatim_to_results([{"osm_type": "node", "osm_id": 5, "display_name": "Ritual, Valencia St",
                                 "name": "Ritual", "address": {"house_number": "1026", "road": "Valencia Street",
                                                               "city": "San Francisco"},
                                 "extratags": {"phone": "+1 415 641 1011", "website": "https://ritualroasters.com"}}])
    gold_phone = {"field": "phone", "gold_phone_digits": "14156411011"}
    assert match(gold_phone, nom[0]["title"] + " " + nom[0]["snippet"], nom[0]["url"])
    assert nominatim_query("What is the phone number for Ritual on Valencia Street in San Francisco?") \
        == "ritual on valencia street in san francisco"


# ---- CLI: runs, sealing, no overwrites -----------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch):
    import localbench.generate as G

    orig = G.generate
    monkeypatch.setattr(G, "generate", lambda *a, **k: orig(
        *a, **{**k, "fetcher": fake_fetcher, "trail_fetcher": fake_trail_fetcher}))
    data = tmp_path / "data"

    def run(*args):
        main([args[0], "--data-dir", str(data), *args[1:]])
    run.data, run.tmp = data, tmp_path
    return run


GEN = ("generate", "--date", DAY, "--no-verify")


def test_cli_roundtrip(cli, monkeypatch):
    monkeypatch.setenv("GOLD_KEY", "secret")
    cli(*GEN)
    d = cli.data
    assert (d / "gold" / f"{DAY}-r1.csv.enc").exists() and not (d / "gold" / f"{DAY}-r1.csv").exists()
    cli("reveal")
    assert (d / "gold" / f"{DAY}-r1.csv").exists() and (d / "gold" / f"{DAY}-r1.csv.enc").exists()
    gold = C.read_csv(d / "gold" / f"{DAY}-r1.csv")
    queries = C.read_csv(d / "queries" / f"{DAY}-r1.csv")
    assert len(queries) == len(gold) == 60
    preds = [{"query_id": x["query_id"], "engine": "oracle", "answer": x["answer"],
              "results": [{"url": "https://example.org", "title": x["answer"], "snippet": ""}]} for x in gold]
    C.write_csv(cli.tmp / "preds.csv", C.preds_to_rows(preds), C.PRED_COLUMNS)
    cli("score", "--run", f"{DAY}-r1", "--preds", str(cli.tmp / "preds.csv"), "--out", str(cli.tmp / "rep.csv"))
    rep = {(r["dimension"], r["group"]): r for r in C.read_csv(cli.tmp / "rep.csv")}
    assert rep[("overall", "all")]["answer_acc"] == "1.0"
    assert rep[("overall", "all")]["recall_at_k"] == "1.0"
    assert ("vertical", "bike_trails") in rep and ("field", "length") in rep


def test_rerun_same_day_keeps_everything(cli):
    cli(*GEN)
    before = {p: p.read_bytes() for p in cli.data.rglob("*") if p.is_file()}
    cli(*GEN)
    after = {p: p.read_bytes() for p in cli.data.rglob("*") if p.is_file()}
    for p, content in before.items():
        assert after[p].startswith(content) if p.name in ("runs.csv", "days.csv") else after[p] == content
    q1 = C.read_csv(cli.data / "queries" / f"{DAY}-r1.csv")
    q2 = C.read_csv(cli.data / "queries" / f"{DAY}-r2.csv")
    assert not {x["query_id"] for x in q1} & {x["query_id"] for x in q2}
    assert [r["run_id"] for r in C.read_csv(cli.data / "runs.csv")] == [f"{DAY}-r1", f"{DAY}-r2"]


def test_outputs_refuse_to_overwrite(cli):
    cli(*GEN)
    existing = cli.tmp / "exists.csv"
    existing.write_text("keep me")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli(*GEN, "--gold-plain-out", str(existing))
    assert not (cli.data / "queries" / f"{DAY}-r2.csv").exists()
    preds = cli.tmp / "p.csv"
    C.write_csv(preds, [], C.PRED_COLUMNS)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli("score", "--preds", str(preds), "--out", str(existing))
    assert existing.read_text() == "keep me"


def test_latest(cli, capsys):
    cli(*GEN)
    cli(*GEN)
    capsys.readouterr()
    cli("latest")
    assert capsys.readouterr().out.startswith(f"{DAY}-r2")


def test_baseline_skips_engines_without_keys(cli, monkeypatch, capsys):
    import localbench.engines as E

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setitem(E.PAUSE, "ddgs", 0)
    monkeypatch.setitem(E.ENGINES, "ddgs", lambda q, k: {"results": []})
    cli(*GEN, "--gold-plain-out", str(cli.tmp / "g.csv"))
    cli("baseline", "--gold", str(cli.tmp / "g.csv"), "--engines", "ddgs,groq")
    out = capsys.readouterr().out
    assert "skipping groq" in out and "ddgs" in out
    hist = C.read_csv(cli.data / "history.csv")
    assert {r["engine"] for r in hist} == {"ddgs"} and {r["vertical"] for r in hist} >= {"all", "bike_trails"}


def test_seal_roundtrip():
    data = b"query_id,answer\nabc,1\n"
    assert unseal(seal(data, "pw"), "pw") == data and commitment(data) == commitment(unseal(seal(data, "pw"), "pw"))


def test_query_builder():
    q = build_query(BY_SLUG["berlin"], [CATEGORIES["coffee_shop"]], newer="2026-09-01T00:00:00Z")
    assert '["amenity"="cafe"]' in q and "out center meta" in q


def test_total_splits_across_verticals():
    q, _, summary, _ = gen(total=20)
    counts = sorted(s["n_queries"] for s in summary)
    assert len(q) == 20 and counts == [6, 7, 7]
    q1, *_ = gen(total=20, verticals=["coffee_shops"])
    assert len(q1) == 20
