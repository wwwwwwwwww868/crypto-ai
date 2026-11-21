#!/usr/bin/env python3
"""
ESPN Fantasy Basketball auto-manager.

What it does, every time it runs:
  * Pulls your roster from ESPN.
  * Works out which of your players have an NBA game that day.
  * Builds the best legal starting lineup (players who are playing, not OUT,
    best first) and benches everyone else.
  * Optionally moves OUT players into an open IR slot (--use-ir).
  * Sends the lineup changes to ESPN (or just prints them with --dry-run).
  * Can do this for several days ahead at once (--days 7).

ESPN has no official public API. This uses the same private endpoints the
ESPN website and app use, authenticated with your browser cookies
(espn_s2 and SWID). ESPN can change these endpoints at any time.

Config comes from environment variables (or a .env file next to this script):
  ESPN_LEAGUE_ID   your league id (from the league URL: leagueId=XXXX)
  ESPN_TEAM_ID     your team id   (from your team URL:   teamId=X)
  ESPN_S2          the espn_s2 cookie value
  ESPN_SWID        the SWID cookie value, including the {braces}
  ESPN_SEASON      optional, e.g. 2027 for the 2026-27 season
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

READ_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
WRITE_BASE = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"

# ESPN fantasy basketball lineup slot ids.
SLOT_NAMES = {
    0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C",
    5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C",
    11: "UTIL", 12: "BENCH", 13: "IR",
}
BENCH = 12
IR = 13
NON_STARTING_SLOTS = {BENCH, IR}

# Injury statuses that mean "do not start this player".
DO_NOT_START = {"OUT", "SUSPENSION", "INJURY_RESERVE"}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def load_dotenv(path: Path) -> None:
    """Minimal .env loader so there's no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_season(today: dt.date) -> int:
    # ESPN names an NBA season by the year it ends (2026-27 season -> 2027).
    return today.year + 1 if today.month >= 8 else today.year


def get_config() -> dict:
    load_dotenv(Path(__file__).with_name(".env"))
    missing = [k for k in ("ESPN_LEAGUE_ID", "ESPN_TEAM_ID", "ESPN_S2", "ESPN_SWID")
               if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing config: {', '.join(missing)}. See README.md / .env.example.")
    return {
        "league_id": int(os.environ["ESPN_LEAGUE_ID"]),
        "team_id": int(os.environ["ESPN_TEAM_ID"]),
        "espn_s2": os.environ["ESPN_S2"],
        "swid": os.environ["ESPN_SWID"],
        "season": int(os.environ.get("ESPN_SEASON") or default_season(dt.date.today())),
    }


# --------------------------------------------------------------------------- #
# ESPN API
# --------------------------------------------------------------------------- #

class EspnClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.cookies.set("espn_s2", cfg["espn_s2"])
        self.session.cookies.set("SWID", cfg["swid"])
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        self.read_base = READ_BASE.format(season=cfg["season"])
        self.write_base = WRITE_BASE.format(season=cfg["season"])

    def _get(self, url: str, params) -> dict:
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code in (401, 403):
            sys.exit("ESPN rejected the login. Your ESPN_S2 / ESPN_SWID cookies are "
                     "probably wrong or expired. Grab fresh ones from your browser.")
        r.raise_for_status()
        return r.json()

    def league(self, scoring_period: int | None = None) -> dict:
        params = [("view", "mSettings"), ("view", "mRoster"), ("view", "mTeam"),
                  ("view", "mStatus")]
        if scoring_period is not None:
            params.append(("scoringPeriodId", scoring_period))
        url = f"{self.read_base}/segments/0/leagues/{self.cfg['league_id']}"
        return self._get(url, params)

    def pro_schedule(self) -> dict:
        """NBA schedule: which pro teams play in which scoring period (day)."""
        return self._get(self.read_base, [("view", "proTeamSchedules_wl")])

    def submit_lineup(self, scoring_period: int, moves: list[dict]) -> dict:
        payload = {
            "isLeagueManager": False,
            "teamId": self.cfg["team_id"],
            "type": "ROSTER",
            "memberId": self.cfg["swid"],
            "scoringPeriodId": scoring_period,
            "executionType": "EXECUTE",
            "items": [
                {"playerId": m["player_id"], "type": "LINEUP",
                 "fromLineupSlotId": m["from"], "toLineupSlotId": m["to"]}
                for m in moves
            ],
        }
        url = (f"{self.write_base}/segments/0/leagues/"
               f"{self.cfg['league_id']}/transactions/")
        r = self.session.post(url, json=payload, timeout=30)
        if not r.ok:
            raise RuntimeError(f"ESPN refused the lineup change ({r.status_code}): {r.text[:500]}")
        return r.json() if r.text else {}


# --------------------------------------------------------------------------- #
# Data shaping
# --------------------------------------------------------------------------- #

def games_by_period(schedule: dict) -> dict[int, set[int]]:
    """scoring period id -> set of pro team ids that play that day."""
    out: dict[int, set[int]] = {}
    for team in schedule.get("settings", {}).get("proTeams", []):
        for period, games in (team.get("proGamesByScoringPeriod") or {}).items():
            if games:
                out.setdefault(int(period), set()).add(team["id"])
    return out


def player_value(player: dict, season: int) -> float:
    """
    How good a player is, for ranking who starts. Uses, in order:
    this season's average fantasy points, projected average, last season's
    average, then % rostered as a last resort (useful in category leagues).
    """
    stats = player.get("stats") or []

    def avg(source: int, stat_id: str) -> float | None:
        for s in stats:
            if s.get("statSourceId") == source and s.get("id") == stat_id:
                v = s.get("appliedAverage")
                if v:
                    return float(v)
        return None

    for source, stat_id in ((0, f"00{season}"), (1, f"10{season}"), (0, f"00{season - 1}")):
        v = avg(source, stat_id)
        if v is not None:
            return v
    return float((player.get("ownership") or {}).get("percentOwned") or 0) / 10.0


def parse_roster(league: dict, team_id: int, season: int) -> list[dict]:
    team = next((t for t in league.get("teams", []) if t["id"] == team_id), None)
    if team is None:
        sys.exit(f"Team {team_id} not found in league. Check ESPN_TEAM_ID.")
    roster = []
    for entry in team.get("roster", {}).get("entries", []):
        p = entry["playerPoolEntry"]["player"]
        roster.append({
            "id": p["id"],
            "name": p.get("fullName", str(p["id"])),
            "pro_team": p.get("proTeamId"),
            "eligible": set(p.get("eligibleSlots", [])),
            "injury": p.get("injuryStatus") or ("OUT" if p.get("injured") else "ACTIVE"),
            "slot": entry["lineupSlotId"],
            "value": player_value(p, season),
        })
    return roster


def starting_slots(league: dict) -> list[int]:
    """Expand lineupSlotCounts ({"0": 1, "11": 3, ...}) into one entry per slot."""
    counts = league["settings"]["rosterSettings"]["lineupSlotCounts"]
    slots = []
    for slot_id, n in counts.items():
        slot_id = int(slot_id)
        if slot_id in NON_STARTING_SLOTS:
            continue
        slots.extend([slot_id] * int(n))
    return slots


def ir_slot_count(league: dict) -> int:
    return int(league["settings"]["rosterSettings"]["lineupSlotCounts"].get(str(IR), 0))


# --------------------------------------------------------------------------- #
# Lineup optimizer
# --------------------------------------------------------------------------- #

def optimize(roster: list[dict], slots: list[int], playing: set[int],
             use_ir: bool, ir_slots: int) -> dict[int, int]:
    """
    Returns {player_id: target_slot_id}.

    Starting slots are filled with an optimal max-value assignment: players who
    are playing and healthy are considered best-first, and each one is placed
    if any chain of swaps (augmenting path) can make room for them. Because
    slot eligibility forms a transversal matroid, this greedy is optimal.
    """
    target: dict[int, int] = {}

    # Leave current IR players where they are (moving them off IR might need
    # a roster spot we don't have). Optionally send OUT players to empty IR.
    ir_used = sum(1 for p in roster if p["slot"] == IR)
    for p in roster:
        if p["slot"] == IR:
            target[p["id"]] = IR
    if use_ir:
        for p in sorted(roster, key=lambda p: p["value"]):
            if (ir_used < ir_slots and p["id"] not in target
                    and p["injury"] == "OUT" and IR in p["eligible"]):
                target[p["id"]] = IR
                ir_used += 1

    candidates = [p for p in roster
                  if p["id"] not in target
                  and p["pro_team"] in playing
                  and p["injury"] not in DO_NOT_START]
    candidates.sort(key=lambda p: p["value"], reverse=True)

    slot_owner: list[int | None] = [None] * len(slots)   # slot index -> player id
    by_id = {p["id"]: p for p in roster}

    def try_place(pid: int, seen: set[int]) -> bool:
        # Try the player's current slot first so we don't make pointless swaps.
        order = sorted(range(len(slots)), key=lambda i: slots[i] != by_id[pid]["slot"])
        for i in order:
            slot_id = slots[i]
            if slot_id not in by_id[pid]["eligible"] or i in seen:
                continue
            seen.add(i)
            if slot_owner[i] is None or try_place(slot_owner[i], seen):
                slot_owner[i] = pid
                return True
        return False

    for p in candidates:
        try_place(p["id"], set())

    for i, pid in enumerate(slot_owner):
        if pid is not None:
            target[pid] = slots[i]
    for p in roster:
        target.setdefault(p["id"], BENCH)
    return target


def plan_moves(roster: list[dict], target: dict[int, int]) -> list[dict]:
    return [
        {"player_id": p["id"], "name": p["name"], "from": p["slot"], "to": target[p["id"]]}
        for p in roster if p["slot"] != target[p["id"]]
    ]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def slot_name(s: int) -> str:
    return SLOT_NAMES.get(s, f"slot{s}")


def run(args) -> int:
    cfg = get_config()
    client = EspnClient(cfg)

    base = client.league()
    current = base.get("scoringPeriodId") or base.get("status", {}).get("latestScoringPeriod")
    if not current:
        sys.exit("Couldn't figure out today's scoring period from ESPN.")
    schedule = games_by_period(client.pro_schedule())
    slots = starting_slots(base)
    ir_slots = ir_slot_count(base)

    print(f"League {cfg['league_id']} | team {cfg['team_id']} | season {cfg['season']} "
          f"| today = scoring period {current}")
    if args.dry_run:
        print("DRY RUN: nothing will be changed on ESPN.\n")

    failures = 0
    for period in range(current, current + args.days):
        league = base if period == current else client.league(period)
        roster = parse_roster(league, cfg["team_id"], cfg["season"])
        playing = schedule.get(period, set())
        target = optimize(roster, slots, playing, args.use_ir, ir_slots)
        moves = plan_moves(roster, target)

        print(f"=== Scoring period {period} ({len(playing)} NBA teams playing) ===")
        for p in sorted(roster, key=lambda p: (target[p["id"]] in NON_STARTING_SLOTS, -p["value"])):
            tag = []
            if p["pro_team"] not in playing:
                tag.append("no game")
            if p["injury"] not in ("ACTIVE", "NORMAL"):
                tag.append(p["injury"])
            print(f"  {slot_name(target[p['id']]):>6}  {p['name']:<26} {p['value']:6.1f}"
                  f"  {', '.join(tag)}")

        if not moves:
            print("  Lineup already optimal.\n")
            continue
        print("  Changes:")
        for m in moves:
            print(f"    {m['name']}: {slot_name(m['from'])} -> {slot_name(m['to'])}")

        if args.dry_run:
            print()
            continue
        try:
            client.submit_lineup(period, moves)
            print("  Submitted to ESPN.\n")
        except RuntimeError as e:
            failures += 1
            print(f"  FAILED: {e}\n  (Players whose games already started are locked; "
                  f"run earlier in the day.)\n")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-set your ESPN fantasy basketball lineup.")
    ap.add_argument("--days", type=int, default=1,
                    help="how many days to set, starting today (default 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without changing anything")
    ap.add_argument("--use-ir", action="store_true",
                    help="move OUT players into empty IR slots")
    ap.add_argument("--debug", action="store_true",
                    help="dump raw ESPN league JSON to espn_debug.json")
    args = ap.parse_args()
    if args.debug:
        cfg = get_config()
        data = EspnClient(cfg).league()
        Path("espn_debug.json").write_text(json.dumps(data, indent=2))
        print("Wrote espn_debug.json")
        return
    sys.exit(run(args))


if __name__ == "__main__":
    main()
