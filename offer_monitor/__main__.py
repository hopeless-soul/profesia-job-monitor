# CLI. By default runs forever, scraping every job source once per `interval` minutes.
# Every setting comes from data/configs/config.yaml; its `providers` list picks the sources,
# and each reads its own file in the same folder (profesia_sk.yaml, linkedin.yaml).
#
#     python -m offer_monitor
#     python -m offer_monitor --once               # single run, then exit
#
# Offers are stored in SQLite (config `db`). With a Telegram token (config
# telegram.token or TELEGRAM_BOT_TOKEN), each new offer is sent to everyone who
# sent /start to the bot. With an LLM configured (config `model`), every new
# offer then gets a verdict that is saved and added to its Telegram message.
# `linkedin` in `providers` turns LinkedIn searches on (local runs only).
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

from .config import CONFIG_DIR, Config, load_config
from .errors import ConfigError
from .models import Job, Verdict
from .providers import build_providers
from .providers.base import SourceProvider, SourceProviderError
from .store import (
    connect,
    count_jobs,
    known_ids,
    now_iso,
    refresh_known,
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

# ANSI colors for console output only (not logs); 256-color orange, since the 16-color yellow looks more yellow than orange
YELLOW, ORANGE, GREY, RESET = "\033[33m", "\033[38;5;208m", "\033[90m", "\033[0m"

# Background workers for verdicts
VERDICTS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="verdict")


# Load config file and build the providers it lists (each reads its own file next to it)
def read_config(parser: argparse.ArgumentParser) -> tuple[Config, list[SourceProvider]]:
    try:
        config = load_config()
        return config, build_providers(config.providers, CONFIG_DIR)
    except ConfigError as e:
        parser.error(str(e))


# Fetch one source
# Note: Retry-After when it sent one; a failure a retry cannot fix gives up at once. None when the source gives up.
def fetch_source(
    provider: SourceProvider, known: Callable[[list[int]], set[int]]
) -> list[Job] | None:
    attempts, delay = provider.retry.attempts, provider.retry.delay
    for attempt in range(1, attempts + 1):
        try:
            return provider.fetch(known)
        except SourceProviderError as e:
            kind = type(e).__name__
            if not e.retryable:
                logging.warning(
                    "%s failed (%s), not retrying: %s", provider.name, kind, e
                )
                return None
            # the last attempt gives up; earlier ones wait and try again
            if attempt == attempts:
                logging.warning(
                    "%s skipped after %d attempt(s) (%s): %s",
                    provider.name,
                    attempts,
                    kind,
                    e,
                )
                return None
            # wait what the source asked for, or the policy's delay when it sent no Retry-After
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


# Run Scrape Once
def run_once(
    config: Config,
    bot: Bot | None,
    subs: Subscribers | None,
    review: Callable[[str], Verdict] | None = None,
    *,
    providers: list[SourceProvider],
):
    # STAGE 1
    # fetch: every source fetches and drops its own repeats; a failing one is retried, then logged
    # and skipped while the others still run
    fetched = []
    for provider in providers:
        with closing(connect(config.db)) as conn:
            jobs = fetch_source(provider, partial(known_ids, conn, provider.name))
        if jobs is None:
            continue
        jobs = provider.dedup_fetched(jobs)
        print(f"{provider.name}: {len(jobs)} offers")
        fetched.append((provider, jobs))
    scraped = sum(len(jobs) for _, jobs in fetched)

    # STAGE 2
    # store (one transaction): each source picks its offers not stored yet, stored ones are refreshed,
    # then the new ones are inserted; a title + company duplicate across sources is stored but not returned
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
        print(
            f"{YELLOW}NEW {job.source} {job.id}  {job.title} | {job.company} | {salary}{RESET}"
        )
    # summary line in orange, then a blank line
    print(
        f"{ORANGE}Scraped {scraped}, new {len(added)}, dup {dups}, total {total} in {config.db}{RESET}"
    )
    print()

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

    # STAGE 3,
    # verdicts: only after every source is fetched and stored; on_verdict runs when each one finishes
    if review:
        for job in added:
            future = VERDICTS.submit(review, job.url)
            future.add_done_callback(
                partial(on_verdict, config.db, bot, job, message_ids.get(job.uuid, {}))
            )


# Verdict callback: save it, add it to the offer's Telegram messages, log it
def on_verdict(
    db: Path, bot: Bot | None, job: Job, message_ids: dict[int, int], future: Future
) -> None:
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
        backstory += "".join(
            f"\n\n## Pet project: {p.stem}\n\n{p.read_text(encoding='utf-8')}"
            for p in projects
        )
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
    # log lines get a grey timestamp in brackets, same format as the run header
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format=f"{GREY}[%(asctime)s]{RESET} %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Load config and the providers it lists
    config, providers = read_config(parser)

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
            except TelegramAuthError:
                parser.error("Telegram rejected the bot token")
            except TelegramError as e:
                logging.warning("subscriber sync skipped: %s", e)
        run_once(config, bot, subs, review, providers=providers)
        # wait for pending verdicts before exiting
        VERDICTS.shutdown(wait=True)
        return 0

    # CRON
    interval = config.interval * 60
    next_run = time.monotonic()
    bot_note = (
        f"\nTelegram: {len(subs.chats)} Telegram subscriber(s)"
        if subs
        else ", Telegram: OFF"
    )
    verdict_note = "\nVerdicts: ON" if review else "\nVerdicts: OFF"
    linkedin_note = "\nLinkedIn: ON" if "linkedin" in config.providers else ""
    print(
        f"Running: scraping every {config.interval:g} min. Ctrl+C to stop.{bot_note}{verdict_note}{linkedin_note}\n"
    )
    try:
        while True:
            try:
                if time.monotonic() >= next_run:
                    next_run = time.monotonic() + interval
                    run_once(config, bot, subs, review, providers=providers)
                wait = max(0, int(next_run - time.monotonic()))
                if bot and subs:
                    # Long-poll so /start and /stop get answered right away between scrapes.
                    sync_subscribers(bot, subs, timeout=min(30, wait))
                else:
                    time.sleep(wait)
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


if __name__ == "__main__":
    sys.exit(main())
