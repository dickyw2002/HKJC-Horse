# HKJC Horse Racing Predictor

A machine-learning project for estimating Hong Kong Jockey Club (HKJC) horse-racing outcomes and improving models through an automated, reproducible fine-tuning pipeline.

> This project is for research and educational use. Predictions are uncertain and are not financial advice. Use data only where permitted by the source's terms, robots policy, and applicable law. Do not use automation to place bets.

## Week 1 — local results collector

Week 1 is a polite, resumable collector for **local** Hong Kong meetings (Sha Tin `ST` and Happy Valley `HV`). It does not place bets and it does not republish HKJC data. Overseas simulcast meetings (`S1`, `S2`, `S3`) are ignored.

The current results URL is:

```text
https://racing.hkjc.com/en-us/local/information/localresults?racedate=YYYY/MM/DD&Racecourse=ST&RaceNo=N
```

Older `.aspx` results URLs redirect. Meetings older than about a year redirect again to `/en-us/local/information/archive/localresults` (lowercase `racecourse` parameter). That page is the same HTML tables. The collector requests the archive URL directly for those older meetings, and for the rest of a meeting after the first response lands on `/archive/`.

Meeting dates come from the fixture calendar (`/en-us/local/information/fixture?calyear=YYYY&calmonth=MM`) plus the date-list JSON used by the results page dropdown. A fixture response whose calendar header is a different month (an August request can return September) is ignored, so those days are recorded only from the matching month. Runner columns are mapped by header name, so a void race that omits odds and running positions still parses. Brand numbers may have two letters (`AJ313`). Withdrawn rows with a blank horse number are kept.

### Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

The database file defaults to `data/hkjc.duckdb`. Raw HTML is cached under `cache/`. Both are gitignored.

### Commands

```bash
# Historical load. Defaults to 2024-01-01 through today in Hong Kong.
# About one HTTP request per race, so a full run is on the order of 30–40 minutes.
uv run hkjc backfill --from 2024-01-01 --to 2026-09-27

# Meetings after the latest complete meeting already in the database.
uv run hkjc update

# CSV and Parquet extracts of every table, including the empty odds_snapshots table.
uv run hkjc export --out data

# Load the checked-in reference extract. No network.
uv run hkjc import
```

Useful flags: `--db`, `--cache`, `--delay` (minimum 1 second), `--force` (redownload). Re-runs skip meetings already marked complete and read cached HTML instead of calling the site again. Rows are upserted, so a repeated race does not duplicate runners or dividends. `hkjc import` reads `data/races.csv` and `data/runners.csv` (override with `--races` and `--runners`) and upserts meetings, races, and runners. It does not delete races that are absent from those files, so abandoned placeholders already stored by a scrape stay in the database.

### Database

| Table | Contents |
| --- | --- |
| `meetings` | Date, `ST`/`HV`, venue name, completeness, race numbers |
| `races` | Class, distance, rating band, going, course, surface, rail, name, prize, sectionals, season, race-time splits. `abandoned` marks a race with no runners |
| `runners` | `finish_position` kept as text (`1`, `3 DH`, `WV`, `PU`, `DNF`, `VOID`, …), horse number (blank when withdrawn), name, brand code, horse id, jockey, trainer, actual weight, declared horse weight, draw, lengths behind, running positions, finish time, win odds, incident. Primary key is `(race_id, horse_id)` |
| `dividends` | Pool, winning combination, dividend in HK$ |
| `odds_snapshots` | Empty. Schema is reserved for later pre-race odds logging |
| `fetched_pages` | Audit of cached page outcomes (not exported) |

Horse, jockey, and trainer ids are the public ids in the profile links (`HK_2024_K209`, and the short jockey/trainer codes). `lbw` is the original margin token (`---`, `N`, `1-1/4`, …). `lbw_lengths` is a convenience conversion, not an official HKJC field: nose 0.05, short head 0.1, head 0.2, neck (`N`) 0.3, dead-heat and `---` are 0, and `whole-num/den` fractions are added. A leading minus on the winner (`-SH`, `-HD`) is stored as a negative winning margin. `ML` (many lengths) stays null. Unknown tokens stay null.

Jockey and trainer ids are filled only when the results page links a profile. Visiting riders and some apprentices are printed as plain text, so the name is stored and the id is null.

### Checked-in results dataset

`data/races.csv` and `data/runners.csv`, with matching `.parquet` files, are a validated local-results extract for **2024-01-01 through 2026-09-27**: **238 meetings, 2,304 races, 28,718 runner rows**. A separate scraper produced them. Load them with `uv run hkjc import` when you do not want to download the pages again. Do not republish them.

Finish position stays text, including `WV`, `WV-A`, `PU`, `DNF`, `VOID`, `WX`, `FE`, `UR`, `DISQ`, `TNP`, `WXNR`, and dead-heat markers such as `3 DH`. Withdrawn horses are rows with a blank horse number; key them by `(date, racecourse, race_no, horse_id)`. `---` on weights, draw, and odds becomes null in the typed columns. `lbw` keeps the original margin token, including `---`. Running-position cells that contain only extra spaces are collapsed on import (`13   ` becomes `13`).

`hkjc export` writes the database tables as CSV and Parquet. When `data/races.csv` or `data/runners.csv` still has this reference header (`date,season,racecourse,...`), export leaves those files and their parquet twins in place and writes `races.normalized.*` and `runners.normalized.*` instead.

`data/meetings.csv`, `data/dividends.csv`, and `data/odds_snapshots.csv` (and their parquet files) are a collector export from the same window. That export also contains empty date-list probes and dividend rows. It is not the same shape as the reference race and runner files.

Compared with this collector's own backfill of the same dates, the 238 meetings match. The collector previously dropped 209 withdrawn rows with no horse number; it now keeps them. The reference files omit five abandoned races that have no result table (listed below). On 2024-01-01 Sha Tin the race fields and finishers match aside from trailing spaces in running positions. On 2026-09-27 Sha Tin the only runner difference was those withdrawn rows. On 2026-09-23 Happy Valley the same withdrawn-row gap appears, and the reference file keeps a literal `---` where the database stores null for weights, draw, and odds.

#### Known gaps in the reference files

- 2025-11-15 Sha Tin race 8 was declared VOID. Runners are kept with finishing position `VOID`. Odds, running positions, and going are blank.
- Abandoned races have no result table and are not rows in these files: 2024-11-13 Happy Valley races 7–9, and 2025-09-21 Sha Tin races 9–10. A scrape can still store those pages as `races.abandoned` with refund dividends.
- Fixture days with no results page: 2025-09-24 Happy Valley, and 2026-09-20 Sha Tin ("No information.").
- `rating_band` is blank for Group, Griffin, and 4-year-old races. `rail` is blank on the all-weather track.
- Brand numbers can have two letters (for example `AJ313`). The horse id on the profile link drops a letter (`HK_2023_J313`).

### Politeness

- Descriptive User-Agent (`HKJCResearchCollector/0.1`), not a browser impersonation.
- At least 1 second between requests, plus up to 0.35s of jitter. The clock starts at the beginning of each request, so the rate stays near one page per second.
- Retries with exponential backoff on network errors and HTTP 429/5xx. HTTP 401/403 stops the run.
- Raw responses are written under `cache/` and are not committed.
- `racing.hkjc.com/robots.txt` currently returns a site 404 rather than crawl rules. Throttling is still applied.

### Sample cron

Times below are UTC. Hong Kong is UTC+8. Wednesday night Happy Valley cards usually finish before 23:30 HKT. Weekend Sha Tin cards usually finish before 19:00 HKT. The pattern has holiday exceptions, and `update` is safe to run more often because completed meetings are skipped.

```cron
# Happy Valley, usually Wednesday. 15:30 UTC = 23:30 HKT.
30 15 * * 3 cd /path/to/HKJC-Horse && uv run hkjc update >> logs/update.log 2>&1

# Sha Tin, usually Saturday and Sunday. 11:00 UTC = 19:00 HKT.
0 11 * * 0,6 cd /path/to/HKJC-Horse && uv run hkjc update >> logs/update.log 2>&1

# Alternative: one daily run at 16:30 UTC (00:30 HKT) covers both.
30 16 * * * cd /path/to/HKJC-Horse && uv run hkjc update >> logs/update.log 2>&1
```

### Tests

```bash
uv run pytest -m "not live"   # parser, database, and fixture tests
uv run pytest -m live         # fetches 2026-09-27 Sha Tin and prints race/runner counts
```

Saved HTML under `tests/fixtures/` is for parser tests only.

### Known limitations

- Live win/place odds are not collected. HKJC serves them from a GraphQL endpoint that accepts only whitelisted persisted queries. `odds_snapshots` is ready for a later logger that records `captured_at` so pre-race prices are not confused with final dividends.
- Per-horse sectional times live on a separate page and are not ingested. Race-level sectional strings from the results header are stored on `races.sectionals`.
- Barrier trials, trackwork, and horse-profile fields (age, sex, rating) are out of scope.
- Gear and equipment codes are not a column on the results table.
- A meeting that returns "No information." is stored as empty and is not retried after that Hong Kong date.
- Abandoned races (no runners, pool dividends marked `REFUND`) are stored with `races.abandoned` set when you scrape them. They are absent from the checked-in reference CSVs; see the gaps listed above. `hkjc update` retries any meeting left `partial`.
- This repository may contain small CSV/Parquet exports for private research. Do not republish them.

## Goals

- Collect and normalize historical race, horse, jockey, trainer, draw, going, distance, weight, and result data.
- Predict win probability and ranked finishing order for each runner.
- Compare model probabilities with market odds without introducing future-data leakage.
- Backtest models using chronological race splits.
- Automatically retrain and tune models as new verified results become available.
- Promote a new model only when it outperforms the current model on fixed validation criteria.

## Suggested Architecture

```text
Permitted data sources
        |
        v
Data ingestion -> Validation -> Feature store
                                  |
                                  v
                           Training / tuning
                                  |
                                  v
                         Model evaluation
                                  |
                     better? -----+----- no
                        |                 |
                        v                 v
                 Model registry       Keep current
                        |
                        v
                 Race predictions
```

## Prediction Targets

Start with one target and add others after the baseline is reliable:

1. **Win probability**: probability that each runner wins its race.
2. **Place probability**: probability that each runner finishes in a defined place range.
3. **Finishing rank**: expected ordering of all runners in a race.

Probabilities within each race should be calibrated and normalized. Accuracy alone is not sufficient; use probabilistic and ranking metrics.

## Candidate Features

Only include information available before the race starts.

- Horse: age, sex, rating, weight carried, recent form, days since last race.
- Connections: jockey and trainer rolling statistics.
- Race: course, surface, distance, class, going, rail position, field size.
- Entry: draw, declared weight, equipment changes.
- History: speed figures and course/distance performance calculated from prior races.
- Market: pre-race odds captured at a documented timestamp, if permitted.

Never calculate a pre-race feature from final results or data published after the prediction timestamp.

## Recommended Technology

- Python 3.12+
- `pandas` or `polars` for data preparation
- `scikit-learn` for preprocessing and baseline models
- LightGBM, XGBoost, or CatBoost for tabular ranking/probability models
- Optuna for hyperparameter tuning
- MLflow for experiment tracking and model registration
- Prefect, Dagster, or GitHub Actions for scheduled automation
- Pandera or Great Expectations for data validation

## Project layout

Week 1:

```text
HKJC-Horse/
|-- README.md
|-- pyproject.toml
|-- src/hkjc_predictor/
|   |-- cli.py
|   `-- ingestion/
|-- tests/
|   `-- fixtures/
`-- data/          # CSV and Parquet exports; hkjc.duckdb stays local
```

Later modelling work can add `features/`, `models/`, `evaluation/`, and `pipelines/` under `src/hkjc_predictor/`.

The raw HTML cache (`cache/`), the DuckDB file, and credentials are not committed. Small CSV and Parquet exports under `data/` may be committed for this private project when they stay under about 20 MB in total. Do not republish those extracts.

## Development Roadmap

### 1. Data Contract

Define one row per runner and stable identifiers for meetings, races, horses, and entries. Record `source_timestamp`, `prediction_timestamp`, and `result_timestamp` so leakage checks can be enforced.

### 2. Baseline Model

Build a simple baseline before tuning:

- chronological train/validation/test split;
- missing-value handling fitted on training data only;
- logistic regression or gradient-boosted trees;
- race-level probability normalization;
- saved features, configuration, random seed, and model artifact.

### 3. Evaluation

Use a walk-forward backtest rather than a random split. Report at least:

- multiclass log loss or race-level negative log likelihood;
- Brier score;
- calibration error and reliability chart;
- top-1 winner accuracy;
- NDCG or Spearman correlation for ranking;
- performance by season, course, distance, class, and odds band.

Any return-on-investment simulation should be reported separately and must include realistic timing, odds availability, dead heats, scratches, and transaction assumptions. It must not be the sole model-selection metric.

### 4. Automated Fine-Tuning

A scheduled pipeline should:

1. Ingest newly available results from permitted sources.
2. Validate schema, uniqueness, ranges, and missingness.
3. Rebuild time-aware features from immutable snapshots.
4. Create expanding-window or rolling-window folds.
5. Tune only on training and validation periods.
6. Evaluate the selected candidate once on a held-out period.
7. Compare it with the registered production model.
8. Register and promote it only if quality thresholds pass.
9. Save metrics, parameters, data version, code revision, and artifacts.
10. Produce a report and alert on failure or data drift.

Suggested promotion gates:

- no data-validation or leakage failures;
- improved log loss with no material calibration regression;
- stable results across multiple recent time windows;
- minimum sample size met;
- deterministic rerun within an accepted tolerance;
- manual approval before any production deployment.

## Reproducibility Rules

- Version datasets or store content hashes.
- Keep all settings in configuration files.
- Pin dependencies and record the Python version.
- Set random seeds where supported.
- Never overwrite a model artifact; create a new version.
- Log the exact feature list and prediction timestamp.
- Keep the final test period untouched during tuning.

## Initial Milestones

- [x] Document the public local-results pages used for private research (see Week 1). Confirm HKJC terms before any use beyond that.
- [x] Define the runner-level dataset schema (meetings, races, runners, dividends, odds_snapshots).
- [x] Build historical ingestion with parser tests and a live smoke test.
- [ ] Add leakage-safe rolling features.
- [ ] Train and backtest a baseline model.
- [ ] Add calibration and ranking reports.
- [ ] Add Optuna tuning with time-series folds.
- [ ] Add experiment tracking and model registration.
- [ ] Schedule retraining in dry-run mode.
- [ ] Add drift monitoring and approval-based promotion.

## Responsible Use

Horse-racing outcomes contain substantial randomness. Historical performance does not guarantee future results. Set strict limits, comply with local laws and HKJC rules, and treat every prediction as an uncertain estimate rather than a promise.

## License

No license has been selected yet. Add one before distributing or accepting external contributions.
