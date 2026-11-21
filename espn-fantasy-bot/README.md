# ESPN Fantasy Basketball Auto-Lineup Bot

Sets your ESPN fantasy basketball lineup for you every day:

- starts your best players who **have an NBA game that day**
- benches players with no game, or who are **OUT** or suspended
- optionally moves OUT players into an empty **IR** slot (`--use-ir`)
- can set several days ahead at once (`--days 7`)

"Best" means this season's average fantasy points, falling back to projections,
then last season, then % rostered (for category leagues).

## 1. Get your IDs and cookies

1. Open your team on **fantasy.espn.com** in a desktop browser while logged in.
   The URL looks like `.../basketball/team?leagueId=123456&teamId=4`. Those are
   your `ESPN_LEAGUE_ID` and `ESPN_TEAM_ID`.
2. Open the browser dev tools (F12) → **Application** (Chrome) or **Storage**
   (Firefox) → **Cookies** → `https://fantasy.espn.com`.
3. Copy the values of **`espn_s2`** and **`SWID`** (keep the `{ }` around SWID).

These cookies act like your password. Don't share them or commit them. They
last for months, but if ESPN logs you out, grab new ones.

## 2. Run it on your computer

```bash
cd espn-fantasy-bot
pip install -r requirements.txt
cp .env.example .env      # then edit .env with your values
python espn_bot.py --dry-run          # preview, changes nothing
python espn_bot.py                    # set today's lineup
python espn_bot.py --days 7 --use-ir  # set the whole week, use IR
```

## 3. Make it run by itself every day

**Option A: GitHub Actions (no computer needs to be on).** The workflow in
`.github/workflows/espn-fantasy-bot.yml` runs at 10am and 5pm ET every day.
In your GitHub repo, go to **Settings → Secrets and variables → Actions** and
add `ESPN_LEAGUE_ID`, `ESPN_TEAM_ID`, `ESPN_S2` and `ESPN_SWID`. You can also
run it by hand from the **Actions** tab ("Run workflow").

**Option B: cron on a Mac/Linux machine that's always on:**

```
0 10 * * * cd /path/to/espn-fantasy-bot && /usr/bin/python3 espn_bot.py --days 2 --use-ir >> bot.log 2>&1
```

## Notes

- ESPN has no official API. This uses the same private endpoints the ESPN site
  uses, so it can break if ESPN changes them. Automating your account may also
  go against ESPN's terms of use, so use it at your own risk.
- Players whose game has already started are locked. Run it before tip-off.
- Players already on IR are left there. Activating them may need a roster
  spot, so you still decide that yourself.
- If something looks off, `python espn_bot.py --debug` saves the raw ESPN data
  to `espn_debug.json` (which includes your league's data, so don't post it publicly).
