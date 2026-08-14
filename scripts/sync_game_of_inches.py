#!/usr/bin/env python3
"""Build the authoritative Game of Inches dynasty snapshot from Sleeper."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = "https://api.sleeper.app/v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "game-of-inches.json"
DEFAULT_OUTPUT = ROOT / "data" / "game-of-inches-master.json"
RAW_DIR = ROOT / "data" / "raw"
PREVIOUS_SNAPSHOT = ROOT / "data" / "history" / "previous.json"
SNAPSHOT_REPORT = ROOT / "reports" / "league-snapshot.md"
USER_REPORT = ROOT / "reports" / "tjs2025-team-report.md"


class SyncError(RuntimeError):
    pass


def fetch_json(path: str) -> Any:
    request = urllib.request.Request(
        f"{API_ROOT}{path}", headers={"User-Agent": "Game-Of-Inches-Sync/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status != 200:
                raise SyncError(f"Sleeper returned HTTP {response.status} for {path}")
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SyncError(f"Unable to fetch valid Sleeper data from {path}: {exc}") from exc


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


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def player_record(player_id: str, players: dict[str, Any]) -> dict[str, Any]:
    player = players.get(player_id) or {}
    return {
        "player_id": player_id,
        "full_name": clean_name(player.get("full_name")),
        "first_name": clean_name(player.get("first_name")),
        "last_name": clean_name(player.get("last_name")),
        "position": player.get("position"),
        "fantasy_positions": player.get("fantasy_positions") or [],
        "nfl_team": player.get("team"),
        "age": player.get("age"),
        "years_exp": player.get("years_exp"),
        "status": player.get("status"),
        "injury_status": player.get("injury_status"),
    }


def fetch_history(start_league: dict[str, Any], depth: int) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    current = start_league
    seen = {str(current.get("league_id"))}
    for _ in range(max(depth, 0)):
        previous_id = str(current.get("previous_league_id") or "")
        if not previous_id or previous_id == "0" or previous_id in seen:
            break
        previous = fetch_json(f"/league/{previous_id}")
        history.append({
            "league_id": str(previous.get("league_id")),
            "name": previous.get("name"),
            "season": previous.get("season"),
            "status": previous.get("status"),
            "draft_id": previous.get("draft_id"),
            "previous_league_id": previous.get("previous_league_id"),
        })
        seen.add(previous_id)
        current = previous
    return history


def build_snapshot(config: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    league = raw["league"]
    league_id = str(config["league_id"])
    if str(league.get("league_id")) != league_id:
        raise SyncError("Sleeper response did not match the configured Game of Inches league ID")
    if int(league.get("settings", {}).get("type", -1)) != int(config["expected_league_type"]):
        raise SyncError("Sleeper league is not configured as the expected dynasty league type")

    users_by_id = {str(user["user_id"]): user for user in raw["users"]}
    players = raw["players"]
    player_ids: set[str] = set()
    teams: list[dict[str, Any]] = []
    for roster in sorted(raw["rosters"], key=lambda item: int(item["roster_id"])):
        owner_id = str(roster.get("owner_id") or "")
        user = users_by_id.get(owner_id, {})
        manager = clean_name(user.get("display_name")) or clean_name(user.get("username"))
        team_name = clean_name((user.get("metadata") or {}).get("team_name")) or manager
        roster_players = [str(value) for value in (roster.get("players") or [])]
        starters = [str(value) for value in (roster.get("starters") or []) if value != "0"]
        reserve = [str(value) for value in (roster.get("reserve") or [])]
        taxi = [str(value) for value in (roster.get("taxi") or [])]
        player_ids.update(roster_players)
        teams.append({
            "roster_id": int(roster["roster_id"]),
            "owner_user_id": owner_id or None,
            "manager": manager,
            "team_name": team_name,
            "settings": roster.get("settings") or {},
            "metadata": roster.get("metadata") or {},
            "player_ids": roster_players,
            "starter_ids": starters,
            "reserve_ids": reserve,
            "taxi_ids": taxi,
        })

    drafts: list[dict[str, Any]] = []
    for draft in raw["drafts"]:
        draft_id = str(draft["draft_id"])
        picks = raw["draft_picks"].get(draft_id, [])
        player_ids.update(str(pick["player_id"]) for pick in picks if pick.get("player_id"))
        drafts.append({
            "draft_id": draft_id,
            "season": draft.get("season"),
            "status": draft.get("status"),
            "type": draft.get("type"),
            "settings": draft.get("settings") or {},
            "metadata": draft.get("metadata") or {},
            "draft_order": draft.get("draft_order"),
            "slot_to_roster_id": draft.get("slot_to_roster_id"),
            "picks": picks,
        })
    drafts.sort(key=lambda item: (str(item.get("season") or ""), item["draft_id"]))

    relevant_players = {
        player_id: player_record(player_id, players) for player_id in sorted(player_ids)
    }
    return {
        "schema_version": 1,
        "source": {
            "provider": "Sleeper",
            "authoritative": True,
            "league_id": league_id,
        },
        "league": {
            "name": league.get("name"),
            "season": league.get("season"),
            "status": league.get("status"),
            "season_type": league.get("season_type"),
            "settings": league.get("settings") or {},
            "scoring_settings": league.get("scoring_settings") or {},
            "roster_positions": league.get("roster_positions") or [],
            "total_rosters": league.get("total_rosters"),
        },
        "teams": teams,
        "players": relevant_players,
        "drafts": drafts,
        "traded_picks": raw["traded_picks"],
        "transactions_by_round": raw["transactions"],
        "matchups_by_week": raw["matchups"],
        "league_history": raw["history"],
    }


def team_report(snapshot: dict[str, Any], configured_name: str) -> str:
    target = configured_name.casefold()
    team = next(
        (item for item in snapshot["teams"] if (item.get("team_name") or "").casefold() == target
         or (item.get("manager") or "").casefold() == target),
        None,
    )
    lines = ["# TJS2025 Dynasty Team Report", ""]
    if not team:
        return "\n".join(lines + ["The configured user team was not found in the current Sleeper users."])
    players = snapshot["players"]
    lines.extend([
        f"- Team: **{team.get('team_name') or configured_name}**",
        f"- Manager: **{team.get('manager') or 'Unknown'}**",
        f"- Roster ID: **{team['roster_id']}**",
        f"- Players: **{len(team['player_ids'])}**",
        f"- Taxi: **{len(team['taxi_ids'])}**",
        f"- Reserve/IR: **{len(team['reserve_ids'])}**",
        "",
        "## Current roster",
        "",
    ])
    roster = [players[player_id] for player_id in team["player_ids"] if player_id in players]
    roster.sort(key=lambda item: (item.get("position") or "ZZ", item.get("full_name") or item["player_id"]))
    for player in roster:
        designation = []
        if player["player_id"] in team["taxi_ids"]:
            designation.append("taxi")
        if player["player_id"] in team["reserve_ids"]:
            designation.append("reserve/IR")
        suffix = f" — {', '.join(designation)}" if designation else ""
        name = player.get("full_name") or player["player_id"]
        lines.append(f"- {name} — {player.get('position') or '?'} — {player.get('nfl_team') or 'FA'}{suffix}")
    owned = [pick for pick in snapshot["traded_picks"] if int(pick.get("owner_id", -1)) == team["roster_id"]]
    lines.extend(["", "## Acquired future picks", ""])
    if owned:
        for pick in sorted(owned, key=lambda item: (str(item.get("season")), int(item.get("round", 0)))):
            lines.append(f"- {pick.get('season')} Round {pick.get('round')} (original roster {pick.get('roster_id')})")
    else:
        lines.append("- No acquired picks are currently listed by Sleeper.")
    return "\n".join(lines)


def league_report(snapshot: dict[str, Any]) -> str:
    league = snapshot["league"]
    lines = [
        "# Game of Inches League Snapshot",
        "",
        f"- Season: **{league.get('season')}**",
        f"- Status: **{league.get('status')}**",
        f"- Teams: **{len(snapshot['teams'])}**",
        f"- Traded-pick records: **{len(snapshot['traded_picks'])}**",
        f"- Drafts attached to current league: **{len(snapshot['drafts'])}**",
        "",
        "## Teams",
        "",
    ]
    for team in snapshot["teams"]:
        lines.append(
            f"- Roster {team['roster_id']}: **{team.get('team_name') or 'Unnamed'}** "
            f"({team.get('manager') or 'Unknown'}) — {len(team['player_ids'])} players"
        )
    return "\n".join(lines)


def sync(config_path: Path, output_path: Path, check_only: bool = False) -> dict[str, Any]:
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise SyncError(f"Invalid configuration: {config_path}")
    league_id = str(config["league_id"])
    base_paths = {
        "league": f"/league/{league_id}",
        "users": f"/league/{league_id}/users",
        "rosters": f"/league/{league_id}/rosters",
        "drafts": f"/league/{league_id}/drafts",
        "traded_picks": f"/league/{league_id}/traded_picks",
        "players": "/players/nfl",
    }
    with ThreadPoolExecutor(max_workers=len(base_paths)) as pool:
        futures = {name: pool.submit(fetch_json, path) for name, path in base_paths.items()}
        raw = {name: future.result() for name, future in futures.items()}
    raw["history"] = fetch_history(raw["league"], int(config.get("history_depth", 10)))

    draft_ids = [str(draft["draft_id"]) for draft in raw["drafts"]]
    matchup_weeks = range(1, int(config.get("matchup_weeks", 18)) + 1)
    transaction_rounds = range(1, int(config.get("transaction_rounds", 18)) + 1)
    requests: dict[str, str] = {}
    requests.update({f"draft:{draft_id}": f"/draft/{draft_id}/picks" for draft_id in draft_ids})
    requests.update({f"matchup:{week}": f"/league/{league_id}/matchups/{week}" for week in matchup_weeks})
    requests.update({f"transaction:{week}": f"/league/{league_id}/transactions/{week}" for week in transaction_rounds})
    with ThreadPoolExecutor(max_workers=min(max(len(requests), 1), 12)) as pool:
        futures = {name: pool.submit(fetch_json, path) for name, path in requests.items()}
        expanded = {name: future.result() for name, future in futures.items()}
    raw["draft_picks"] = {draft_id: expanded[f"draft:{draft_id}"] for draft_id in draft_ids}
    raw["matchups"] = {str(week): expanded[f"matchup:{week}"] for week in matchup_weeks}
    raw["transactions"] = {str(week): expanded[f"transaction:{week}"] for week in transaction_rounds}

    snapshot = build_snapshot(config, raw)
    fingerprint = stable_hash(snapshot)
    old = read_json(output_path)
    old_fingerprint = ((old or {}).get("sync") or {}).get("source_fingerprint")
    if old_fingerprint == fingerprint:
        print(f"Game of Inches data unchanged ({fingerprint[:12]})")
        return old
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshot["sync"] = {"last_changed_at": now, "source_fingerprint": fingerprint}
    if check_only:
        print(f"Game of Inches data valid; changes detected ({fingerprint[:12]})")
        return snapshot
    if old:
        write_json(PREVIOUS_SNAPSHOT, old)
    write_json(output_path, snapshot)
    for name in ("league", "users", "rosters", "drafts", "traded_picks", "history"):
        write_json(RAW_DIR / f"{name}.json", raw[name])
    write_text(SNAPSHOT_REPORT, league_report(snapshot))
    write_text(USER_REPORT, team_report(snapshot, str(config["user_team_name"])))
    print(f"Game of Inches data refreshed ({fingerprint[:12]})")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        sync(args.config, args.output, args.check)
    except (KeyError, TypeError, ValueError, SyncError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

