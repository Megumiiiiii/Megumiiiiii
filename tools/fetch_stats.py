"""Fetch GitHub statistics into cache/: stats.json for the card, calendar.json
for the Pac-Man board.

Split from tools/build_card.py on purpose: this half is the slow, network-bound,
rate-limited part, so it caches aggressively and can fail without taking the
card down (build_card.py falls back to placeholders when the cache is absent).

Lines-of-code is the expensive figure -- it walks every commit you authored on
each repo's default branch. cache/loc.json memoises that per repo, keyed by the
branch head OID, so an unchanged repo costs one cheap field in the repo query
instead of a full history walk.

Requires a GITHUB_TOKEN in the environment (the Actions-provided token is
enough for public data).

Usage:
    GITHUB_TOKEN=... python tools/fetch_stats.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
STATS_OUT = CACHE / "stats.json"
LOC_CACHE = CACHE / "loc.json"
CALENDAR_OUT = CACHE / "calendar.json"

LOGIN = os.environ.get("GH_LOGIN", "Megumiiiiii")
ENDPOINT = "https://api.github.com/graphql"
TOKEN = os.environ.get("GITHUB_TOKEN")

session = requests.Session()


def query(document: str, variables: dict, attempt: int = 0) -> dict:
    """POST a GraphQL document, retrying transient failures.

    Retries on rate limits (403/429, honouring Retry-After) and on
    transient server errors (500/502/503/504) plus connection problems,
    with exponential backoff. A 502 Bad Gateway from api.github.com is a
    GitHub-side hiccup, not a problem with the query -- without the retry
    the whole fetch dies on the first hiccup.
    """
    try:
        resp = session.post(
            ENDPOINT,
            json={"query": document, "variables": variables},
            headers={"Authorization": f"bearer {TOKEN}"},
            timeout=30,
        )
    except requests.RequestException as exc:
        if attempt < 3:
            wait = 10 * (2 ** attempt)
            print(f"  request failed ({exc}), retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            return query(document, variables, attempt + 1)
        raise

    if resp.status_code in (403, 429, 500, 502, 503, 504) and attempt < 3:
        raw = resp.headers.get("Retry-After")
        try:
            wait = int(raw) if raw else 0
        except ValueError:
            wait = 0
        if wait <= 0:
            wait = 10 * (2 ** attempt)
        print(f"  HTTP {resp.status_code}, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
        return query(document, variables, attempt + 1)

    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        # Plain exception, not SystemExit: a single bad query (e.g. one repo
        # the token cannot read) must not kill the whole fetch -- fetch_loc
        # catches it per repo and skips that repo.
        raise RuntimeError(f"GraphQL error: {json.dumps(payload['errors'], indent=2)}")
    return payload["data"]


REPOS_Q = """
query($login:String!, $after:String) {
  user(login:$login) {
    id
    createdAt
    followers { totalCount }
    repositories(ownerAffiliations:OWNER, isFork:false, first:100, after:$after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        stargazerCount
        defaultBranchRef { target { ... on Commit { oid } } }
      }
    }
  }
}
"""

# The same last-year calendar GitHub draws on the profile. Anything the
# profile hides from visitors -- private contributions, unless the owner opts
# in -- this token cannot see either, so the board always matches the graph
# rendered directly beneath it.
CALENDAR_Q = """
query($login:String!) {
  user(login:$login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date weekday contributionCount contributionLevel }
        }
      }
    }
  }
}
"""

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
          "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

CONTRIB_Q = """
query($login:String!) {
  user(login:$login) {
    repositoriesContributedTo(
      first:1,
      contributionTypes:[COMMIT, PULL_REQUEST, REPOSITORY]
    ) { totalCount }
  }
}
"""

# contributionsCollection is capped at one year per call, so this gets asked
# once per year the account has existed.
COMMITS_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""

HISTORY_Q = """
query($owner:String!, $name:String!, $authorId:ID!, $after:String) {
  repository(owner:$owner, name:$name) {
    defaultBranchRef {
      target { ... on Commit {
        history(first:100, after:$after, author:{id:$authorId}) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { additions deletions }
        }
      }}
    }
  }
}
"""


def fetch_repos() -> tuple[dict, list[dict]]:
    """Return (user_meta, repos). Paginates through every owned, non-fork repo."""
    repos: list[dict] = []
    meta: dict = {}
    after = None
    while True:
        data = query(REPOS_Q, {"login": LOGIN, "after": after})["user"]
        if not meta:
            meta = {
                "id": data["id"],
                "createdAt": data["createdAt"],
                "followers": data["followers"]["totalCount"],
                "repos": data["repositories"]["totalCount"],
            }
        repos.extend(data["repositories"]["nodes"])
        page = data["repositories"]["pageInfo"]
        if not page["hasNextPage"]:
            return meta, repos
        after = page["endCursor"]


def fetch_commit_total(created_at: str) -> int:
    """Sum contributions year by year from account creation to now."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    total = 0
    cursor = start
    while cursor < now:
        try:
            next_year = cursor.replace(year=cursor.year + 1)
        except ValueError:
            next_year = cursor.replace(year=cursor.year + 1, day=28)  # Feb 29
        end = min(next_year, now)
        c = query(COMMITS_Q, {
            "login": LOGIN,
            "from": cursor.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        })["user"]["contributionsCollection"]
        total += c["totalCommitContributions"] + c["restrictedContributionsCount"]
        cursor = end
    return total


def _save_loc_cache(cache: dict) -> None:
    """Persist the LOC cache now, not only at the end of the walk.

    The old code wrote it once after every repo: one bad repo near the end
    threw the whole run away, including every repo already walked. Saving
    after each repo means a re-run resumes from the last good repo.
    """
    CACHE.mkdir(exist_ok=True)
    LOC_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _walk_repo(owner: str, short: str, author_id: str) -> tuple[int, int]:
    """Additions/deletions authored in one repo, paginating its history."""
    r_add = r_del = 0
    after = None
    while True:
        data = query(HISTORY_Q, {
            "owner": owner, "name": short,
            "authorId": author_id, "after": after,
        })
        # Any of these can come back null: a repo the token cannot read, a
        # default branch pointing at a tag instead of a commit, or a repo
        # deleted/renamed between fetch_repos() and this walk. That used to
        # raise TypeError here and abort the entire workflow with exit 1.
        # Raise instead of returning 0: a null repo must be *skipped* (retried
        # next run), not cached as 0 lines, or a later token fix would never
        # correct it until the repo's head commit moves.
        repo_data = (data or {}).get("repository") or {}
        if not repo_data:
            raise RuntimeError(f"repository {owner}/{short} returned null "
                               "(not readable with this token?)")
        branch = repo_data.get("defaultBranchRef") or {}
        if not branch:
            raise RuntimeError(f"repository {owner}/{short} has no default branch")
        target = branch.get("target") or {}
        if "history" not in target:
            raise RuntimeError(f"repository {owner}/{short} default branch "
                               "target is not a commit")
        hist = target.get("history") or {}
        for node in hist.get("nodes") or []:
            r_add += node["additions"]
            r_del += node["deletions"]
        page = hist.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    return r_add, r_del


def fetch_loc(repos: list[dict], author_id: str) -> tuple[int, int]:
    """Total additions/deletions authored across all repos, memoised by head OID."""
    CACHE.mkdir(exist_ok=True)
    cache = json.loads(LOC_CACHE.read_text(encoding="utf-8")) if LOC_CACHE.exists() else {}
    added = deleted = 0
    walked = reused = skipped = 0
    skipped_names: list[str] = []

    for repo in repos:
        name = repo["nameWithOwner"]
        target = (repo.get("defaultBranchRef") or {}).get("target") or {}
        head = target.get("oid")
        if not head:
            continue  # empty repo, nothing to walk

        hit = cache.get(name)
        if hit and hit.get("oid") == head:
            added += hit["added"]
            deleted += hit["deleted"]
            reused += 1
            continue

        owner, short = name.split("/", 1)
        try:
            r_add, r_del = _walk_repo(owner, short, author_id)
        except Exception as exc:  # noqa: BLE001 -- one bad repo must not kill the run
            # Skip it and keep going: a partial LOC total still lets the card
            # build, and the next run retries this repo (no cache entry saved).
            print(f"  WARNING skipping {name}: {exc}", file=sys.stderr)
            skipped += 1
            skipped_names.append(name)
            continue

        cache[name] = {"oid": head, "added": r_add, "deleted": r_del}
        _save_loc_cache(cache)
        added += r_add
        deleted += r_del
        walked += 1
        print(f"  walked {name}: +{r_add:,} -{r_del:,}")

    print(f"  loc cache: {walked} walked, {reused} reused, {skipped} skipped"
          + (f" ({', '.join(skipped_names)})" if skipped_names else ""))
    return added, deleted


def fetch_calendar() -> dict:
    """Weeks as seven weekday slots (0 = Sunday), None where a day doesn't exist yet."""
    cal = query(CALENDAR_Q, {"login": LOGIN})["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = []
    for week in cal["weeks"]:
        slots: list[dict | None] = [None] * 7
        for day in week["contributionDays"]:
            slots[day["weekday"]] = {
                "date": day["date"],
                "count": day["contributionCount"],
                "level": LEVELS[day["contributionLevel"]],
            }
        weeks.append(slots)
    return {"total": cal["totalContributions"], "weeks": weeks}


def main() -> None:
    if not TOKEN:
        raise SystemExit("GITHUB_TOKEN is not set")

    print(f"fetching stats for {LOGIN}")
    meta, repos = fetch_repos()
    print(f"  repos: {meta['repos']} | followers: {meta['followers']}")

    stars = sum(r["stargazerCount"] for r in repos)
    contrib = query(CONTRIB_Q, {"login": LOGIN})["user"]["repositoriesContributedTo"]["totalCount"]
    commits = fetch_commit_total(meta["createdAt"])
    added, deleted = fetch_loc(repos, meta["id"])

    stats = {
        "repos": meta["repos"],
        "contrib": contrib,
        "stars": stars,
        "commits": commits,
        "followers": meta["followers"],
        "loc": added - deleted,
        "added": added,
        "deleted": deleted,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    CACHE.mkdir(exist_ok=True)
    STATS_OUT.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))

    calendar = fetch_calendar()
    CALENDAR_OUT.write_text(json.dumps(calendar, indent=1), encoding="utf-8")
    print(f"  calendar: {len(calendar['weeks'])} weeks, {calendar['total']} contributions")


if __name__ == "__main__":
    main()
