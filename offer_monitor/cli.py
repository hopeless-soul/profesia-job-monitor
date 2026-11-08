# Imports
import argparse
import subprocess
from pathlib import Path


# CLI arguments: run modes only; every setting lives in data/configs/config.yaml
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offer_monitor",
        description="Monitor job sources for new offers, store them in SQLite and send them to Telegram.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="scrape once and exit instead of running forever",
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

# Switch on ANSI escape codes in old Windows consoles
subprocess.run("", shell=True)
