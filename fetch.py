"""Stage 1: fetch anime metadata from the AniList GraphQL API into SQLite.

Usage:
    uv run fetch.py --sample 3000   # popular-first pool into data/sample.db
    uv run fetch.py --add SCORE_DESC 300 --before 2000 --min-popularity 5000
                                    # add extra entries to that pool
    uv run fetch.py                 # full catalogue into data/animap.db, resumable
    uv run fetch.py --reset         # forget progress and start the full fetch over

The full fetch pages through every non-adult anime sorted by ID, which keeps
page boundaries stable, so a restart continues from the last committed page.
Each page and its progress marker are written in a single transaction.
"""

import argparse
import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

API_URL = "https://graphql.anilist.co"
DATA_DIR = Path(__file__).parent / "data"

QUERY = """
query ($page: Int, $perPage: Int, $sort: [MediaSort], $minPopularity: Int, $startBefore: FuzzyDateInt) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { currentPage hasNextPage }
    media(type: ANIME, isAdult: false, sort: $sort, popularity_greater: $minPopularity, startDate_lesser: $startBefore) {
      id idMal
      title { romaji english native }
      synonyms
      description(asHtml: false)
      format episodes duration status source
      seasonYear startDate { year month }
      genres
      tags { id name rank category isMediaSpoiler isGeneralSpoiler isAdult }
      studios { edges { isMain node { id name isAnimationStudio } } }
      staff(sort: RELEVANCE, perPage: 25) { edges { role node { id name { full } } } }
      relations { edges { relationType node { id type format } } }
      recommendations(sort: RATING_DESC, perPage: 10) { nodes { rating mediaRecommendation { id } } }
      popularity favourites averageScore meanScore
      coverImage { medium color }
      siteUrl
    }
  }
}
"""


class RateLimiter:
    """Keeps requests at or below a fixed rate and honours AniList's headers."""

    def __init__(self, rpm):
        self.interval = 60.0 / rpm
        self.last = 0.0

    def wait(self):
        delay = self.last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()

    def observe(self, response):
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and int(remaining) <= 1 and reset:
            pause = max(0.0, int(reset) - time.time()) + 1
            print(f"  rate limit nearly spent, pausing {pause:.0f}s")
            time.sleep(pause)


def post_with_retry(client, limiter, variables, max_tries=8):
    for attempt in range(max_tries):
        limiter.wait()
        try:
            response = client.post(API_URL, json={"query": QUERY, "variables": variables})
        except httpx.TransportError as exc:
            pause = min(120, 2 ** (attempt + 1)) + random.uniform(0, 1)
            print(f"  network error ({exc!r}), retrying in {pause:.0f}s")
            time.sleep(pause)
            continue

        if response.status_code == 429:
            pause = int(response.headers.get("Retry-After", 60)) + 1
            print(f"  429 rate limited, sleeping {pause}s")
            time.sleep(pause)
            continue

        if response.status_code >= 500:
            pause = min(120, 2 ** (attempt + 1)) + random.uniform(0, 1)
            print(f"  HTTP {response.status_code}, retrying in {pause:.0f}s")
            time.sleep(pause)
            continue

        body = response.json()
        if response.status_code != 200 or body.get("errors"):
            raise RuntimeError(f"AniList error {response.status_code}: {body.get('errors')}")

        limiter.observe(response)
        return body["data"]["Page"]

    raise RuntimeError(f"giving up after {max_tries} attempts")


def open_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS media (id INTEGER PRIMARY KEY, json TEXT NOT NULL, fetched_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS fetch_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM fetch_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def save_page(conn, page_number, page):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(m["id"], json.dumps(m, ensure_ascii=False), now) for m in page["media"]]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO media VALUES (?, ?, ?)", rows)
        conn.execute("INSERT OR REPLACE INTO fetch_state VALUES ('last_page', ?)", (str(page_number),))
        conn.execute("INSERT OR REPLACE INTO fetch_state VALUES ('has_next', ?)", (str(int(page["pageInfo"]["hasNextPage"])),))


def run(db_path, sort, per_page, rpm, limit=None, reset=False, clear=False):
    conn = open_db(db_path)
    if reset or clear:
        with conn:
            conn.execute("DELETE FROM fetch_state")
            if clear:
                conn.execute("DELETE FROM media")

    if get_state(conn, "has_next") == "0":
        count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        print(f"{db_path.name} is already complete ({count} entries). Use --reset to refetch.")
        return

    page_number = int(get_state(conn, "last_page", 0)) + 1
    limiter = RateLimiter(rpm)
    started = time.monotonic()

    with httpx.Client(timeout=60, headers={"User-Agent": "animap/0.1"}) as client:
        while True:
            page = post_with_retry(client, limiter, {"page": page_number, "perPage": per_page, "sort": [sort]})
            save_page(conn, page_number, page)
            total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            elapsed = time.monotonic() - started
            print(f"page {page_number}: +{len(page['media'])} (total {total}, {elapsed:.0f}s)")

            if not page["pageInfo"]["hasNextPage"]:
                print("done, reached the last page")
                break
            if limit and total >= limit:
                print(f"done, reached the sample size of {limit}")
                break
            page_number += 1


def add(db_path, sort, count, per_page, rpm, min_popularity=None, before=None):
    """Add the top `count` entries for another sort order to an existing pool.

    Rows already present are simply refreshed, and the pool's own progress
    state is left alone.
    """
    conn = open_db(db_path)
    existing = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    limiter = RateLimiter(rpm)
    variables = {"perPage": per_page, "sort": [sort], "minPopularity": min_popularity, "startBefore": before * 10000 if before else None}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with httpx.Client(timeout=60, headers={"User-Agent": "animap/0.1"}) as client:
        for page_number in range(1, -(-count // per_page) + 1):
            page = post_with_retry(client, limiter, {**variables, "page": page_number})
            rows = [(m["id"], json.dumps(m, ensure_ascii=False), now) for m in page["media"]]
            with conn:
                conn.executemany("INSERT OR REPLACE INTO media VALUES (?, ?, ?)", rows)
            print(f"{sort} page {page_number}: {len(rows)} entries")
            if not page["pageInfo"]["hasNextPage"]:
                break
    after = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    print(f"added {after - existing} new entries ({after} in {db_path.name})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, help="fetch roughly N popular entries into data/sample.db")
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--rpm", type=float, default=28, help="requests per minute (AniList currently allows 30)")
    parser.add_argument("--reset", action="store_true", help="discard saved progress and start over")
    parser.add_argument("--add", nargs=2, metavar=("SORT", "N"), help="add the top N entries by an AniList sort (e.g. SCORE_DESC) to data/sample.db")
    parser.add_argument("--min-popularity", type=int, help="with --add: only entries above this popularity")
    parser.add_argument("--before", type=int, help="with --add: only entries that started before this year")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    try:
        if args.add:
            add(DATA_DIR / "sample.db", args.add[0], int(args.add[1]), args.per_page, args.rpm, args.min_popularity, args.before)
        elif args.sample:
            run(DATA_DIR / "sample.db", "POPULARITY_DESC", args.per_page, args.rpm, limit=args.sample, clear=True)
        else:
            run(DATA_DIR / "animap.db", "ID", args.per_page, args.rpm, reset=args.reset)
    except KeyboardInterrupt:
        print("\ninterrupted; progress up to the last completed page is saved")
        sys.exit(130)


if __name__ == "__main__":
    main()
