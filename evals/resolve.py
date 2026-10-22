"""Resolve teams, players, and seasons to one canonical form before comparing.

Gold answers identify teams by `team_id`, players by id, and seasons by start
year. The agent may answer with "Boston Celtics", "BOS", "Kobe Bryant",
"2007-08", or "22007" — the same answer in a different form. Resolution maps those forms to the canonical one
so the comparator judges the answer, not its spelling.

Every rule here is a place a real mistake could hide, so the rules are few:
  - a team label resolves only if it names exactly one franchise
  - a player name resolves only if it names exactly one player
  - a season resolves only from an explicit season shape
Anything else passes through unchanged and is compared as-is.
"""

import re
from dataclasses import dataclass

# 2007-08, 2007-2008, 1999-00
SEASON_RANGE = re.compile(r"^(\d{4})-(\d{2}|\d{4})$")
# 22007: season type digit, then start year (the snapshot's season_id)
SEASON_ID = re.compile(r"^[1-5](\d{4})$")


def _label(value: str) -> str:
    return " ".join(value.lower().split())


def _unique(labels: dict[str, set[str]]) -> dict[str, str]:
    # A label naming more than one entity is left unresolved.
    return {label: next(iter(ids)) for label, ids in labels.items() if len(ids) == 1}


@dataclass(frozen=True)
class Resolver:
    """Team labels → team_id and player names → player id, from the snapshot."""

    teams: dict[str, str]
    players: dict[str, str]

    @classmethod
    def from_snapshot(cls, con) -> "Resolver":
        labels: dict[str, set[str]] = {}

        def add(label, team_id):
            if label and team_id:
                labels.setdefault(_label(str(label)), set()).add(str(team_id))

        for side in ("home", "away"):
            for team_id, abbreviation, name in con.execute(
                f"SELECT DISTINCT team_id_{side}, team_abbreviation_{side}, team_name_{side} "
                "FROM nba.game"
            ).fetchall():
                add(team_id, team_id)
                add(abbreviation, team_id)
                add(name, team_id)

        for team_id, full_name, abbreviation, nickname in con.execute(
            "SELECT id, full_name, abbreviation, nickname FROM nba.team"
        ).fetchall():
            for label in (full_name, abbreviation, nickname):
                add(label, team_id)

        for team_id, city, nickname in con.execute(
            "SELECT team_id, city, nickname FROM nba.team_history"
        ).fetchall():
            add(f"{city} {nickname}", team_id)
            add(nickname, team_id)

        names: dict[str, set[str]] = {}
        for person_id, name in con.execute(
            "SELECT id, full_name FROM nba.player "
            "UNION SELECT person_id, display_first_last FROM nba.common_player_info"
        ).fetchall():
            if person_id and name:
                names.setdefault(_label(str(name)), set()).add(str(person_id))
                names.setdefault(str(person_id), set()).add(str(person_id))

        return cls(teams=_unique(labels), players=_unique(names))

    def team(self, value):
        return _lookup(self.teams, value)

    def player(self, value):
        return _lookup(self.players, value)


def _lookup(table: dict[str, str], value):
    if isinstance(value, str):
        return table.get(_label(value), value)
    if isinstance(value, int) and not isinstance(value, bool):
        return table.get(str(value), value)
    return value


def season(value):
    """Season forms → start year. Plain integers are already start years."""
    if isinstance(value, str):
        text = value.strip()
        match = SEASON_RANGE.match(text) or SEASON_ID.match(text)
        if match:
            return int(match.group(1))
        if text.isdigit() and len(text) == 4:
            return int(text)
    return value


def resolve_rows(rows, columns, resolver: Resolver, teams, seasons, players=()):
    """Resolve the named team, season, and player columns of one result set.

    `columns` are the result's column names; `teams`, `seasons`, and `players`
    name which of them hold which kind.
    """
    def indexes(names):
        return {columns.index(name) for name in names if name in columns}

    team_indexes, season_indexes, player_indexes = indexes(teams), indexes(seasons), indexes(players)
    return [
        tuple(
            resolver.team(cell) if i in team_indexes
            else season(cell) if i in season_indexes
            else resolver.player(cell) if i in player_indexes
            else cell
            for i, cell in enumerate(row)
        )
        for row in rows
    ]


def resolve_any(rows, resolver: Resolver, teams: bool, seasons: bool, players: bool = False):
    """Resolve every cell that looks like a team, a player, or a season.

    For the agent's result, whose column names we do not control. Only runs
    for the kinds the case declares, and only on explicit shapes.
    """
    def one(cell):
        if teams:
            resolved = resolver.team(cell)
            if resolved is not cell:
                return resolved
        if players:
            resolved = resolver.player(cell)
            if resolved is not cell:
                return resolved
        if seasons and isinstance(cell, str):
            return season(cell)
        return cell

    return [tuple(one(cell) for cell in row) for row in rows]


def resolve_pair(gold, agent, contract, resolver: Resolver):
    """Resolve a gold result and an agent result for comparison.

    Gold's team and season columns are named in the contract. The agent's are
    not known, so its cells are resolved by shape — only for the kinds the
    contract declares.
    """
    gold_rows = resolve_rows(
        gold.rows, gold.columns, resolver, contract.teams, contract.seasons, contract.players
    )
    agent_rows = resolve_any(
        agent.rows,
        resolver,
        teams=bool(contract.teams),
        seasons=bool(contract.seasons),
        players=bool(contract.players),
    )
    return gold_rows, agent_rows
