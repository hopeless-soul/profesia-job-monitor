# Imports
import argparse
import os
from pathlib import Path

from .globals import DEFAULT_CONFIG


# CLI arguments
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offer_monitor",
        description="Scrape Profesia.sk offers into a SQLite database.",
    )
    p.add_argument(
        "keyword",
        nargs="?",
        help="one-off search text; when given, the config's searches are ignored",
    )
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="config file (default: data/configs/config.yaml)",
    )
    p.add_argument("--db", type=Path, help="SQLite database (overrides config)")
    p.add_argument(
        "--pages", type=int, help="max listing pages per search (overrides config)"
    )
    p.add_argument(
        "--delay", type=float, help="seconds between page requests (overrides config)"
    )
    p.add_argument(
        "--days",
        type=int,
        metavar="N",
        help="only offers posted in the last N days (overrides config for every search)",
    )
    filters = p.add_argument_group("one-off search filters (require KEYWORD)")
    filters.add_argument(
        "--segment",
        action="append",
        default=[],
        metavar="SLUG",
        help="path filter, repeatable, e.g. bratislavsky-kraj, plny-uvazok",
    )
    filters.add_argument(
        "--remote",
        type=int,
        choices=[0, 1, 2],
        help="0 on-site only, 1 remote only, 2 partially remote",
    )
    filters.add_argument("--min-salary", type=int, help="minimum salary in EUR")
    filters.add_argument(
        "--salary-period",
        choices=["m", "h"],
        default="m",
        help="m = monthly, h = hourly",
    )
    p.add_argument(
        "--interval",
        type=float,
        metavar="MINUTES",
        help="minutes between scrapes (overrides config, default 60)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="scrape once and exit instead of running forever",
    )
    p.add_argument(
        "--import-json",
        type=Path,
        metavar="FILE",
        help="import offers from an old jobs.json into the database, then exit",
    )
    p.add_argument(
        "--no-notify", action="store_true", help="don't send Telegram messages"
    )
    p.add_argument(
        "--log",
        type=Path,
        metavar="FILE",
        help="append all output to FILE (for scheduled runs without a console)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p

# Switch on ANSI escape codes in old Windows consoles (colors live in globals.py)
os.system("")
