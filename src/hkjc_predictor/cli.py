"""Command line interface for the HKJC results collector."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from hkjc_predictor import __version__
from hkjc_predictor.ingestion.client import PoliteFetcher
from hkjc_predictor.ingestion.pipeline import backfill, hk_today, update
from hkjc_predictor.ingestion.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hkjc",
        description=(
            "Collect public Hong Kong Jockey Club local results into DuckDB. "
            "Private research only: do not republish the data or use it to place bets."
        ),
    )
    parser.add_argument("--version", action="version", version=f"hkjc {__version__}")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--db",
        type=Path,
        default=Path("data/hkjc.duckdb"),
        help="DuckDB file path (default: data/hkjc.duckdb)",
    )
    shared.add_argument(
        "--cache",
        type=Path,
        default=Path("cache"),
        help="Directory for cached raw HTML (default: cache/)",
    )
    shared.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Minimum seconds between HTTP requests (default: 1.0; values below 1 are raised)",
    )
    shared.add_argument(
        "--force",
        action="store_true",
        help="Refetch pages even when the meeting is already stored",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    backfill_parser = sub.add_parser(
        "backfill",
        parents=[shared],
        help="Fetch local meetings in a date range",
    )
    backfill_parser.add_argument(
        "--from",
        dest="date_from",
        default="2024-01-01",
        help="First meeting date, YYYY-MM-DD (default: 2024-01-01)",
    )
    backfill_parser.add_argument(
        "--to",
        dest="date_to",
        default=None,
        help="Last meeting date, YYYY-MM-DD (default: today in Hong Kong)",
    )

    sub.add_parser(
        "update",
        parents=[shared],
        help="Fetch meetings newer than the latest complete meeting",
    )
    export_parser = sub.add_parser(
        "export",
        parents=[shared],
        help="Write CSV and Parquet extracts to data/",
    )
    export_parser.add_argument(
        "--out",
        type=Path,
        default=Path("data"),
        help="Export directory (default: data/)",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    store = Store(args.db)
    try:
        if args.command == "export":
            paths = store.export(args.out)
            _print_totals(store)
            for path in paths:
                print(path)
            return 0
        with PoliteFetcher(args.cache, min_interval=args.delay) as fetcher:
            if args.command == "backfill":
                start = _parse_date(args.date_from, "--from")
                end = hk_today() if args.date_to is None else _parse_date(args.date_to, "--to")
                logging.getLogger(__name__).info(
                    "backfill %s to %s at about 1 request/second; already stored meetings are skipped",
                    start,
                    end,
                )
                summary = backfill(store, fetcher, start=start, end=end, force=args.force)
            else:
                summary = update(store, fetcher, force=args.force)
            _print_summary(summary, fetcher.http_requests, fetcher.cache_hits)
            _print_totals(store)
            return 1 if summary.errors else 0
    finally:
        store.close()


def _parse_date(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{flag} must be YYYY-MM-DD, got {value!r}") from exc


def _print_summary(summary, http_requests: int, cache_hits: int) -> None:
    print(
        "meetings "
        f"complete={summary.meetings_complete} "
        f"empty={summary.meetings_empty} "
        f"partial={summary.meetings_partial} "
        f"skipped={summary.meetings_skipped}"
    )
    print(
        f"races_stored={summary.races_stored} "
        f"http_requests={http_requests} "
        f"cache_hits={cache_hits} "
        f"errors={summary.errors}"
    )
    for note in summary.notes:
        print(f"note: {note}")


def _print_totals(store: Store) -> None:
    totals = store.totals()
    print(
        "database "
        + " ".join(f"{name}={count}" for name, count in totals.items())
    )


if __name__ == "__main__":
    raise SystemExit(main())
