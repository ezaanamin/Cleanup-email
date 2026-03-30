# Gmail Automation Cleanup Shield 🛡️

An automated Python script that keeps your Gmail inbox pristine by batch-deleting unwanted emails — OTPs, Promotions, Social, and Spam — every night at 12:00 AM, while archiving a full log of every deleted email to a CSV file in your Google Drive.

---

## ✨ Features

- **Midnight Scheduler** — Run once and forget. The script calculates the exact time until 12:00 AM and sleeps until then, running a full cleanup cycle every night automatically.
- **Immediate Startup Cleanup** — Runs a cleanup pass the moment you start the script, before waiting for midnight. Controlled by the `RUN_ON_STARTUP` flag.
- **OTP Sweeper** — Scans your inbox for verification codes and one-time passwords by subject keyword (e.g. `"Your Aurestra Verification Code"`, `"otp"`, `"verification code"`). Fully configurable.
- **Category Cleanup** — Fully empties the **Promotions**, **Social**, and **Spam** labels. Spam is fully paginated — every page is swept, not just the first.
- **Batch Processing** — Uses Gmail API's high-performance `batchDelete` method (up to 1,000 messages per call) for lightning-fast execution.
- **CSV Audit Log** — Before any email is deleted, its metadata and plain-text body are captured and saved to `deleted_emails.csv` on your Google Drive inside a folder called `deletedemail`. The log grows daily — new rows are appended, never overwritten.
- **Deduplication** — Each run checks existing CSV rows by `Message-ID`. If the script is restarted mid-run, no email is logged twice.
- **Body Truncation Flag** — Email bodies are capped at 5,000 characters. A dedicated `Body Truncated` column (`yes`/`no`) in the CSV tells you when a body was cut off.
- **Retry Logic with Backoff** — Every Gmail and Drive API call automatically retries up to 3 times on transient errors (429, 500, 503), with exponential backoff (5s → 10s → 15s).
- **Quota-Friendly Fetching** — Email details are fetched in small batches of 10 with a gentle 300ms pause between batches, keeping well under Gmail's per-second quota limit.
- **Graceful Shutdown** — `Ctrl+C` or a `SIGTERM` signal exits cleanly with a log message instead of an ugly traceback.
- **Token Persistence** — Your OAuth session is saved to `token.json` so you only authenticate once. The token is auto-refreshed when it expires.
- **Protected Labels** — "Updates" and "Forums" are intentionally left untouched unless you add them manually.

---

## 📋 CSV Audit Log

Every deleted email is recorded with the following columns before deletion:

| Column | Description |
|---|---|
| `Message-ID` | Unique email identifier (used for deduplication) |
| `Date` | Send date from the email header |
| `From` | Sender name and address |
| `Subject` | Email subject line |
| `Type` | Category: `OTP / Verification`, `Promotions`, `Social`, or `Spam` |
| `Body` | Plain-text body (HTML tags stripped, max 5,000 chars) |
| `Body Truncated` | `yes` if the body exceeded 5,000 characters, otherwise `no` |

The CSV is saved to **Google Drive → `deletedemail/deleted_emails.csv`** and updated on every run.

---

## 🛠️ Prerequisites

### 1. Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Enable both the **Gmail API** and the **Google Drive API**.
4. Configure the **OAuth Consent Screen** (Internal or External).
5. Create a **Desktop App OAuth 2.0 Client ID**.
6. Download the JSON file and save it as `credentials.json` in the project root.

### 2. Required OAuth Scopes

The script requests two scopes on first login:

```
https://mail.google.com/
https://www.googleapis.com/auth/drive
```

> **Note:** If you previously authenticated with only the Gmail scope, delete your `token.json` and re-run the script to re-authenticate with both scopes.

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/ezaan-amin/gmail-cleanup.git
cd gmail-cleanup
```

### 2. Create and activate a virtual environment

```bash
# Linux / macOS
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
.\venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` should include:

```
google-auth
google-auth-oauthlib
google-auth-httplib2
google-api-python-client
python-dotenv
```

---

## ⚙️ Configuration

### Environment file

Create a `.env` file in the project root:

```env
BANK_EMAIL_ACCOUNT=yourname@gmail.com
```

### Script-level settings

All tunable parameters are grouped at the top of `clean_email.py`:

```python
BODY_MAX_CHARS = 5_000   # Max characters to save per email body
MAX_RETRIES    = 3        # API retry attempts on transient errors
RETRY_DELAY    = 5        # Base delay in seconds between retries
BATCH_FETCH    = 10       # Emails fetched per mini-batch (quota-friendly)
RUN_ON_STARTUP = True     # Run a cleanup immediately when the script starts
```

### OTP keywords

Add or remove subject keywords to control which inbox emails are swept:

```python
OTP_SUBJECT_KEYWORDS = [
    "Your  Verification Code",
    "otp",
    "verification code",
    "security code",
]
```

---

## 🎯 Usage

Start the Midnight Scheduler:

```bash
python3 clean_email.py
```

On first run, a browser window will open for Google OAuth authentication. After that, `token.json` is saved and all future runs are fully automatic.

### What you'll see in the log

```
2026-03-30 00:00:00  Email Automation Shield active.
2026-03-30 00:00:01  Authentication OK.
2026-03-30 00:00:01  Running immediate cleanup on startup...
2026-03-30 00:00:01  ====================================================
2026-03-30 00:00:01  CLEANUP STARTED  2026-03-30 00:00:01
2026-03-30 00:00:02    Scanning INBOX for OTP / verification emails...
2026-03-30 00:00:03    Found 4 OTP emails
2026-03-30 00:00:05    Deleted batch of 4
2026-03-30 00:00:05    Scanning: Promotions
2026-03-30 00:00:06    Found 38 in Promotions
2026-03-30 00:00:12    Deleted batch of 38
2026-03-30 00:00:12    Scanning: Social
2026-03-30 00:00:13    [EMPTY] No emails in Social
2026-03-30 00:00:13    Scanning Spam (all pages)...
2026-03-30 00:00:14    Found 11 spam emails
2026-03-30 00:00:17    Deleted batch of 11
2026-03-30 00:00:17    Emails logged this run: 53
2026-03-30 00:00:19    Updated deleted_emails.csv on Drive (53 total rows)
2026-03-30 00:00:19  CLEANUP FINISHED — 53 item(s) deleted
2026-03-30 00:00:19  ====================================================
2026-03-30 00:00:19  Next cleanup at midnight — waiting 23h 59m...
```

---

## 📁 Project Structure

```
gmail-cleanup/
├── clean_email.py       # Main script
├── credentials.json     # OAuth client credentials (never commit this)
├── token.json           # Saved session token — auto-generated (never commit this)
├── clean_email.log      # Local log file — auto-generated
├── .env                 # Environment variables
├── .gitignore           # Should include credentials.json, token.json, .env
└── requirements.txt     # Python dependencies
```

---

## 🔒 Security Notes

- This script uses the `https://mail.google.com/` scope, which grants **full mailbox access** including permanent deletion. Use it only on accounts you own and fully control.
- **Never commit** `credentials.json`, `token.json`, or `.env` to a public repository. Add them to `.gitignore`.
- The Google Drive scope is used exclusively to write and update the audit CSV in your own Drive.

### Recommended `.gitignore` entries

```
credentials.json
token.json
.env
*.log
__pycache__/
venv/
```

---

## 🔧 Troubleshooting

| Issue | Fix |
|---|---|
| `Authentication failed on startup` | Ensure `credentials.json` is present and both APIs (Gmail + Drive) are enabled in your Cloud project |
| `Token invalid or expired` | Delete `token.json` and re-run to re-authenticate |
| Emails not being deleted | Check that the correct label IDs are used; Gmail label IDs are case-sensitive |
| Drive folder not appearing | Confirm the Drive API is enabled and the `drive` scope was granted during OAuth |
| Script exits with traceback | Check `clean_email.log` for the full stack trace |
| Same emails logged twice | Ensure `Message-ID` headers are present; the deduplication logic depends on them |

---

*Created by **Ezaan Amin***