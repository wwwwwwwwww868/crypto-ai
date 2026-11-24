# Brand outreach sender (Gmail API)

Sends personalized outreach emails, one at a time with delays between sends,
to a list of contacts you've chosen yourself — not a bulk blast. Tracks who's
been contacted so nobody gets emailed twice, and honors unsubscribe requests.

This is built for small, targeted lists (brands/collaborators you've
identified), sent from your own Gmail account. It is not a mass-mailer: Gmail
caps regular accounts at ~500 sends/day and will suspend accounts that look
like they're sending bulk unsolicited mail. Keep your list to people you have
an actual reason to contact.

## 1. Get Gmail API credentials

1. Go to https://console.cloud.google.com/ and create a project (or use an
   existing one).
2. Enable the **Gmail API** for that project (APIs & Services → Library).
3. Go to APIs & Services → Credentials → Create Credentials → OAuth client ID.
   - Application type: **Desktop app**
   - If prompted to configure the OAuth consent screen first, set it to
     "External" + "Testing" mode and add your own Google account as a test
     user — that's enough for personal use, no Google review needed.
4. Download the credentials JSON and save it as `credentials.json` in this
   folder.

This app only ever requests the `gmail.send` scope — it can send mail as you,
but cannot read your inbox or anything else.

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

## 3. Fill in your contacts

Edit `contacts.csv`. Columns:

- `email` (required)
- `name`, `brand`, `niche` — used to personalize the template
- `notes` — for your own reference
- `status` — leave blank/`pending` for contacts not yet emailed; the script
  sets this to `sent` after sending. Set it to `unsubscribe` yourself if
  someone replies asking not to be contacted again, so they're never emailed
  again.
- `last_contacted` — filled in automatically

## 4. Edit the template

Edit `template.txt`. Replace every `[YOUR ...]` placeholder with your real
info (name, TikTok handle, contact details, mailing address — a physical
address is required for compliance with anti-spam law on commercial email).
`{first_name}`, `{name}`, `{brand}`, and `{niche}` get filled in per-contact
from `contacts.csv`.

## 5. Preview before sending

```bash
python outreach.py --dry-run
```

This prints every email it would send, with no Gmail calls at all. Check the
output carefully before moving on.

## 6. Send

```bash
python outreach.py --limit 5
```

The first run opens a browser window to authorize your Google account (one
time only — it saves a `token.json` afterward). `--limit` caps how many go
out in one run; omit it to go through everyone still `pending`. The script
waits 20–60 random seconds between sends and updates `contacts.csv` with
`status: sent` as it goes, so re-running it only emails new/pending contacts.

## Files you should never commit

`credentials.json` and `token.json` contain your OAuth secrets — they're
already covered by `.gitignore` additions below. Don't remove those lines.
