# Game of Inches Sync

Game of Inches Sync is the persistent Sleeper data bridge for the user's dynasty league.

## Source of truth

- Sleeper league: `1312075409780649984`
- User team: `TJS2025`
- Format: dynasty, Superflex

The sync discovers draft IDs and prior-season league IDs from Sleeper instead of hard-coding them. This is important for a dynasty league because Sleeper rolls leagues forward and draft-pick ownership can span several seasons.

## Data files

- `data/game-of-inches-master.json` is the project-facing master record.
- `data/raw/` preserves the current Sleeper responses used to build it.
- `data/history/previous.json` preserves the prior master snapshot when source data changes.
- `reports/league-snapshot.md` summarizes the league.
- `reports/tjs2025-team-report.md` summarizes the user's roster and draft capital.
- `data/sleeper-news.json` retains 30 days of Sleeper player-news records for every player rostered in the league.
- `reports/sleeper-news.md` lists the latest captured stories with Sleeper publication timestamps and source links.

The master record includes league settings, manager/team mapping, complete rosters, starters, reserve and taxi assignments, current drafts and picks, traded picks, transactions, matchups, player metadata, and the prior-league chain.

## Refresh behavior

The GitHub Action runs every five minutes and can also be started manually. It runs unit tests first, validates the Sleeper league identity and dynasty format, then writes only when source data changes. It also queries Sleeper's undocumented `get_player_news` GraphQL operation in batches for every rostered player. News capture is failure-tolerant so an upstream feed change cannot interrupt the authoritative league sync.

Run locally with Python 3.11 or newer:

```bash
python -m unittest discover -s tests -v
python scripts/sync_game_of_inches.py
```

