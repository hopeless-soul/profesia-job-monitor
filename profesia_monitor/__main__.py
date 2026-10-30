"""CLI. By default runs forever, scraping every search in data/config.json once per
`interval` minutes (default 60). Ctrl+C stops it.

    python -m profesia_monitor
    python -m profesia_monitor --interval 30        # scrape every 30 min
    python -m profesia_monitor --once               # single run, then exit
    python -m profesia_monitor "c#" --remote 2 --once   # one-off search, ignores config searches
    python -m profesia_monitor --import-json jobs.json  # one-time import of the old JSON store

Offers are stored in SQLite (config `db`). With a Telegram token (config
telegram.token or TELEGRAM_BOT_TOKEN), each new offer is sent to everyone who
sent /start to the bot. With an LLM configured (config `model`), every new
offer then gets a verdict that is saved and added to its Telegram message.
With a `linkedin` section in the config, LinkedIn searches run alongside (local runs only).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from functools import partial
from pathlib import Path

from . import linkedin
from .config import Config, ConfigError, load_config
from .models import Job, Verdict
from .scraper import Query, scrape
from .store import (
    connect,
    count_jobs,
    insert_jobs,
    known_ids,
    load_jobs,
    save_message_ids,
    save_verdict,
    upsert_jobs,
)
from .telegram import Bot, Subscribers, TelegramError, format_verdict, handle_updates, notify

# Runtime files (config, jobs.db, subscribers, backstory) live in data/, shared with Docker's /data volume
DEFAULT_CONFIG = Path("data/config.json")

# Background workers for verdicts, so scraping and /start replies never wait on the LLM
VERDICTS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="verdict")


# CLI arguments
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="profesia_monitor",
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
        help="config file (default: data/config.json)",
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


def resolve_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    # Merge config file and CLI flags. CLI flags win.
    if args.keyword is None and (
        args.segment or args.remote is not None or args.min_salary is not None
    ):
        parser.error(
            "search filters need a KEYWORD; put them in the config file instead"
        )

    # Load config
    config_given = args.config != DEFAULT_CONFIG
    if args.config.exists():
        try:
            config = load_config(args.config)
        except ConfigError as e:
            parser.error(f"{args.config}: {e}")
    elif config_given or args.keyword is None:
        # require either explicit config or a one-off keyword
        parser.error(
            f"{args.config} not found; copy data/config.example.json to data/config.json or pass a KEYWORD"
        )
    else:
        config = Config()

    # one-off keyword search overrides all config searches
    if args.keyword is not None:
        query = Query(
            args.keyword, args.segment, args.remote, args.min_salary, args.salary_period
        )
        try:
            query.url()
        except ValueError as e:
            parser.error(str(e))
        config.searches = [query]
        config.linkedin = None  # one-off searches are Profesia-only

    # apply days filter to all searches
    if args.days is not None:
        for query in config.searches:
            query.count_days = args.days

    # override database location
    if args.db is not None:
        config.db = args.db

    # override max pages per search
    if args.pages is not None:
        config.pages = args.pages

    # override request delay
    if args.delay is not None:
        config.delay = args.delay

    # override scrape interval, must be positive
    if args.interval is not None:
        if args.interval <= 0:
            parser.error("--interval must be positive")
        config.interval = args.interval
    return config

# Run Scrape Once
def run_once(
    config: Config,
    bot: Bot | None,
    subs: Subscribers | None,
    review: Callable[[str], Verdict] | None = None,
) -> None:
    # scrape every search
    scraped: list[Job] = []
    for query in config.searches:
        jobs = scrape(query, max_pages=config.pages, delay=config.delay)
        print(f"{' '.join([repr(query.keyword), *query.segments])}: {len(jobs)} offers")
        scraped.extend(jobs)

    # LinkedIn searches; details are fetched only for jobs not stored yet
    if config.linkedin:
        with closing(connect(config.db)) as conn:
            jobs = linkedin.fetch_jobs(config.linkedin, partial(known_ids, conn, "linkedin"))
        print(f"linkedin: {len(jobs)} offers")
        scraped.extend(jobs)

    # store offers; new ones get a uuid and no verdict yet, duplicates are stored but not returned
    with closing(connect(config.db)) as conn, conn:
        before = count_jobs(conn)
        added = upsert_jobs(conn, scraped)
        total = count_jobs(conn)
    dups = total - before - len(added)

    # print new offers and totals
    for job in added:
        salary = job.salary.raw if job.salary else "-"
        print(f"NEW {job.source} {job.id}  {job.title} | {job.company} | {salary}")
    print(
        f"Scraped {len(scraped)}, new {len(added)}, dup {dups}, total {total} in {config.db}"
    )

    # notify subscribers and remember which message each chat got
    message_ids: dict[str, dict[int, int]] = {}
    if bot and subs and added:
        changed, message_ids = notify(bot, subs, added)
        if changed:
            subs.save()
        with closing(connect(config.db)) as conn, conn:
            for offer_uuid, ids in message_ids.items():
                save_message_ids(conn, offer_uuid, ids)
        print(f"Notified {len(subs.chats)} Telegram subscriber(s) \n\n")

    # start a verdict per new offer; on_verdict runs when each one finishes
    if review:
        for job in added:
            future = VERDICTS.submit(review, job.url)
            future.add_done_callback(partial(on_verdict, config.db, bot, job, message_ids.get(job.uuid, {})))


# Verdict callback: save it, add it to the offer's Telegram messages, log it
def on_verdict(db: Path, bot: Bot | None, job: Job, message_ids: dict[int, int], future: Future) -> None:
    # skip cancelled reviews (Ctrl+C), only log failed ones
    if future.cancelled():
        return
    try:
        job.verdict = future.result()
    except Exception:
        logging.exception("verdict failed for offer %s", job.id)
        return

    # save to the database (own connection: callbacks run in worker threads)
    with closing(connect(db)) as conn, conn:
        save_verdict(conn, job.uuid, job.verdict)

    # edit every subscriber's message for this offer
    if bot:
        text = format_verdict(job)
        for chat_id, message_id in message_ids.items():
            try:
                bot.edit(chat_id, message_id, text)
            except TelegramError as e:
                logging.warning("verdict edit for offer %s in chat %s failed: %s", job.id, chat_id, e)

    logging.info("verdict %s for offer %s (uuid %s)", job.verdict.tag, job.id, job.uuid)


# Apply /start and /stop commands sent to the bot
def sync_subscribers(bot: Bot, subs: Subscribers, timeout: int = 0) -> None:
    if handle_updates(bot, subs, timeout):
        subs.save()


# Verdict pipeline (orchestrator agent); None when langchain, the API key or the backstory is missing
def build_review(config: Config) -> Callable[[str], Verdict] | None:
    try:
        from .agents.llm import build_llm
        from .agents.orchestrator import build_orchestrator, evaluate

        backstory = config.backstory.read_text(encoding="utf-8")
        # append pet projects (projects/*.md next to the backstory) as extra evidence
        projects = sorted((config.backstory.parent / "projects").glob("*.md"))
        backstory += "".join(f"\n\n## Pet project: {p.stem}\n\n{p.read_text(encoding='utf-8')}" for p in projects)
        return partial(evaluate, build_orchestrator(build_llm(config.model), backstory))
    except Exception as e:
        logging.warning("verdicts disabled: %s", e)
        return None


# Main
def main(argv: list[str] | None = None) -> int:
    # Load run args
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
        print(f"--- {datetime.now().isoformat(timespec='seconds')}")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    # Load config
    config = resolve_config(args, parser)

    # Import Legacy JSON Scenario
    if args.import_json:
        if not args.import_json.exists():
            parser.error(f"{args.import_json} not found")
        jobs = load_jobs(args.import_json)
        with closing(connect(config.db)) as conn, conn:
            inserted = insert_jobs(conn, jobs)
        print(f"Imported {inserted} of {len(jobs)} offers from {args.import_json} into {config.db}")
        return 0

    # Bot setup
    bot = subs = None
    token = config.telegram.token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and not args.no_notify:
        bot, subs = Bot(token), Subscribers.load(config.telegram.subscribers)

    # Verdict agent setup
    review = build_review(config)

    # Run Once Scenario
    if args.once:
        if bot and subs:
            try:
                sync_subscribers(
                    bot, subs
                )  # pick up /start and /stop sent since the last run
            except TelegramError as e:
                if e.code == 401:
                    parser.error("Telegram rejected the bot token")
                logging.warning("subscriber sync skipped: %s", e)
        run_once(config, bot, subs, review)
        # wait for pending verdicts before exiting
        VERDICTS.shutdown(wait=True)
        return 0

    # CRON
    interval = config.interval * 60
    next_run = time.monotonic()
    bot_note = (
        f", {len(subs.chats)} Telegram subscriber(s)" if subs else ", Telegram off"
    )
    verdict_note = ", verdicts on" if review else ", verdicts off"
    linkedin_note = ", LinkedIn on" if config.linkedin else ""
    print(f"Running: scraping every {config.interval:g} min{bot_note}{verdict_note}{linkedin_note}. Ctrl+C to stop.")
    try:
        while True:
            try:
                if time.monotonic() >= next_run:
                    next_run = time.monotonic() + interval
                    run_once(config, bot, subs, review)
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
        # drop queued verdicts; ones already running finish before exit
        VERDICTS.shutdown(wait=False, cancel_futures=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
