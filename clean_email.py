import os
import sys
import logging
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://mail.google.com/"]
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clean_email.log")

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

OTP_SUBJECT_KEYWORDS = [
    "Your Aurestra Verification Code",
    "otp",
    "verification code",
    "security code"
]

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def batch_delete(service, message_ids):
    for i in range(0, len(message_ids), 1000):
        batch = message_ids[i:i + 1000]
        service.users().messages().batchDelete(
            userId="me",
            body={"ids": batch}
        ).execute()
        log.info(f"  ✓ Deleted batch of {len(batch)}")

def delete_by_label(service, label_id, label_name):
    log.info(f"  Searching: {label_name}")
    message_ids = []
    page_token = None
    while True:
        kwargs = {"userId": "me", "labelIds": [label_id], "maxResults": 500}
        if page_token: kwargs["pageToken"] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        if not messages: break
        message_ids.extend(m["id"] for m in messages)
        page_token = result.get("nextPageToken")
        if not page_token: break
    if not message_ids:
        log.info(f"  [EMPTY] No emails in {label_name}")
        return 0
    log.info(f"  Found {len(message_ids)} emails — deleting...")
    batch_delete(service, message_ids)
    return len(message_ids)

def delete_inbox_otp(service):
    log.info("  Scanning INBOX for matching subjects...")
    message_ids = []
    for keyword in OTP_SUBJECT_KEYWORDS:
        kwargs = {"userId": "me", "q": f'in:inbox subject:"{keyword}"', "maxResults": 500}
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        message_ids.extend(m["id"] for m in messages)
    message_ids = list(set(message_ids))
    if not message_ids:
        log.info("  [EMPTY] No matching OTP emails found in INBOX")
        return 0
    log.info(f"  Found {len(message_ids)} matching emails — deleting...")
    batch_delete(service, message_ids)
    return len(message_ids)

def run_cleanup():
    log.info("=" * 50)
    log.info("AUTOMATED CLEANUP STARTED")
    try:
        service = get_gmail_service()
        total = 0
        total += delete_inbox_otp(service)
        total += delete_by_label(service, "CATEGORY_PROMOTIONS", "Promotions")
        total += delete_by_label(service, "CATEGORY_SOCIAL", "Social")
        # Spam
        try:
            spam_res = service.users().messages().list(userId="me", q="in:spam").execute()
            spam_ids = [m['id'] for m in spam_res.get('messages', [])]
            if spam_ids:
                batch_delete(service, spam_ids)
                total += len(spam_ids)
                log.info(f"  ✓ Deleted {len(spam_ids)} from Spam")
        except Exception: pass
        log.info(f"Cleanup finished. Total items deleted: {total}")
    except Exception as e:
        log.error(f"Cleanup Error: {e}")
    log.info("=" * 50)

def main():
    log.info("Email Automation Shield Active.")
    
    # Check authentication on startup
    try:
        get_gmail_service()
        log.info("Authentication verified.")
    except Exception as e:
        log.error(f"Authentication failed on startup: {e}")
        return

    while True:
        now = datetime.now()
        # Target 12:00 AM (00:00:00) the next day
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        wait_seconds = (target - now).total_seconds()
        
        hours = int(wait_seconds // 3600)
        minutes = int((wait_seconds % 3600) // 60)
        
        log.info(f"SCHEDULER: Next cleanup at 12:00 AM. Waiting {hours}h {minutes}m...")
        
        # Sleep until the target time
        time.sleep(wait_seconds)
        
        run_cleanup()
        
        # Small sleep to ensure we don't trigger again in the same second/minute
        time.sleep(60)

if __name__ == "__main__":
    main()