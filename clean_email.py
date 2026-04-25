import os
import sys
import csv
import webbrowser
import base64
import logging
import time
import re
import io
import signal
import argparse
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Google’s token response often includes more scopes than we requested (e.g. openid, profile).
# oauthlib otherwise raises: Scope has changed from "…" to "…".
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# ─── Configuration ────────────────────────────────────────────────────────────
DEFAULT_AUTH_URL = os.environ.get("AUTH_SERVICE_URL", "https://principle-creating-cause-desperate.trycloudflare.com").rstrip("/")
DEFAULT_EMAIL    = os.environ.get("AUTH_EMAIL", "ezaan.amin@gmail.com")
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
LOG_FILE         = os.path.join(BASE_DIR, "clean_email.log")
DRIVE_FOLDER     = "deletedemail"
CSV_FILENAME     = "deleted_emails.csv"
CSV_HEADERS      = ["Message-ID", "Date", "From", "Subject", "Type", "Body", "Body Truncated"]
BODY_MAX_CHARS   = 5000   # cap per email body
MAX_RETRIES      = 3      # API retry attempts
RETRY_DELAY      = 5      # seconds between retries
BATCH_FETCH      = 10     # emails fetched per mini-batch (quota-friendly)
RUN_ON_STARTUP   = True   # set False to only run at midnight

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

# ─── API Client ───────────────────────────────────────────────────────────────
GOOGLE_API_BASE = "https://www.googleapis.com"

class AuthServiceProxy:
    def __init__(self, base_url, email):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self._token = None

    def check_auth(self, silent=False):
        try:
            res = requests.get(f"{self.base_url}/auth/check", params={"email": self.email}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("authenticated"):
                    return True
                return False
            
            if not silent:
                if res.status_code == 500:
                    log.debug(f"Auth Service at {self.base_url} returned 500.")
                else:
                    log.warning(f"Auth Service at {self.base_url} returned status {res.status_code}.")
            return False
        except Exception:
            return False

    def get_token(self):
        """Fetch a fresh access token from the auth service."""
        try:
            res = requests.get(
                f"{self.base_url}/auth/token",
                params={"email": self.email},
                timeout=10
            )
            res.raise_for_status()
            data = res.json()
            if data.get("action") == "login_required":
                log.error("Session expired. Please re-run the script to login again.")
                sys.exit(1)
            token = data.get("access_token") 
            if not token:
                raise ValueError(f"No access_token in response: {data}")
            self._token = token
            return token
        except Exception as e:
            log.error(f"Failed to fetch access token from auth service: {e}")
            raise

    def call(self, method, path, params=None, json_data=None, data=None, headers=None):
        """Call Google APIs directly using a Bearer token from the auth service."""
        if not self._token:
            self.get_token()

        url = f"{GOOGLE_API_BASE}/{path}"
        req_params = (params or {}).copy()

        for attempt in range(1, MAX_RETRIES + 1):
            req_headers = dict(headers or {})
            req_headers["Authorization"] = f"Bearer {self._token}"

            try:
                res = requests.request(
                    method=method,
                    url=url,
                    params=req_params,
                    json=json_data,
                    data=data,
                    headers=req_headers,
                )

                # Token expired — refresh and retry once
                if res.status_code == 401:
                    log.warning("  Access token expired — refreshing...")
                    self.get_token()
                    continue

                if res.status_code == 429 and attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    log.warning(f"  Rate limited — retry {attempt}/{MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                    continue

                res.raise_for_status()
                try:
                    return res.json()
                except Exception:
                    return res.content

            except requests.exceptions.HTTPError:
                raise
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    log.warning(f"  API Error: {e} — retry {attempt}/{MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                else:
                    raise


# ─── Authentication ───────────────────────────────────────────────────────────
def get_proxy_service(target_email, target_url):
    """Authenticate via the auth service and return a proxy client."""
    url = target_url.rstrip("/")
    email = target_email

    first_attempt = True
    while True:
        proxy = AuthServiceProxy(url, email)
        if proxy.check_auth(silent=not first_attempt):
            log.info(f"Successfully authenticated as {email} via {url}")
            return proxy

        if first_attempt:
            log.info(f"Authentication needed for {email}.")
            login_url = f"{url}/auth/google/login?email={email}"
            log.info(f"Please login at: {login_url}")
            try:
                webbrowser.open(login_url)
            except Exception:
                pass
            first_attempt = False

        print(f"\r[AUTH] Waiting for login: {email} ... ", end="", flush=True)
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(0)

# ─── Drive helpers ────────────────────────────────────────────────────────────
def get_or_create_drive_folder(proxy, folder_name):
    q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = proxy.call("GET", "drive/v3/files", params={"q": q, "spaces": "drive", "fields": "files(id)"})
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = proxy.call("POST", "drive/v3/files", json_data=meta, params={"fields": "id"})
    log.info(f"  Created Drive folder '{folder_name}'")
    return folder["id"]

def download_existing_csv(proxy, folder_id):
    q = f"name='{CSV_FILENAME}' and '{folder_id}' in parents and trashed=false"
    res = proxy.call("GET", "drive/v3/files", params={"q": q, "spaces": "drive", "fields": "files(id)"})
    files = res.get("files", [])
    if not files:
        return [], None
    file_id = files[0]["id"]
    content = proxy.call("GET", f"drive/v3/files/{file_id}", params={"alt": "media"})
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    return list(reader), file_id

def upload_csv_to_drive(proxy, folder_id, existing_file_id, rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    
    csv_content = output.getvalue().encode("utf-8")
    
    if existing_file_id:
        proxy.call("PATCH", f"upload/drive/v3/files/{existing_file_id}", params={"uploadType": "media"}, data=csv_content, headers={"Content-Type": "text/csv"})
        log.info(f"  Updated {CSV_FILENAME} on Drive ({len(rows)} total rows)")
    else:
        meta = {"name": CSV_FILENAME, "parents": [folder_id]}
        file_meta = proxy.call("POST", "drive/v3/files", json_data=meta, params={"fields": "id"})
        file_id = file_meta["id"]
        proxy.call("PATCH", f"upload/drive/v3/files/{file_id}", params={"uploadType": "media"}, data=csv_content, headers={"Content-Type": "text/csv"})
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

def fetch_email_details(proxy, msg_id, email_type):
    try:
        msg = proxy.call("GET", f"gmail/v1/users/me/messages/{msg_id}", params={"format": "full"})
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
def batch_delete(proxy, message_ids):
    for i in range(0, len(message_ids), 1000):
        chunk = message_ids[i:i + 1000]
        proxy.call("POST", "gmail/v1/users/me/messages/batchDelete", json_data={"ids": chunk})
        log.info(f"    Deleted batch of {len(chunk)}")

def collect_and_delete(proxy, message_ids, email_type):
    rows = []
    log.info(f"    Fetching details for {len(message_ids)} '{email_type}' emails...")
    for i in range(0, len(message_ids), BATCH_FETCH):
        for mid in message_ids[i:i + BATCH_FETCH]:
            row = fetch_email_details(proxy, mid, email_type)
            if row:
                rows.append(row)
        time.sleep(0.3)
    batch_delete(proxy, message_ids)
    return rows

def list_messages(proxy, **kwargs):
    ids, page_token = [], None
    path = "gmail/v1/users/me/messages"
    while True:
        params = kwargs.copy()
        if page_token:
            params["pageToken"] = page_token
        result = proxy.call("GET", path, params=params)
        ids += [m["id"] for m in result.get("messages", [])]
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids

# ─── Specific deletion routines ───────────────────────────────────────────────
def delete_by_label(proxy, label_id, label_name):
    log.info(f"  Scanning: {label_name}")
    ids = list_messages(proxy, labelIds=[label_id], maxResults=500)
    if not ids:
        log.info(f"  [EMPTY] No emails in {label_name}")
        return []
    log.info(f"  Found {len(ids)} in {label_name}")
    return collect_and_delete(proxy, ids, label_name)

def delete_inbox_otp(proxy):
    log.info("  Scanning INBOX for OTP / verification emails...")
    ids = []
    for keyword in OTP_SUBJECT_KEYWORDS:
        ids += list_messages(proxy, q=f'in:inbox subject:"{keyword}"', maxResults=500)
    ids = list(set(ids))
    if not ids:
        log.info("  [EMPTY] No OTP emails found")
        return []
    log.info(f"  Found {len(ids)} OTP emails")
    return collect_and_delete(proxy, ids, "OTP / Verification")

def delete_spam(proxy):
    log.info("  Scanning Spam (all pages)...")
    ids = list_messages(proxy, q="in:spam", maxResults=500)
    if not ids:
        log.info("  [EMPTY] No spam")
        return []
    log.info(f"  Found {len(ids)} spam emails")
    return collect_and_delete(proxy, ids, "Spam")

def delete_by_sender(proxy):
    rows = []
    for sender in SENDER_KEYWORDS:
        log.info(f"  Scanning all mail from: {sender} (any folder)")
        query = f'in:anywhere from:"{sender}"'
        ids = list_messages(proxy, q=query)
        if not ids:
            log.info(f"  [EMPTY] No emails found from {sender}")
            continue
        log.info(f"  Found {len(ids)} emails from {sender}, preparing to delete...")
        rows += collect_and_delete(proxy, ids, f"Sender — {sender}")
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
def run_cleanup(proxy):
    log.info("=" * 52)
    log.info(f"CLEANUP STARTED  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    new_rows = []
    try:
        new_rows += delete_inbox_otp(proxy)
        new_rows += delete_by_label(proxy, "CATEGORY_PROMOTIONS", "Promotions")
        new_rows += delete_by_label(proxy, "CATEGORY_SOCIAL", "Social")
        new_rows += delete_spam(proxy)
        new_rows += delete_by_sender(proxy)

        log.info(f"  Emails logged this run: {len(new_rows)}")

        if new_rows:
            folder_id = get_or_create_drive_folder(proxy, DRIVE_FOLDER)
            existing_rows, existing_file_id = download_existing_csv(proxy, folder_id)
            unique_new = deduplicate(existing_rows, new_rows)
            all_rows = existing_rows + unique_new
            upload_csv_to_drive(proxy, folder_id, existing_file_id, all_rows)

    except Exception as e:
        log.error(f"Cleanup error: {e}", exc_info=True)

    log.info(f"CLEANUP FINISHED — {len(new_rows)} item(s) deleted")
    log.info("=" * 52)

# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Email Automation Shield")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help=f"Google email to clean (default: {DEFAULT_EMAIL})")
    parser.add_argument("--url", default=DEFAULT_AUTH_URL, help=f"Auth Service URL (default: {DEFAULT_AUTH_URL})")
    args = parser.parse_args()

    log.info("Email Automation Shield active.")
    try:
        proxy = get_proxy_service(args.email, args.url)
    except Exception as e:
        log.error(f"Failed to initialize service: {e}")
        return

    if RUN_ON_STARTUP:
        log.info("Running immediate cleanup on startup...")
        run_cleanup(proxy)

    while True:
        now = datetime.now()
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        secs = (target - now).total_seconds()
        log.info(f"Next cleanup at midnight — waiting {int(secs//3600)}h {int((secs%3600)//60)}m...")
        time.sleep(secs)
        run_cleanup(proxy)
        time.sleep(60)

if __name__ == "__main__":
    main()