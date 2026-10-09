# localbench

A live, daily-refreshed **local search benchmark**, modeled on
[Keenable's NEEDLE](https://github.com/keenableai/needle). Every day it
samples real places from OpenStreetMap, turns each into one question with a
known answer, and publishes the questions as CSV. The answers are published
encrypted and revealed the next day.

Three verticals. The daily workflow makes **20 new questions a day**,
split across them (7 / 7 / 6, rotating which vertical gets 6):

| Vertical | What's in it | OSM source | Question types |
|---|---|---|---|
| `coffee_shops` | independent coffee shops | `amenity=cafe` (bubble tea, juice and dessert cafés excluded) | phone, address, website, name |
| `grocery_produce` | stores that sell fresh produce: produce markets, independent grocery stores, farm stands | `shop=greengrocer`, `shop=supermarket`, `shop=farm` | phone, address, website, name |
| `bike_trails` | named bike trails | `route=bicycle` relations, named `highway=cycleway` and bike-designated paths | length, surface, website, operator, name |

Runs never overwrite anything: each run gets its own ID (`2026-10-01-r1`,
`-r2`, …) and all labeled data is kept.

## How the labeled data is made

**Source.** Everything comes from OpenStreetMap via the free Overpass API.
Labels are facts recorded in OSM (a phone number, an address, a trail's
surface), not human annotations.

**Businesses.** A coffee shop or grocer is eligible if it has a name, a
house number, a street, and a phone number or its own website. Chains are
excluded (anything with a `brand` tag, or a name that repeats in the run's
pool). Each candidate's website is fetched and checked for the OSM phone
number and address; a label confirmed there is `confidence=high`.

**Trails.** A trail is eligible if it's named and 1–300 km long. Named path
segments with the same name are merged into one trail. National and
international long-distance routes are skipped.

- **Length** comes from the route's `distance` tag (`high` confidence) or is
  computed from the map geometry (`medium` for routes, `low` for merged
  paths, which can be partial).
- **Surface** is reduced to paved, gravel or dirt.
- **Operator, website and endpoints** come straight from OSM tags (`medium`).

**Questions.** Each entity becomes one question. Question types are
balanced within each vertical, and each is phrased either as keywords
("Ritual Coffee San Francisco phone number") or as a question ("How long is
the Stevens Creek Trail bike trail near Mountain View?"), also balanced.

**Coverage.** Each run uses 6 cities sampled from 22 major cities in the US,
Canada, the UK, Ireland, the Netherlands, France, Germany, Australia and
Singapore. You can pin a run to named cities instead (`--city`);
Mountain View, Palo Alto, Sunnyvale and Santa Clara are built in.

**Fresh vs stable.** About 30% of each vertical comes from places whose OSM
entry was edited in the last 30 days, so you can see whether an engine keeps
up with changes. An edit isn't always a real-world change.

**Short runs.** If a vertical has fewer than 20 eligible places in the
run's cities, the run comes up short rather than padding. Bike trails in
dense city centers are the most likely to fall short. `days.csv` records
every vertical's count and pool size.

## Scoring

Deterministic rules, no LLM judge:

- **Search engines** return result lists, scored with recall@5 and MRR@5.
  A result is a hit if its URL, title or snippet contains the answer.
- **Models with search** return an answer, scored with accuracy.

What counts as a match:

| field | rule |
|---|---|
| phone | last 9 digits |
| address | house number directly next to the street name |
| website | host name |
| name, operator | every distinctive word appears |
| length | any length in the text within ±25%, in miles or km |
| surface | a word for the right class (e.g. asphalt → paved) |

Scores are a lower bound: only snippets and answers are checked, never full
pages. Results pointing at this repo are dropped. Every report breaks down
by vertical, category, field, bucket, phrasing and city.

## Engines (all free)

| engine | what it is | needs |
|---|---|---|
| `ddgs` | keyless web search via the `ddgs` library (DuckDuckGo by default; set `LOCALBENCH_DDGS_BACKEND` to `brave`, `google`, `mojeek` or `auto`) | nothing |
| `groq` | Groq Compound: an open model with built-in web search; scored on its answer and on the pages it searched | free `GROQ_API_KEY` (no card; about 250 requests/day) |
| `nominatim` | OpenStreetMap's own search. Since the labels come from OSM, this is a ceiling, not a competitor | nothing |

`ddgs` is unofficial (it reads public result pages) and can be rate-limited,
especially from cloud servers. Failed queries are recorded as errors and
excluded from scores, never counted as misses.

## Data files (all CSV)

```
data/runs.csv                     one row per run: the index of all labeled data
data/queries/<run_id>.csv         each run's questions        <- benchmark against these
data/gold/<run_id>.csv.enc        each run's sealed answers (kept forever)
data/gold/<run_id>.sha256         fingerprint of the answers, public from day one
data/gold/<run_id>.csv            readable answers (from the next day)
data/days.csv                     per-run, per-vertical counts and pool sizes
data/history.csv                  baseline scores per run, engine and vertical
data/evals/<run_id>.<engine>.<time>.{preds,report}.csv
```

Column definitions are in `localbench/csvio.py`. Read the files as text
(pandas: `pd.read_csv(path, dtype=str)`), or phone digits lose their
leading zeros. For the cleanest labels, filter answers on `confidence`.

## Benchmark your own system

1. Take the newest questions (`python -m localbench latest` prints the run
   and its paths) and run your system on them the same day.
2. Write predictions as CSV, one row per search result, final answer or
   error:

   ```csv
   query_id,engine,kind,rank,url,title,snippet,answer,error,latency_s
   cfd2a1173476,mine,result,1,https://example.com,Example Café,Call (415) 555-2002,,,0.41
   cfd2a1173476,mine,answer,,,,,+1 415 555 2002,,
   445f3d2b4937,mine,error,,,,,,timeout,
   ```
3. The next day, once the answers are revealed, score:

   ```bash
   python -m localbench score --run 2026-10-01-r1 --preds my_preds.csv \
     --out report_r1.csv --rows-out per_query_r1.csv
   ```

To verify the revealed answers weren't altered, compare
`shasum -a 256 data/gold/<run_id>.csv` with `data/gold/<run_id>.sha256`.

## Commands

| command | does |
|---|---|
| `generate` | create a new run (`--total`, `--verticals`, `--n`, `--city`, `--cities`, `--fresh-days`, `--no-verify`, `--gold-plain-out`) |
| `latest` | print the newest run ID and its files |
| `reveal` | decrypt answers past the one-day embargo (keeps the `.enc`) |
| `run` | run one engine and write predictions (`--engine`, `--run`, `--out`) |
| `score` | score a predictions CSV (`--run` or `--queries/--gold`, `--preds`, `--out`) |
| `baseline` | run the free engines and append to `history.csv` (`--engines`) |

localbench never overwrites a file: output paths must be new, and index
CSVs are append-only.

## Extending

- **Business categories:** `localbench/categories.py`.
- **Cities:** `localbench/cities.py` (set `in_daily_pool=False` for
  places used only with `--city`).
- **Engines:** `localbench/engines.py`. An engine is a function
  `(query, k) -> {"results": [...]}` or `{"answer": ...}`.

## Licensing

Code: MIT. Data: © OpenStreetMap contributors, under the
[ODbL](https://www.openstreetmap.org/copyright). Keep that attribution if you
redistribute `data/`.
