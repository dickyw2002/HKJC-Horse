# HKJC Horse Racing Predictor

A machine-learning project for estimating Hong Kong Jockey Club (HKJC) horse-racing outcomes and improving models through an automated, reproducible fine-tuning pipeline.

> This project is for research and educational use. Predictions are uncertain and are not financial advice. Use data only where permitted by the source's terms, robots policy, and applicable law. Do not use automation to place bets.

## Week 1 — local results collector

Week 1 is a polite, resumable collector for **local** Hong Kong meetings (Sha Tin `ST` and Happy Valley `HV`). It does not place bets and it does not republish HKJC data. Overseas simulcast meetings (`S1`, `S2`, `S3`) are ignored.

The current results URL is:

```text
https://racing.hkjc.com/en-us/local/information/localresults?racedate=YYYY/MM/DD&Racecourse=ST&RaceNo=N
```

Older `.aspx` results URLs redirect. Meetings before the current season redirect again to `/en-us/local/information/archive/localresults`, which is the same HTML tables. Meeting dates come from the fixture calendar (`/en-us/local/information/fixture?calyear=YYYY&calmonth=MM`) plus the date-list JSON used by the results page dropdown.

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
```

Useful flags: `--db`, `--cache`, `--delay` (minimum 1 second), `--force` (redownload). Re-runs skip meetings already marked complete and read cached HTML instead of calling the site again. Rows are upserted, so a repeated race does not duplicate runners or dividends.

### Database

| Table | Contents |
| --- | --- |
| `meetings` | Date, `ST`/`HV`, venue name, completeness, race numbers |
| `races` | Class, distance, rating band, going, course/rail, name, prize, sectionals |
| `runners` | `finish_position` (text such as `1`, `1 DH`, `WV`), horse number, name, brand code, horse id, jockey, trainer, actual weight, declared horse weight, draw, lengths behind, running positions, finish time, win odds, incident |
| `dividends` | Pool, winning combination, dividend in HK$ |
| `odds_snapshots` | Empty. Schema is reserved for later pre-race odds logging |
| `fetched_pages` | Audit of cached page outcomes (not exported) |

Horse, jockey, and trainer ids are the public ids in the profile links (`HK_2024_K209`, and the short jockey/trainer codes). `lbw` is the original margin token (`---`, `N`, `1-1/4`, …). `lbw_lengths` is a convenience conversion, not an official HKJC field: nose 0.05, short head 0.1, head 0.2, neck (`N`) 0.3, dead-heat and `---` are 0, and `whole-num/den` fractions are added. A leading minus on the winner (`-SH`, `-HD`) is stored as a negative winning margin. `ML` (many lengths) stays null. Unknown tokens stay null.

Jockey and trainer ids are filled only when the results page links a profile. Visiting riders and some apprentices are printed as plain text, so the name is stored and the id is null.

The checked-in `data/*.csv` and `data/*.parquet` files are a private snapshot of local meetings from 2024-01-01 through 2026-09-27. Do not republish them. Refresh with `hkjc update` and `hkjc export`.

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
- Abandoned races (no runners, pool dividends marked `REFUND`) are stored with `races.abandoned` set. `hkjc update` retries any meeting left `partial`.
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
