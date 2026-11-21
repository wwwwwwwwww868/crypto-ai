#!/usr/bin/env python3
"""
ESPN Fantasy Basketball Lineup app.

Runs a small web app on your own computer and opens it in your browser:
  * Settings screen for your league and ESPN login
  * See each day's lineup: who's playing, who's hurt, what the bot would change
  * One click to apply a day (or the whole week) to ESPN
  * Auto-pilot: sets your lineup every day at a time you pick, while the app is open

Run:   python app.py           (real ESPN)
       python app.py --demo    (fake team, to try it out without logging in)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import espn_bot as bot

HERE = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)   # True when running as the built app


def _data_dir() -> Path:
    """Where settings are saved. Next to the code when run from source;
    in the normal per-user app-data folder when run as the built app."""
    if not FROZEN:
        return HERE
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / "LineupAutopilot"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA_DIR = _data_dir()
ENV_FILE = DATA_DIR / ".env"
SETTINGS_FILE = DATA_DIR / "app_settings.json"
INDEX_HTML = Path(getattr(sys, "_MEIPASS", HERE)) / "static" / "index.html"

DEFAULT_SETTINGS = {"auto_enabled": False, "auto_time": "10:00", "auto_days": 2, "use_ir": True}
ENV_KEYS = ("ESPN_LEAGUE_ID", "ESPN_TEAM_ID", "ESPN_S2", "ESPN_SWID", "ESPN_SEASON")

log_lock = threading.Lock()
activity: list[dict] = []


def log(msg: str, level: str = "info") -> None:
    with log_lock:
        activity.insert(0, {"time": dt.datetime.now().strftime("%b %d %I:%M %p"),
                            "msg": msg, "level": level})
        del activity[50:]
    print(msg)


# --------------------------------------------------------------------------- #
# Settings storage
# --------------------------------------------------------------------------- #

def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


def save_env(values: dict) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            seen.add(key)
            if values[key]:
                out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen and v]
    ENV_FILE.write_text("\n".join(out) + "\n")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def current_config() -> dict | None:
    """Config dict for EspnClient, or None if something required is missing."""
    try:
        return {
            "league_id": int(os.environ["ESPN_LEAGUE_ID"]),
            "team_id": int(os.environ["ESPN_TEAM_ID"]),
            "espn_s2": os.environ["ESPN_S2"],
            "swid": os.environ["ESPN_SWID"],
            "season": int(os.environ.get("ESPN_SEASON") or bot.default_season(dt.date.today())),
        }
    except (KeyError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Building the lineup view
# --------------------------------------------------------------------------- #

class AppError(Exception):
    pass


class Engine:
    """Wraps the ESPN client and caches the slow, rarely-changing data."""

    def __init__(self, demo: bool):
        self.demo = demo
        self.lock = threading.Lock()
        self._client = None
        self._client_key = None
        self._schedule = None
        self._schedule_at = 0.0

    def client(self):
        if self.demo:
            if self._client is None:
                from demo_data import DemoClient
                self._client = DemoClient()
            return self._client
        cfg = current_config()
        if not cfg:
            raise AppError("Finish setting up: add your league, team and ESPN login in Settings.")
        key = tuple(sorted(cfg.items()))
        if key != self._client_key:
            self._client, self._client_key, self._schedule = bot.EspnClient(cfg), key, None
        return self._client

    def _call(self, fn, *a):
        try:
            return fn(*a)
        except SystemExit as e:       # espn_bot exits on bad login; turn into a message
            raise AppError(str(e))
        except bot.requests.RequestException as e:
            raise AppError(f"Couldn't reach ESPN: {e}")

    def schedule(self):
        if self._schedule is None or time.time() - self._schedule_at > 3600:
            self._schedule = self._call(self.client().pro_schedule)
            self._schedule_at = time.time()
        return self._schedule

    def plan(self, offset: int, use_ir: bool) -> dict:
        with self.lock:
            client = self.client()
            base = self._call(client.league)
            today = base.get("scoringPeriodId") or base.get("status", {}).get("latestScoringPeriod")
            if not today:
                raise AppError("ESPN didn't say what day of the season it is. Is the season over?")
            period = today + offset
            league = base if offset == 0 else self._call(client.league, period)
            season = client.cfg["season"]
            team_id = client.cfg["team_id"]
            raw_sched = self.schedule()

        games = bot.games_by_period(raw_sched)
        playing = games.get(period, set())
        pro = {t["id"]: t for t in raw_sched.get("settings", {}).get("proTeams", [])}
        matchups = _matchups(pro, period)

        try:
            roster = bot.parse_roster(league, team_id, season)
        except SystemExit as e:
            raise AppError(str(e))
        slots = bot.starting_slots(base)
        target = bot.optimize(roster, slots, playing, use_ir, bot.ir_slot_count(base))
        moves = bot.plan_moves(roster, target)

        team = next(t for t in league["teams"] if t["id"] == team_id)
        team_name = team.get("name") or f"{team.get('location', '')} {team.get('nickname', '')}".strip()

        slot_order = {s: i for i, s in enumerate(sorted(set(slots)))}
        rows = []
        for p in roster:
            m = matchups.get(p["pro_team"])
            rows.append({
                "id": p["id"], "name": p["name"], "positions": p["positions"],
                "team": pro.get(p["pro_team"], {}).get("abbrev", "FA"),
                "game": m, "injury": p["injury"], "value": round(p["value"], 1),
                "current": bot.slot_name(p["slot"]), "suggested": bot.slot_name(target[p["id"]]),
                "starting": target[p["id"]] not in bot.NON_STARTING_SLOTS,
                "moved": p["slot"] != target[p["id"]],
                "_sort": (target[p["id"]] in bot.NON_STARTING_SLOTS,
                          target[p["id"]] == bot.IR,
                          slot_order.get(target[p["id"]], 99), -p["value"]),
            })
        rows.sort(key=lambda r: r.pop("_sort"))

        date = _period_date(pro, period) or (dt.date.today() + dt.timedelta(days=offset))
        return {
            "team_name": team_name, "period": period, "offset": offset,
            "date": date.isoformat(), "teams_playing": len(playing),
            "starting_slots": len(slots),
            "players": rows, "moves": [{"name": m["name"], "from": bot.slot_name(m["from"]),
                                        "to": bot.slot_name(m["to"])} for m in moves],
            "_moves_raw": moves, "demo": self.demo,
        }

    def apply(self, offset: int, use_ir: bool) -> dict:
        plan = self.plan(offset, use_ir)
        moves = plan.pop("_moves_raw")
        label = dt.date.fromisoformat(plan["date"]).strftime("%a %b %d")
        if not moves:
            log(f"{label}: lineup already optimal, nothing to change.")
            return {"ok": True, "changed": 0, "date": plan["date"]}
        try:
            with self.lock:
                self.client().submit_lineup(plan["period"], moves)
        except RuntimeError as e:
            log(f"{label}: ESPN refused the change. Players whose games started are locked. ({e})", "error")
            raise AppError(f"ESPN refused the change for {label}. If games already started those "
                           f"players are locked.")
        log(f"{label}: set lineup ({len(moves)} change{'s' if len(moves) != 1 else ''}).", "success")
        return {"ok": True, "changed": len(moves), "date": plan["date"]}


def _matchups(pro: dict, period: int) -> dict:
    out = {}
    for tid, t in pro.items():
        g = ((t.get("proGamesByScoringPeriod") or {}).get(str(period)) or [None])[0]
        if not g:
            continue
        home = g.get("homeProTeamId") == tid
        opp = g.get("awayProTeamId") if home else g.get("homeProTeamId")
        when = ""
        if g.get("date"):
            when = dt.datetime.fromtimestamp(g["date"] / 1000).strftime("%-I:%M %p")
        out[tid] = {"opp": pro.get(opp, {}).get("abbrev", "?"), "home": home, "time": when}
    return out


def _period_date(pro: dict, period: int) -> dt.date | None:
    for t in pro.values():
        for g in (t.get("proGamesByScoringPeriod") or {}).get(str(period)) or []:
            if g.get("date"):
                return dt.datetime.fromtimestamp(g["date"] / 1000).date()
    return None


# --------------------------------------------------------------------------- #
# Auto-pilot
# --------------------------------------------------------------------------- #

def autopilot(engine: Engine) -> None:
    last_run_day = None
    while True:
        time.sleep(20)
        s = load_settings()
        if not s["auto_enabled"]:
            continue
        now = dt.datetime.now()
        if now.strftime("%H:%M") < s["auto_time"] or last_run_day == now.date():
            continue
        last_run_day = now.date()
        log("Auto-pilot: setting lineups...")
        for offset in range(int(s["auto_days"])):
            try:
                engine.apply(offset, s["use_ir"])
            except AppError as e:
                log(f"Auto-pilot: {e}", "error")


# --------------------------------------------------------------------------- #
# Web server
# --------------------------------------------------------------------------- #

def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _guard(self, fn):
            try:
                self._send(200, fn())
            except AppError as e:
                self._send(400, {"error": str(e)})
            except Exception as e:  # keep the app alive on surprises
                self._send(500, {"error": f"Something went wrong: {e}"})

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/config":
                return self._guard(lambda: {
                    "demo": engine.demo,
                    "configured": engine.demo or current_config() is not None,
                    "league_id": os.environ.get("ESPN_LEAGUE_ID", ""),
                    "team_id": os.environ.get("ESPN_TEAM_ID", ""),
                    "season": os.environ.get("ESPN_SEASON", ""),
                    "has_s2": bool(os.environ.get("ESPN_S2")),
                    "has_swid": bool(os.environ.get("ESPN_SWID")),
                    "settings": load_settings(),
                })
            if url.path == "/api/plan":
                offset = int(q.get("day", ["0"])[0])
                use_ir = q.get("use_ir", ["1"])[0] == "1"
                return self._guard(lambda: {k: v for k, v in engine.plan(offset, use_ir).items()
                                            if not k.startswith("_")})
            if url.path == "/api/activity":
                with log_lock:
                    return self._send(200, list(activity))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            # Only accept requests from this app's own page.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._send(403, {"error": "forbidden"})
            path = urlparse(self.path).path
            if path == "/api/config":
                def save():
                    b = self._body()
                    env = {}
                    for key, field in (("ESPN_LEAGUE_ID", "league_id"), ("ESPN_TEAM_ID", "team_id"),
                                       ("ESPN_SEASON", "season")):
                        if field in b:
                            v = str(b[field]).strip()
                            if v and not v.isdigit():
                                raise AppError(f"{field.replace('_', ' ').title()} should be a number.")
                            env[key] = v
                    # Cookies: blank means "keep what's saved".
                    if b.get("espn_s2", "").strip():
                        env["ESPN_S2"] = b["espn_s2"].strip()
                    if b.get("swid", "").strip():
                        swid = b["swid"].strip()
                        env["ESPN_SWID"] = swid if swid.startswith("{") else "{" + swid + "}"
                    if env:
                        save_env(env)
                    if "settings" in b:
                        s = {**load_settings(), **{k: b["settings"][k] for k in DEFAULT_SETTINGS
                                                   if k in b["settings"]}}
                        s["auto_days"] = max(1, min(7, int(s["auto_days"])))
                        save_settings(s)
                    return {"ok": True}
                return self._guard(save)
            if path == "/api/apply":
                def apply():
                    b = self._body()
                    days = max(1, min(7, int(b.get("days", 1))))
                    start = int(b.get("day", 0))
                    results, errors = [], []
                    for off in range(start, start + days):
                        try:
                            results.append(engine.apply(off, bool(b.get("use_ir", True))))
                        except AppError as e:
                            errors.append(str(e))
                    if errors and not results:
                        raise AppError(errors[0])
                    return {"results": results, "errors": errors}
                return self._guard(apply)
            self._send(404, {"error": "not found"})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="ESPN fantasy basketball lineup app")
    ap.add_argument("--demo", action="store_true", help="use a fake team (no ESPN login needed)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    bot.load_dotenv(ENV_FILE)
    engine = Engine(demo=args.demo)
    threading.Thread(target=autopilot, args=(engine,), daemon=True).start()

    url = f"http://127.0.0.1:{args.port}"
    with socket.socket() as probe:
        already_running = probe.connect_ex(("127.0.0.1", args.port)) == 0
    if already_running:
        # Double-clicked again while it's open: just bring up the page.
        print(f"Already running. Opening {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine))
    print(f"Lineup Autopilot is running at {url}")
    print("Keep this window open (it's what runs autopilot). Close it to quit.")
    if args.demo:
        print("DEMO MODE: fake team, nothing is sent to ESPN.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
