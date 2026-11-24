#!/usr/bin/env python3
"""Personalized brand outreach sender using the Gmail API.

Sends one email at a time to contacts in contacts.csv, filling in the
template from template.txt, rate-limiting between sends, and recording
status back into the CSV so nobody gets emailed twice.

Setup: see README.md for the Google Cloud OAuth steps.

Usage:
    python outreach.py --dry-run      # preview without sending anything
    python outreach.py                # actually send
    python outreach.py --limit 5      # send at most 5 this run
"""
import argparse
import base64
import csv
import random
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
HERE = Path(__file__).parent
CREDENTIALS_FILE = HERE / "credentials.json"
TOKEN_FILE = HERE / "token.json"
CONTACTS_FILE = HERE / "contacts.csv"
TEMPLATE_FILE = HERE / "template.txt"

MIN_DELAY_SECONDS = 20
MAX_DELAY_SECONDS = 60


def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                sys.exit(
                    f"Missing {CREDENTIALS_FILE}. Follow README.md to download "
                    "your OAuth client credentials from Google Cloud Console first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def load_template():
    text = TEMPLATE_FILE.read_text()
    subject_line, _, body = text.partition("\n")
    if not subject_line.startswith("SUBJECT:"):
        sys.exit("template.txt must start with a line like 'SUBJECT: ...'")
    subject = subject_line[len("SUBJECT:"):].strip()
    return subject, body.strip()


def load_contacts():
    with open(CONTACTS_FILE, newline="") as f:
        return list(csv.DictReader(f))


def save_contacts(rows, fieldnames):
    with open(CONTACTS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fill_template(subject, body, row):
    first_name = (row.get("name") or "there").split()[0]
    fields = {
        "first_name": first_name,
        "name": row.get("name") or "there",
        "brand": row.get("brand") or "",
        "niche": row.get("niche") or "",
    }
    return subject.format(**fields), body.format(**fields)


def build_message(to_addr, subject, body):
    message = MIMEText(body)
    message["to"] = to_addr
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview only, send nothing")
    parser.add_argument("--limit", type=int, default=None, help="Max emails to send this run")
    args = parser.parse_args()

    subject_tpl, body_tpl = load_template()
    if "[YOUR NAME]" in body_tpl or "[YOUR_HANDLE]" in body_tpl:
        print(
            "WARNING: template.txt still has [PLACEHOLDER] text in it. "
            "Fill in your real name/handle/links before sending for real.\n",
            file=sys.stderr,
        )

    rows = load_contacts()
    fieldnames = rows[0].keys() if rows else []
    service = None if args.dry_run else get_gmail_service()

    sent_count = 0
    for row in rows:
        if row.get("status", "").strip().lower() not in ("", "pending"):
            continue  # already sent, unsubscribed, or bounced — skip
        if not row.get("email"):
            continue
        if args.limit is not None and sent_count >= args.limit:
            break

        subject, body = fill_template(subject_tpl, body_tpl, row)

        if args.dry_run:
            print(f"--- Would send to {row['email']} ---")
            print(f"Subject: {subject}")
            print(body)
            print()
        else:
            message = build_message(row["email"], subject, body)
            service.users().messages().send(userId="me", body=message).execute()
            row["status"] = "sent"
            row["last_contacted"] = time.strftime("%Y-%m-%d")
            print(f"Sent to {row['email']}")

        sent_count += 1

        if not args.dry_run and sent_count < len(rows):
            delay = random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
            time.sleep(delay)

    if not args.dry_run:
        save_contacts(rows, fieldnames)

    print(f"\n{'Would send' if args.dry_run else 'Sent'}: {sent_count} email(s).")


if __name__ == "__main__":
    main()
