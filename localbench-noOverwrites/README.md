# localbench (v3: no overwrites)

A live, daily-refreshed **local business search benchmark**, modeled on
[Keenable's NEEDLE](https://github.com/keenableai/needle). It covers 12
verticals, from restaurants to auto repair. All data files are CSV.

**v3 never overwrites anything.** Every `generate` run gets its own run ID,
and all labeled data from every run is kept. (v2 is the same benchmark but
replaces a day's files when you rerun it.)

Every day it picks 6 cities, samples up to **20 businesses per vertical** from
OpenStreetMap, turns each into one known-answer question, and publishes the
questions. The answers are embargoed for a day.

## Verticals

| Vertical | Categories (label used in questions) | OSM tags |
|---|---|---|
| `restaurants` | restaurant (with cuisine, e.g. "thai restaurant") | `amenity=restaurant` |
| `food_drink` | café, bar, pub, ice cream shop, beer garden, bakery, butcher shop, deli, pastry shop, candy shop, wine shop, coffee roaster, tea shop, cheese shop, fish market, chocolate shop | `amenity=cafe/bar/pub/ice_cream/biergarten`; `shop=bakery/butcher/deli/pastry/confectionery/wine/coffee/tea/cheese/seafood/chocolate` |
| `lodging` | hotel, guest house, hostel, motel | `tourism=hotel/guest_house/hostel/motel` |
| `personal_care` | hair salon, beauty salon, massage studio, tattoo studio, cosmetics shop, gym | `shop=hairdresser/beauty/massage/tattoo/cosmetics`; `leisure=fitness_centre` |
| `health` | dental practice, doctor's office, clinic, veterinary clinic, optician, physiotherapy practice | `amenity=dentist/doctors/clinic/veterinary`; `healthcare=*`; `shop=optician` |
| `retail` | bookstore, florist, gift shop, jewelry store, bike shop, antique shop, thrift store, art shop, record store, instrument shop, toy store, pet store, framing shop, outdoor gear shop, fabric store, furniture store, garden center, hardware store | `shop=*` |
| `clothing` | clothing store, shoe store, boutique | `shop=clothes/shoes/boutique` |
| `services` | laundromat, dry cleaner, print shop, tailor, shoe repair, travel agency, funeral home, photo studio, locksmith | `shop=*`, `craft=*` |
| `auto` | auto repair shop, tire shop, auto parts store, car dealership, car wash | `shop=car_repair/tyres/car_parts/car`; `amenity=car_wash` |
| `entertainment` | movie theater, theater, nightclub, arts center, museum, art gallery, bowling alley, escape room | `amenity=*`, `tourism=museum/gallery`, `leisure=*` |
| `makers` | brewery, winery, distillery | `craft=brewery/winery/distillery` |
| `schools` | driving school, language school, music school | `amenity=driving_school/language_school/music_school` |

The registry lives in `localbench/categories.py`, and it's the only file you
edit to add categories or verticals. A category can match several OSM tags;
dentists, for example, are tagged both `amenity=dentist` and
`healthcare=dentist`. Home-based trades (plumbers, electricians), chain-heavy
categories (banks, fuel, supermarkets) and institutions (schools, hospitals)
are left out on purpose.

Sparse verticals (`makers`, `schools`) may come up short of 20 on some days.
When that happens, `days.csv` records the shortfall and the rest of the day's
run is unaffected.

## Choosing cities

By default each day samples 6 cities from a rotating pool of 22 major cities.
To pin the benchmark to specific places instead, use `--city`:

```bash
python -m localbench generate --city mountain_view
python -m localbench generate --city mountain_view,palo_alto,sunnyvale --verticals restaurants
```

`mountain_view`, `palo_alto`, `sunnyvale` and `santa_clara` are available by
name but aren't in the random rotation. For those cities, businesses whose
OSM address names a neighboring city are dropped, since the bounding boxes
overlap. To add any other place, add one line to `localbench/cities.py` with
its bounding box; set `in_daily_pool=False` to keep it out of the rotation.

A single small city is a much smaller pool than six big ones. Expect some
verticals to come up short, and a smaller fresh bucket; check `days.csv`.

## How it stays honest

1. **The sample rotates daily.** It's seeded from the date and a secret salt,
   so there is no fixed set to overfit.
2. **A fresh bucket (30% by default).** These are businesses whose OSM record
   was edited in the last 30 days: new openings, changed phone numbers, moves.
3. **An answer-key embargo.** Today's gold is committed encrypted, alongside a
   public SHA-256 of the plaintext, and decrypted the next day. Anyone can
   check that the revealed answers match what was committed.
4. **Clean selection.** Chains are excluded and each city is capped.
   Candidates are over-sampled 2x and checked against the business's own
   website. A phone or address that also appears on the site gets
   `confidence=high`.

Load on OpenStreetMap stays flat as you add verticals: there is one combined
Overpass query per city, twice a day (stable and fresh), whatever the number
of verticals.

## Questions

Each business gets one question. The field is balanced within each vertical:

| field | example | gold column |
|---|---|---|
| `phone` | `Smile Studio San Francisco phone number` | `gold_phone_digits` |
| `address` | `What is the street address of the hair salon Kurz & Klein in Berlin?` | `gold_housenumber`, `gold_street` |
| `website` | `Tartine Paris official website` | `gold_host` |
| `name` | `thai restaurant 1234 Valencia Street San Francisco` | `gold_name` |

Each question also has a `style` (`keyword` or `natural`, balanced) and a
`bucket` (`stable` or `fresh`).

## Runs and the no-overwrite rule

Each `generate` creates a new run with ID `<date>-r<N>`: the first run on
2026-09-30 is `2026-09-30-r1`, a rerun that day is `2026-09-30-r2`, and so
on. Each run is seeded by its own ID, so a rerun draws a new sample and adds
more labeled data rather than repeating the first. Query IDs include the
run ID, so they never collide across runs.

- New files are created in exclusive mode: localbench raises an error
  rather than replace any existing file. This includes your own `--out`,
  `--rows-out` and `--gold-plain-out` paths.
- Index files (`runs.csv`, `days.csv`, `history.csv`) are append-only.
- `reveal` writes the readable answers next to the encrypted file and keeps
  the `.enc`, so the committed artifact always stays verifiable.
- Each baseline evaluation gets its own timestamped files in `evals/`.

## Data files (all CSV)

```
data/runs.csv                     one row per run: the index of all labeled data
data/queries/<run_id>.csv         each run's questions
data/gold/<run_id>.csv.enc        each run's sealed answers (kept forever)
data/gold/<run_id>.sha256         commitment, public from the moment of the run
data/gold/<run_id>.csv            readable answers (after reveal)
data/days.csv                     per-run, per-vertical counts and pool sizes
data/history.csv                  baseline scores per run, engine and vertical
data/evals/<run_id>.<engine>.<time>.{preds,report}.csv
```

There's no `latest/` folder, since it would have to be overwritten. The
newest run is the last row of `runs.csv`, and `python -m localbench latest`
prints it along with its file paths.

Column definitions are in `localbench/csvio.py`. Read the files as text
(pandas: `pd.read_csv(path, dtype=str)`, and don't open them in Excel
without a text import), or phone digits lose their leading zeros.

## Benchmark your system

Write predictions as CSV in long format: one row per retrieved result, one
row for a final answer, or one row for an error:

```csv
query_id,engine,kind,rank,url,title,snippet,answer,error,latency_s
cfd2a1173476,mine,result,1,https://example.com,Example Bistro,Call (415) 555-2002,,,0.41
cfd2a1173476,mine,answer,,,,,+1 415 555 2002,,
445f3d2b4937,mine,error,,,,,,timeout,
```

- `result` rows are scored with recall@5 and MRR@5.
- `answer` rows are scored with answer accuracy.
- `error` rows are excluded and counted.
- A question with no rows at all counts as a miss.

```bash
pip install -e .
python -m localbench score --run 2026-09-29-r1 --preds my_preds.csv \
  --out report.csv --rows-out per_query.csv
```

Leave out `--run` to use the newest run, or pass `--queries` and `--gold`
to point at files directly. A sealed run can be scored after its answers
are revealed, or right away with a `--gold-plain-out` copy.

The report CSV has one overall row plus a row for every vertical, category,
field, bucket, style and city. Add `--verticals restaurants,lodging` to
score a subset.

To add an engine, write a function `(query, k) -> {"results": [...]}` or
`{"answer": ...}` in `localbench/engines.py`, then run
`python -m localbench run --engine NAME --out preds.csv` (newest run by
default, or `--run <run_id>`).

## Setup

| secret | required | purpose |
|---|---|---|
| `GOLD_KEY` | yes | encrypts today's answers (any long random string) |
| `SEED_SALT` | yes | makes the daily sample unpredictable |
| `BRAVE_API_KEY` | no | daily Brave baseline |
| `ANTHROPIC_API_KEY` | no | daily Claude-with-web-search baseline |

Go to Settings → Actions → General → Workflow permissions and select "Read
and write". Then run `daily-refresh` by hand. The manual run lets you pick
verticals and the number of businesses per vertical.

To run it locally (Python 3.10+):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && pytest -q
python -m localbench generate --verticals restaurants,lodging --no-verify
python -m localbench latest
```

## Data licensing

Business data is © OpenStreetMap contributors, under the ODbL. Keep that
attribution if you redistribute `data/`. Change `YOUR_ORG` in the
User-Agent in `osm.py` to your repo.
