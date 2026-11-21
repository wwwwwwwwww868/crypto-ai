"""Fake ESPN data for `python app.py --demo`. Players are made up."""

from __future__ import annotations

import datetime as dt
import random

TODAY_PERIOD = 33
SEASON = 2027

ABBREVS = ["ATL", "BOS", "NOP", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU",
           "IND", "LAC", "LAL", "MIA", "MIL", "MIN", "BKN", "NYK", "ORL", "PHI",
           "PHX", "POR", "SAC", "SAS", "OKC", "UTA", "WAS", "TOR", "MEM", "CHA"]

PG, SG, SF, PF, C, G, F, UT, BE, IR = 0, 1, 2, 3, 4, 5, 6, 11, 12, 13

# name, positions, pro team id, avg fantasy points, injury, current slot
PLAYERS = [
    ("Marcus Vale",    [PG, SG],  5, 48.2, "ACTIVE", PG),
    ("Dorian Hayes",   [SG],      9, 41.7, "ACTIVE", SG),
    ("Tyrese Okafor",  [SF, PF], 13, 44.9, "DAY_TO_DAY", SF),
    ("Jalen Brooks",   [PF, C],  17, 39.3, "ACTIVE", PF),
    ("Viktor Lindqvist", [C],     7, 52.6, "ACTIVE", C),
    ("Andre Whitfield", [PG],    21, 33.4, "ACTIVE", G),
    ("Keon Mbeki",     [SF],      2, 30.1, "ACTIVE", F),
    ("Rafael Duarte",  [SG, SF], 25, 36.8, "OUT", UT),
    ("Corey Lamb",     [PF],     11, 28.5, "ACTIVE", UT),
    ("Isaiah Ford",    [C],      15, 31.9, "ACTIVE", UT),
    ("Trent Calloway", [PG, SG], 19, 26.2, "ACTIVE", BE),
    ("Niko Petrovic",  [SF, PF],  3, 34.7, "ACTIVE", BE),
    ("Deshawn Price",  [C],      29, 22.4, "QUESTIONABLE", BE),
]

FLEX = {PG: [G], SG: [G], SF: [F], PF: [F], C: []}


def _eligible(positions):
    slots = set(positions)
    for p in positions:
        slots.update(FLEX[p])
    return sorted(slots | {UT, BE, IR})


def _schedule() -> dict:
    rng = random.Random(7)
    base = dt.datetime.combine(dt.date.today(), dt.time(19, 0))
    games = {i + 1: {} for i in range(30)}
    for off in range(0, 8):
        period = TODAY_PERIOD + off
        teams = list(range(1, 31))
        rng.shuffle(teams)
        teams = teams[: rng.choice([12, 16, 20, 24])]
        for a, b in zip(teams[::2], teams[1::2]):
            when = base + dt.timedelta(days=off, minutes=30 * rng.randint(0, 6))
            g = [{"homeProTeamId": a, "awayProTeamId": b, "date": int(when.timestamp() * 1000)}]
            games[a][str(period)] = g
            games[b][str(period)] = g
    return {"settings": {"proTeams": [
        {"id": i, "abbrev": ABBREVS[i - 1], "proGamesByScoringPeriod": games[i]}
        for i in range(1, 31)]}}


class DemoClient:
    def __init__(self):
        self.cfg = {"team_id": 1, "season": SEASON, "league_id": 0}
        self.lineups: dict[int, dict[int, int]] = {}   # period -> player id -> slot

    def _slots_for(self, period):
        if period not in self.lineups:
            self.lineups[period] = {i + 1: p[5] for i, p in enumerate(PLAYERS)}
        return self.lineups[period]

    def league(self, scoring_period=None):
        period = scoring_period or TODAY_PERIOD
        slots = self._slots_for(period)
        entries = [{
            "lineupSlotId": slots[i + 1],
            "playerPoolEntry": {"player": {
                "id": i + 1, "fullName": name, "proTeamId": team,
                "eligibleSlots": _eligible(pos), "injuryStatus": inj,
                "stats": [{"statSourceId": 0, "id": f"00{SEASON}", "appliedAverage": avg}],
            }},
        } for i, (name, pos, team, avg, inj, _) in enumerate(PLAYERS)]
        return {
            "scoringPeriodId": TODAY_PERIOD,
            "settings": {"rosterSettings": {"lineupSlotCounts": {
                "0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "11": 3, "12": 3, "13": 1}}},
            "teams": [{"id": 1, "name": "Demo Dunkers", "roster": {"entries": entries}}],
        }

    def pro_schedule(self):
        return _schedule()

    def submit_lineup(self, period, moves):
        slots = self._slots_for(period)
        for m in moves:
            slots[m["player_id"]] = m["to"]
        return {}
