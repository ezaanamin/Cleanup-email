import os
import sys
import csv
import base64
import logging
import time
import re
import io
import signal
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────
SCOPES         = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive"]
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE     = os.path.join(BASE_DIR, "token.json")
CREDS_FILE     = os.path.join(BASE_DIR, "credentials.json")
LOG_FILE       = os.path.join(BASE_DIR, "clean_email.log")
DRIVE_FOLDER   = "deletedemail"
CSV_FILENAME   = "deleted_emails.csv"
CSV_HEADERS    = ["Message-ID", "Date", "From", "Subject", "Type", "Body", "Body Truncated"]
BODY_MAX_CHARS = 5000   # cap per email body
MAX_RETRIES    = 3      # API retry attempts
RETRY_DELAY    = 5      # seconds between retries
BATCH_FETCH    = 10     # emails fetched per mini-batch (quota-friendly)
RUN_ON_STARTUP = True   # set False to only run at midnight

OTP_SUBJECT_KEYWORDS = [
    "Your Aurestra Verification Code",
    "otp",
    "verification code",
    "security code",
]

SENDER_KEYWORDS = [
    "aliexpress.com",
    "alibaba.com"
]

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Graceful shutdown ────────────────────────────────────────────────────────
def _handle_signal(sig, frame):
    log.info("Shutdown signal received — exiting cleanly.")
    sys.exit(0)

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ─── API retry wrapper ────────────────────────────────────────────────────────
def api_call(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) with exponential-backoff retry on transient errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(f"  API {e.resp.status} — retry {attempt}/{MAX_RETRIES} in {wait}s")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(f"  Error: {e} — retry {attempt}/{MAX_RETRIES} in {wait}s")
                time.sleep(wait)
            else:
                raise

# ─── Authentication ─────────────────────────────────────────────────────────
def get_services():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    gmail = build("gmail", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return gmail, drive

# ─── Drive helpers ────────────────────────────────────────────────────────────
def get_or_create_drive_folder(drive, folder_name):
    q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = api_call(drive.files().list(q=q, spaces="drive", fields="files(id)").execute)
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = api_call(drive.files().create(body=meta, fields="id").execute)
    log.info(f"  Created Drive folder '{folder_name}'")
    return folder["id"]

def download_existing_csv(drive, folder_id):
    q = f"name='{CSV_FILENAME}' and '{folder_id}' in parents and trashed=false"
    res = api_call(drive.files().list(q=q, spaces="drive", fields="files(id)").execute)
    files = res.get("files", [])
    if not files:
        return [], None
    file_id = files[0]["id"]
    content = api_call(drive.files().get_media(fileId=file_id).execute)
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    return list(reader), file_id

def upload_csv_to_drive(drive, folder_id, existing_file_id, rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    media = MediaIoBaseUpload(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        resumable=False
    )
    if existing_file_id:
        api_call(drive.files().update(fileId=existing_file_id, media_body=media).execute)
        log.info(f"  Updated {CSV_FILENAME} on Drive ({len(rows)} total rows)")
    else:
        meta = {"name": CSV_FILENAME, "parents": [folder_id]}
        api_call(drive.files().create(body=meta, media_body=media, fields="id").execute)
        log.info(f"  Created {CSV_FILENAME} on Drive ({len(rows)} total rows)")

# ─── Email helpers ────────────────────────────────────────────────────────────
def strip_html(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text,  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(ent, ch)
    return re.sub(r"\s+", " ", text).strip()

def decode_part(part) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

def extract_plain_body(payload) -> str:
    mime  = payload.get("mimeType", "")
    parts = payload.get("parts", [])
    if mime == "text/plain":
        return decode_part(payload)
    if mime == "text/html":
        return strip_html(decode_part(payload))
    plain, html_fallback = "", ""
    for part in parts:
        sub = part.get("mimeType", "")
        if sub == "text/plain":
            plain = decode_part(part)
        elif sub == "text/html":
            html_fallback = strip_html(decode_part(part))
        elif sub.startswith("multipart/"):
            result = extract_plain_body(part)
            if result:
                plain = result
                break
    return plain or html_fallback

def fetch_email_details(service, msg_id, email_type):
    try:
        msg = api_call(
            service.users().messages().get(userId="me", id=msg_id, format="full").execute
        )
    except Exception as e:
        log.warning(f"  Could not fetch {msg_id}: {e}")
        return None
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = extract_plain_body(msg.get("payload", {}))
    truncated = len(body) > BODY_MAX_CHARS
    return {
        "Message-ID": headers.get("Message-ID", msg_id),
        "Date": headers.get("Date", ""),
        "From": headers.get("From", ""),
        "Subject": headers.get("Subject", "(no subject)"),
        "Type": email_type,
        "Body": body[:BODY_MAX_CHARS],
        "Body Truncated": "yes" if truncated else "no",
    }

# ─── Deletion helpers ─────────────────────────────────────────────────────────
def batch_delete(service, message_ids):
    for i in range(0, len(message_ids), 1000):
        chunk = message_ids[i:i + 1000]
        api_call(service.users().messages().batchDelete(userId="me", body={"ids": chunk}).execute)
        log.info(f"    Deleted batch of {len(chunk)}")

def collect_and_delete(service, message_ids, email_type):
    rows = []
    log.info(f"    Fetching details for {len(message_ids)} '{email_type}' emails...")
    for i in range(0, len(message_ids), BATCH_FETCH):
        for mid in message_ids[i:i + BATCH_FETCH]:
            row = fetch_email_details(service, mid, email_type)
            if row:
                rows.append(row)
        time.sleep(0.3)
    batch_delete(service, message_ids)
    return rows

def list_messages(service, **kwargs):
    ids, page_token = [], None
    while True:
        if page_token:
            kwargs["pageToken"] = page_token
        result = api_call(service.users().messages().list(**kwargs).execute)
        ids += [m["id"] for m in result.get("messages", [])]
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids

# ─── Specific deletion routines ───────────────────────────────────────────────
def delete_by_label(service, label_id, label_name):
    log.info(f"  Scanning: {label_name}")
    ids = list_messages(service, userId="me", labelIds=[label_id], maxResults=500)
    if not ids:
        log.info(f"  [EMPTY] No emails in {label_name}")
        return []
    log.info(f"  Found {len(ids)} in {label_name}")
    return collect_and_delete(service, ids, label_name)

def delete_inbox_otp(service):
    log.info("  Scanning INBOX for OTP / verification emails...")
    ids = []
    for keyword in OTP_SUBJECT_KEYWORDS:
        ids += list_messages(service, userId="me", q=f'in:inbox subject:"{keyword}"', maxResults=500)
    ids = list(set(ids))
    if not ids:
        log.info("  [EMPTY] No OTP emails found")
        return []
    log.info(f"  Found {len(ids)} OTP emails")
    return collect_and_delete(service, ids, "OTP / Verification")

def delete_spam(service):
    log.info("  Scanning Spam (all pages)...")
    ids = list_messages(service, userId="me", q="in:spam", maxResults=500)
    if not ids:
        log.info("  [EMPTY] No spam")
        return []
    log.info(f"  Found {len(ids)} spam emails")
    return collect_and_delete(service, ids, "Spam")

def delete_by_sender(service):
    rows = []
    for sender in SENDER_KEYWORDS:
        log.info(f"  Scanning all mail from: {sender} (any folder)")
        query = f'in:anywhere from:"{sender}"'
        ids = list_messages(service, userId="me", q=query)
        if not ids:
            log.info(f"  [EMPTY] No emails found from {sender}")
            continue
        log.info(f"  Found {len(ids)} emails from {sender}, preparing to delete...")
        rows += collect_and_delete(service, ids, f"Sender — {sender}")
    log.info(f"  Total sender-based emails deleted this run: {len(rows)}")
    return rows

# ─── Deduplication ───────────────────────────────────────────────────────────
def deduplicate(existing_rows, new_rows):
    seen = {r.get("Message-ID") for r in existing_rows if r.get("Message-ID")}
    unique = [r for r in new_rows if r.get("Message-ID") not in seen]
    dupes = len(new_rows) - len(unique)
    if dupes:
        log.info(f"  Skipped {dupes} duplicate(s) already in CSV")
    return unique

# ─── Main cleanup ─────────────────────────────────────────────────────────────
def run_cleanup(gmail, drive):
    log.info("=" * 52)
    log.info(f"CLEANUP STARTED  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    new_rows = []
    try:
        new_rows += delete_inbox_otp(gmail)
        new_rows += delete_by_label(gmail, "CATEGORY_PROMOTIONS", "Promotions")
        new_rows += delete_by_label(gmail, "CATEGORY_SOCIAL", "Social")
        new_rows += delete_spam(gmail)
        new_rows += delete_by_sender(gmail)

        log.info(f"  Emails logged this run: {len(new_rows)}")

        if new_rows:
            folder_id = get_or_create_drive_folder(drive, DRIVE_FOLDER)
            existing_rows, existing_file_id = download_existing_csv(drive, folder_id)
            unique_new = deduplicate(existing_rows, new_rows)
            all_rows = existing_rows + unique_new
            upload_csv_to_drive(drive, folder_id, existing_file_id, all_rows)

    except Exception as e:
        log.error(f"Cleanup error: {e}", exc_info=True)

    log.info(f"CLEANUP FINISHED — {len(new_rows)} item(s) deleted")
    log.info("=" * 52)

# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    log.info("Email Automation Shield active.")
    try:
        gmail, drive = get_services()
        log.info("Authentication OK.")
    except Exception as e:
        log.error(f"Authentication failed: {e}")
        return

    if RUN_ON_STARTUP:
        log.info("Running immediate cleanup on startup...")
        run_cleanup(gmail, drive)

    while True:
        now = datetime.now()
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        secs = (target - now).total_seconds()
        log.info(f"Next cleanup at midnight — waiting {int(secs//3600)}h {int((secs%3600)//60)}m...")
        time.sleep(secs)
        run_cleanup(gmail, drive)
        time.sleep(60)

if __name__ == "__main__":
    main()