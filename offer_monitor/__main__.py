# CLI. By default runs forever, scraping every job source once per `interval` minutes.
# Every setting comes from data/configs/config.yaml; its `providers` list picks the sources,
# and each reads its own file in the same folder (profesia_sk.yaml, linkedin.yaml).
#
#     python -m offer_monitor
#     python -m offer_monitor --once               # single run, then exit
#
# Offers are stored in SQLite (config `db`). With a Telegram token (config
# telegram.token), each new offer is sent to everyone who
# sent /start to the bot. With an LLM configured (config `model`), every new
# offer then gets a verdict that is saved and added to its Telegram message.
# `linkedin` in `providers` turns LinkedIn searches on (local runs only).
# Only one monitor runs per database, wherever it runs: it holds a lease row in the db.
from __future__ import annotations

import logging
import os
import signal
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from functools import partial
from pathlib import Path

from .agents import AGENT
from .config import CONFIG_DIR, Config, load_config
from .errors import ConfigError
from .models import Job, Verdict
from .providers import build_providers
from .providers.base import SourceProvider, SourceProviderError
from .store import (
    LEASE_BEAT,
    LEASE_TTL,
    claim_lease,
    connect,
    count_jobs,
    known_ids,
    now_iso,
    refresh_known,
    release_lease,
    renew_lease,
    save_message_ids,
    save_verdict,
    store_new,
)
from .telegram import (
    Bot,
    Subscribers,
    TelegramAuthError,
    TelegramError,
    format_verdict,
    handle_updates,
    notify,
)
from .cli import build_parser

"""
ANSI Colors
"""
YELLOW, ORANGE, GREY, RESET = "\033[33m", "\033[38;5;208m", "\033[90m", "\033[0m"


def disable_colors():
    global YELLOW, ORANGE, GREY, RESET
    YELLOW = ORANGE = GREY = RESET = ""


"""
Background workers for verdicts
"""
VERDICTS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="verdict")


def fetch_source(provider: SourceProvider) -> list[Job] | None:
    """
    Fetches a source;
    Returns a list of offers / `None` when the source gives up;
    """
    attempts, delay = provider.retry.attempts, provider.retry.delay
    for attempt in range(1, attempts + 1):
        try:
            return provider.fetch()
        except SourceProviderError as e:
            kind = type(e).__name__
            # gives up
            if not e.retryable:
                logging.warning("%s failed (%s), not retrying: %s", provider.name, kind, e)
                return None
            # the last attempt gives up;
            if attempt == attempts:
                logging.warning(
                    "%s skipped after %d attempt(s) (%s): %s",
                    provider.name,
                    attempts,
                    kind,
                    e,
                )
                return None
            # wait if `retry_after` exist from the request result;
            # wait `delay` from the configuration;
            wait = e.retry_after if e.retry_after is not None else delay
            logging.warning(
                "%s attempt %d of %d failed (%s), retrying in %gs: %s",
                provider.name,
                attempt,
                attempts,
                kind,
                wait,
                e,
            )
            time.sleep(wait)
    return None


def run(
    config: Config,
    bot: Bot | None,
    subs: Subscribers | None,
    review: Callable[[str], Verdict] | None = None,
    *,
    providers: list[SourceProvider],
):
    """
    Run cycle once:
    * fetch every source,
    * store new offers,
    * notify subscribers,
    * run verdicts on them.
    """
    # STAGE 1
    # fetch: every source fetches and drops its own repeats; a failing one is retried, then logged
    # and skipped while the others still run; no database access here, stored offers are dropped in stage 2
    fetched = []
    for provider in providers:
        jobs = fetch_source(provider)
        if jobs is None:
            continue
        jobs = provider.dedup_fetched(jobs)
        print(f"{YELLOW}{provider.name}: {len(jobs)} offers{RESET}")
        fetched.append((provider, jobs))
    scraped = sum(len(jobs) for _, jobs in fetched)

    # STAGE 2
    # store (one transaction): each source picks its offers not stored yet, stored ones are refreshed,
    # then the new ones are inserted; a title + company duplicate across sources is still stored but passed down to the pipe;
    seen_at = now_iso()
    with closing(connect(config.db)) as conn, conn:
        before = count_jobs(conn)
        new: list[Job] = []
        for provider, jobs in fetched:
            new += provider.db_dedup(jobs, partial(known_ids, conn, provider.name))
            refresh_known(conn, jobs, seen_at)
        added = store_new(conn, new, seen_at)
        total = count_jobs(conn)
    dups = total - before - len(added)

    # print new offers and totals
    for job in added:
        salary = job.salary.raw if job.salary else "-"
        print(f"{YELLOW}NEW {job.source} {job.id}  {job.title} | {job.company} | {salary}{RESET}")
    # summary line in orange, then a blank line
    print(f"{ORANGE}Scraped {scraped}, new {len(added)}, dup {dups}, total {total} in {config.db}{RESET}")
    print()

    # STAGE 3
    # notify subscribers and remember which message each chat got
    message_ids: dict[str, dict[int, int]] = {}
    if bot and subs and added:
        changed, message_ids = notify(bot, subs, added)
        if changed:
            subs.save()
        with closing(connect(config.db)) as conn, conn:
            for offer_uuid, ids in message_ids.items():
                save_message_ids(conn, offer_uuid, ids)
        print(f"Notified {len(subs.active_ids())} Telegram subscriber(s) \n")

    # STAGE 4
    # verdicts: only after every source is fetched and stored; on_verdict runs when each one finishes
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
                logging.warning(
                    "verdict edit for offer %s in chat %s failed: %s",
                    job.id,
                    chat_id,
                    e,
                )

    logging.info("verdict %s for offer %s (uuid %s)", job.verdict.tag, job.id, job.uuid)


# Apply /start and /stop commands sent to the bot
def sync_subscribers(bot: Bot, subs: Subscribers, timeout: int = 0) -> None:
    if handle_updates(bot, subs, timeout):
        subs.save()


# Verdict pipeline (orchestrator agent); None when langchain, the API key or the backstory is missing
def build_review(config: Config) -> Callable[[str], Verdict] | None:
    # no `model` section: run without verdicts
    if config.model is None:
        logging.warning("verdicts disabled: no `model` section in config.yaml")
        return None
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


# Renew the lease in the background every LEASE_BEAT seconds (a scrape can take minutes);
# the returned event is set once another monitor has taken the lease over
def keep_lease(db: Path, owner: str) -> threading.Event:
    lost = threading.Event()

    def beat() -> None:
        while not lost.is_set():
            time.sleep(LEASE_BEAT)
            # own connection: this runs in its own thread; a busy db only skips one beat
            try:
                with closing(connect(db)) as conn, conn:
                    if not renew_lease(conn, owner, now_iso()):
                        lost.set()
            except sqlite3.Error as e:
                logging.warning("lease heartbeat skipped: %s", e)

    threading.Thread(target=beat, name="lease", daemon=True).start()
    return lost


# Main
def main(argv: list[str] | None = None) -> int:
    """
    Load run args
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
        print(f"--- {datetime.now().isoformat(timespec='seconds')}")

    """
    Setup logger
    """
    if args.log or not sys.stdout.isatty():
        disable_colors()

    # log lines get a grey timestamp in brackets, same format as the run header
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format=f"{GREY}[%(asctime)s]{RESET} %(levelname)s %(agent)s%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # tag each record with the agent running in this thread (empty outside the verdict pipeline);
    # a record factory, not `extra=`, because the httpx lines come from library code we don't call
    make_record = logging.getLogRecordFactory()

    def record_with_agent(*args, **kwargs):
        record = make_record(*args, **kwargs)
        name = AGENT.get()
        record.agent = f"[{name}] " if name else ""
        return record

    logging.setLogRecordFactory(record_with_agent)

    """
    Load configs
    """
    try:
        config = load_config()
        # Load providers mentioned in main config (each reads its own file next to it)
        providers = build_providers(config.providers, CONFIG_DIR)
    except ConfigError as e:
        parser.error(str(e))

    """
    Claim the lease in the database, or stop if another monitor is running there
    """
    # One monitor per database, wherever it runs (Windows or the Docker container)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    with closing(connect(config.db)) as conn, conn:
        holder = claim_lease(conn, owner, now_iso())
    if holder:
        parser.error(
            f"another monitor ({holder}) is using {config.db}; "
            f"if it has stopped, its lease expires within {LEASE_TTL // 60} min"
        )
    lease_lost = keep_lease(config.db, owner)
    # `docker stop` sends SIGTERM, which PID 1 ignores by default: treat it as Ctrl+C so the lease is released
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    """
    Run the monitor
    """
    try:
        """
        Telegram bot setup
        """
        bot = subs = None
        token = config.telegram.token
        if token and not args.no_notify:
            bot, subs = Bot(token), Subscribers.load(config.db, legacy=config.telegram.subscribers)

        """
        Verdict pipeline setup
        """
        review = build_review(config)

        """
        Run Once Scenario
        """
        if args.once:
            if bot and subs:
                try:
                    sync_subscribers(bot, subs)  # pick up /start and /stop sent since the last run
                except TelegramAuthError:
                    parser.error("Telegram rejected the bot token")
                except TelegramError as e:
                    logging.warning("subscriber sync skipped: %s", e)
            run(config, bot, subs, review, providers=providers)
            # wait for pending verdicts before exiting (they write to the db, so the lease is still held)
            VERDICTS.shutdown(wait=True)
            return 0

        """
        Run Forever Scenario
        """
        interval = config.interval * 60
        next_run = time.monotonic()
        bot_note = f"\nTelegram: {len(subs.active_ids())} Telegram subscriber(s)" if subs else ", Telegram: OFF"
        verdict_note = "\nVerdicts: ON" if review else "\nVerdicts: OFF"
        linkedin_note = "\nLinkedIn: ON" if "linkedin" in config.providers else ""
        print(
            f"Running: scraping every {config.interval:g} min. Ctrl+C to stop.{bot_note}{verdict_note}{linkedin_note}\n"
        )
        try:
            while True:
                # another monitor took the database over (this one stalled past the lease TTL): stop
                if lease_lost.is_set():
                    logging.error("another monitor took over %s; stopping", config.db)
                    VERDICTS.shutdown(wait=False, cancel_futures=True)
                    return 1
                #
                try:
                    if time.monotonic() >= next_run:
                        next_run = time.monotonic() + interval
                        run(config, bot, subs, review, providers=providers)
                    wait = max(0, int(next_run - time.monotonic()))
                    if bot and subs:
                        # Long-poll so /start and /stop get answered right away between scrapes.
                        sync_subscribers(bot, subs, timeout=min(30, wait))
                    else:
                        # wake at least every lease beat so a lost lease is noticed between long waits
                        time.sleep(min(wait, LEASE_BEAT))
                except TelegramAuthError:
                    parser.error("Telegram rejected the bot token")
                except TelegramError as e:
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
    finally:
        # free the database right away on any exit (a crash leaves it to expire after LEASE_TTL)
        with closing(connect(config.db)) as conn, conn:
            release_lease(conn, owner)


if __name__ == "__main__":
    sys.exit(main())
