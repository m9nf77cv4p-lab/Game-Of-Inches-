#!/usr/bin/env python3
"""Capture Sleeper's player-news feed for every rostered Game of Inches player."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = ROOT / "data" / "game-of-inches-master.json"
DEFAULT_OUTPUT = ROOT / "data" / "sleeper-news.json"
DEFAULT_REPORT = ROOT / "reports" / "sleeper-news.md"
GRAPHQL_URL = "https://api.sleeper.app/graphql"
USER_AGENT = "Game-Of-Inches-Sync/1.1"


class NewsSyncError(RuntimeError):
    pass


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def rostered_player_ids(master: dict[str, Any]) -> list[str]:
    ids = {
        str(player_id)
        for team in master.get("teams", [])
        for player_id in team.get("player_ids", [])
        if player_id
    }
    return sorted(ids)


def news_query(player_ids: list[str], limit: int) -> tuple[str, dict[str, str]]:
    aliases: dict[str, str] = {}
    fields: list[str] = []
    for index, player_id in enumerate(player_ids):
        alias = f"p{index}"
        aliases[alias] = player_id
        encoded_id = json.dumps(player_id)
        fields.append(
            f'{alias}: get_player_news(sport: "nfl", player_id: {encoded_id}, limit: {limit}) '
            "{ metadata player_id published source source_key sport }"
        )
    return "query GameOfInchesPlayerNews { " + " ".join(fields) + " }", aliases


def fetch_news_batch(player_ids: list[str], limit: int = 3) -> list[dict[str, Any]]:
    query, aliases = news_query(player_ids, limit)
    body = json.dumps({
        "operationName": "GameOfInchesPlayerNews",
        "variables": {},
        "query": query,
    }).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Sleeper-GraphQL-Op": "get_player_news",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise NewsSyncError(f"Sleeper news request failed: {exc}") from exc
    if payload.get("errors"):
        raise NewsSyncError(f"Sleeper news GraphQL error: {payload['errors']}")
    data = payload.get("data") or {}
    items: list[dict[str, Any]] = []
    for alias, requested_player_id in aliases.items():
        for item in data.get(alias) or []:
            if isinstance(item, dict):
                item.setdefault("player_id", requested_player_id)
                items.append(item)
    return items


def story_key(item: dict[str, Any]) -> str:
    return "|".join(str(item.get(key) or "") for key in (
        "sport", "player_id", "source", "source_key", "published"
    ))


def normalize_story(item: dict[str, Any], players: dict[str, Any]) -> dict[str, Any]:
    player_id = str(item.get("player_id") or "")
    player = players.get(player_id) or {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "story_id": story_key(item),
        "player_id": player_id,
        "player_name": player.get("full_name") or player_id,
        "position": player.get("position"),
        "nfl_team": player.get("nfl_team"),
        "published": item.get("published"),
        "source": item.get("source"),
        "source_key": item.get("source_key"),
        "title": metadata.get("title"),
        "description": metadata.get("description"),
        "analysis": metadata.get("analysis"),
        "url": metadata.get("url"),
        "metadata": metadata,
    }


def published_seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number / 1000 if number > 10_000_000_000 else number


def iso_time(value: Any) -> str:
    seconds = published_seconds(value)
    if not seconds:
        return "Unknown time"
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def render_report(stories: list[dict[str, Any]], new_ids: set[str]) -> str:
    lines = [
        "# Sleeper Player News — Game of Inches",
        "",
        "Undocumented Sleeper GraphQL feed for players rostered in this league.",
        "",
        f"- Stored stories: **{len(stories)}**",
        f"- Newly captured: **{len(new_ids)}**",
        "",
        "## Latest news",
        "",
    ]
    for story in stories[:100]:
        marker = "NEW — " if story["story_id"] in new_ids else ""
        name = story.get("player_name") or story.get("player_id")
        title = story.get("title") or story.get("description") or "Player update"
        lines.append(f"- **{marker}{name}** — {title} ({iso_time(story.get('published'))})")
        if story.get("analysis"):
            lines.append(f"  - {story['analysis']}")
        if story.get("url"):
            lines.append(f"  - Source: {story['url']}")
    if not stories:
        lines.append("- No player-news stories returned.")
    return "\n".join(lines)


def merge_stories(
    old: dict[str, Any] | None,
    fetched: list[dict[str, Any]],
    players: dict[str, Any],
    retention_days: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    existing = {
        str(item.get("story_id")): item
        for item in (old or {}).get("stories", [])
        if isinstance(item, dict) and item.get("story_id")
    }
    old_ids = set(existing)
    for item in fetched:
        story = normalize_story(item, players)
        existing[story["story_id"]] = story
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()
    stories = [
        story for story in existing.values()
        if published_seconds(story.get("published")) >= cutoff
    ]
    stories.sort(key=lambda item: published_seconds(item.get("published")), reverse=True)
    return stories, {story["story_id"] for story in stories} - old_ids


def sync(master_path: Path, output_path: Path, report_path: Path) -> int:
    master = read_json(master_path)
    if not isinstance(master, dict):
        raise NewsSyncError(f"Missing or invalid master snapshot: {master_path}")
    player_ids = rostered_player_ids(master)
    fetched: list[dict[str, Any]] = []
    for batch in chunks(player_ids, 40):
        fetched.extend(fetch_news_batch(batch))
    old = read_json(output_path)
    stories, new_ids = merge_stories(old, fetched, master.get("players") or {}, 30)
    payload = {
        "schema_version": 1,
        "source": {
            "provider": "Sleeper",
            "endpoint": GRAPHQL_URL,
            "operation": "get_player_news",
            "documented": False,
        },
        "league_id": (master.get("source") or {}).get("league_id"),
        "monitored_player_count": len(player_ids),
        "last_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "new_story_ids": sorted(new_ids),
        "stories": stories,
    }
    # Avoid commits on every check: last_checked_at changes only when the feed changes.
    comparable_old = dict(old or {})
    comparable_new = dict(payload)
    comparable_old.pop("last_checked_at", None)
    comparable_new.pop("last_checked_at", None)
    comparable_old["new_story_ids"] = []
    comparable_new["new_story_ids"] = []
    if comparable_old == comparable_new:
        print(f"Sleeper news unchanged ({len(stories)} stored stories)")
        return 0
    write_json(output_path, payload)
    write_text(report_path, render_report(stories, new_ids))
    print(f"Sleeper news refreshed ({len(new_ids)} new; {len(stories)} stored)")
    return len(new_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        sync(args.master, args.output, args.report)
    except (KeyError, TypeError, ValueError, NewsSyncError) as exc:
        # This is intentionally nonfatal because the feed is undocumented.
        print(f"warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
