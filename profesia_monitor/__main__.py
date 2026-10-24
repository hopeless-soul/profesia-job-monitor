"""CLI. By default runs forever, scraping every search in config.json once per
`interval` minutes (default 60). Ctrl+C stops it.

    python -m profesia_monitor
    python -m profesia_monitor --interval 30        # scrape every 30 min
    python -m profesia_monitor --once               # single run, then exit
    python -m profesia_monitor "c#" --remote 2 --once   # one-off search, ignores config searches

With a Telegram token (config telegram.token or TELEGRAM_BOT_TOKEN), new offers
are sent to everyone who sent /start to the bot.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config, ConfigError, load_config
from .models import Job
from .scraper import Query, scrape
from .store import load_jobs, merge_jobs, save_jobs
from .telegram import Bot, Subscribers, TelegramError, handle_updates, notify

DEFAULT_CONFIG = Path("config.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="profesia_monitor", description="Scrape Profesia.sk offers into a JSON file.")
    p.add_argument("keyword", nargs="?", help="one-off search text; when given, the config's searches are ignored")
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG, help="config file (default: config.json)")
    p.add_argument("--out", type=Path, help="JSON store (overrides config)")
    p.add_argument("--pages", type=int, help="max listing pages per search (overrides config)")
    p.add_argument("--delay", type=float, help="seconds between page requests (overrides config)")
    p.add_argument("--days", type=int, metavar="N", help="only offers posted in the last N days (overrides config for every search)")
    filters = p.add_argument_group("one-off search filters (require KEYWORD)")
    filters.add_argument("--segment", action="append", default=[], metavar="SLUG",
                         help="path filter, repeatable, e.g. bratislavsky-kraj, plny-uvazok")
    filters.add_argument("--remote", type=int, choices=[0, 1, 2],
                         help="0 on-site only, 1 remote only, 2 partially remote")
    filters.add_argument("--min-salary", type=int, help="minimum salary in EUR")
    filters.add_argument("--salary-period", choices=["m", "h"], default="m", help="m = monthly, h = hourly")
    p.add_argument("--interval", type=float, metavar="MINUTES",
                   help="minutes between scrapes (overrides config, default 60)")
    p.add_argument("--once", action="store_true", help="scrape once and exit instead of running forever")
    p.add_argument("--no-notify", action="store_true", help="don't send Telegram messages")
    p.add_argument("--log", type=Path, metavar="FILE",
                   help="append all output to FILE (for scheduled runs without a console)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def resolve_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    """Merge config file and CLI flags. CLI flags win."""
    if args.keyword is None and (args.segment or args.remote is not None or args.min_salary is not None):
        parser.error("search filters need a KEYWORD; put them in the config file instead")

    config_given = args.config != DEFAULT_CONFIG
    if args.config.exists():
        try:
            config = load_config(args.config)
        except ConfigError as e:
            parser.error(f"{args.config}: {e}")
    elif config_given or args.keyword is None:
        parser.error(f"{args.config} not found; copy config.example.json to config.json or pass a KEYWORD")
    else:
        config = Config()

    if args.keyword is not None:
        query = Query(args.keyword, args.segment, args.remote, args.min_salary, args.salary_period)
        try:
            query.url()
        except ValueError as e:
            parser.error(str(e))
        config.searches = [query]
    if args.days is not None:
        for query in config.searches:
            query.count_days = args.days
    if args.out is not None:
        config.out = args.out
    if args.pages is not None:
        config.pages = args.pages
    if args.delay is not None:
        config.delay = args.delay
    if args.interval is not None:
        if args.interval <= 0:
            parser.error("--interval must be positive")
        config.interval = args.interval
    return config


def run_once(config: Config, bot: Bot | None, subs: Subscribers | None) -> None:
    scraped: list[Job] = []
    for query in config.searches:
        jobs = scrape(query, max_pages=config.pages, delay=config.delay)
        print(f"{' '.join([repr(query.keyword), *query.segments])}: {len(jobs)} offers")
        scraped.extend(jobs)

    existing = load_jobs(config.out)
    merged, added = merge_jobs(existing, scraped)
    save_jobs(config.out, merged)

    for job in added:
        salary = job.salary.raw if job.salary else "-"
        print(f"NEW {job.id}  {job.title} | {job.company} | {salary}")
    print(f"Scraped {len(scraped)}, new {len(added)}, total {len(merged)} in {config.out}")

    if bot and subs:
        if notify(bot, subs, added):
            subs.save()
        if added:
            print(f"Notified {len(subs.chats)} Telegram subscriber(s) \n\n")


def sync_subscribers(bot: Bot, subs: Subscribers, timeout: int = 0) -> None:
    if handle_updates(bot, subs, timeout):
        subs.save()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
        print(f"--- {datetime.now().isoformat(timespec='seconds')}")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    config = resolve_config(args, parser)

    bot = subs = None
    token = config.telegram.token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and not args.no_notify:
        bot, subs = Bot(token), Subscribers.load(config.telegram.subscribers)

    if args.once:
        if bot and subs:
            try:
                sync_subscribers(bot, subs)  # pick up /start and /stop sent since the last run
            except TelegramError as e:
                if e.code == 401:
                    parser.error("Telegram rejected the bot token")
                logging.warning("subscriber sync skipped: %s", e)
        run_once(config, bot, subs)
        return 0

    interval = config.interval * 60
    next_run = time.monotonic()
    bot_note = f", {len(subs.chats)} Telegram subscriber(s)" if subs else ", Telegram off"
    print(f"Running: scraping every {config.interval:g} min{bot_note}. Ctrl+C to stop.")
    try:
        while True:
            try:
                if time.monotonic() >= next_run:
                    next_run = time.monotonic() + interval
                    run_once(config, bot, subs)
                wait = max(0, int(next_run - time.monotonic()))
                if bot and subs:
                    # Long-poll so /start and /stop get answered right away between scrapes.
                    sync_subscribers(bot, subs, timeout=min(30, wait))
                else:
                    time.sleep(wait)
            except TelegramError as e:
                if e.code == 401:
                    parser.error("Telegram rejected the bot token")
                logging.warning("%s; retrying in 30 s", e)
                time.sleep(30)
            except Exception:
                # One failed scrape (network, site change) must not kill the loop; the next hour retries.
                logging.exception("run failed; retrying in 30 s")
                time.sleep(30)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
