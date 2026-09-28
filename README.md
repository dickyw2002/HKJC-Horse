# HKJC Horse Racing Predictor

A machine-learning project for estimating Hong Kong Jockey Club (HKJC) horse-racing outcomes and improving models through an automated, reproducible fine-tuning pipeline.

> This project is for research and educational use. Predictions are uncertain and are not financial advice. Use data only where permitted by the source's terms, robots policy, and applicable law. Do not use automation to place bets.

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

## Proposed Project Layout

```text
HKJC-Horse/
|-- README.md
|-- pyproject.toml
|-- .env.example
|-- configs/
|   |-- training.yaml
|   `-- tuning.yaml
|-- data/
|   |-- raw/
|   |-- interim/
|   `-- processed/
|-- models/
|-- notebooks/
|-- reports/
|   `-- figures/
|-- src/hkjc_predictor/
|   |-- ingestion/
|   |-- features/
|   |-- models/
|   |-- evaluation/
|   `-- pipelines/
`-- tests/
```

Raw data, trained model binaries, credentials, and local experiment databases should not be committed to Git.

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

- [ ] Confirm permitted data sources and document their terms.
- [ ] Define the runner-level dataset schema.
- [ ] Build historical ingestion with validation tests.
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
