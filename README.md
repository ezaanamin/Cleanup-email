# Gmail Automation Cleanup Shield 🛡️

An automated Python script that keeps your Gmail inbox pristine by batch-deleting unwanted emails like OTPs, Promotions, Social, and Spam at **12:00 AM every single night**.

## ✨ Features
- **Midnight Scheduler**: Run the script once and it will automatically wait for 12:00 AM every night to perform its duty.
- **OTP Sweeper**: Automatically removes old verification codes and one-time passwords from specified subjects like "Your Aurestra Verification Code".
- **Category Cleanup**: Fully empties **Promotions**, **Social**, and **Spam** labels.
- **Batch Processing**: Uses the high-performance Gmail API `batchDelete` method for lightning-fast execution.
- **Token Persistence**: Automatically saves your sign-in session to `token.json` so you only have to log in once.
- **Safety First**: Skips protected folders like "Updates" and "Forums" unless you specify otherwise.

## 🛠️ Prerequisites
1.  **Google Cloud Project**:
    - Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
    - Enable the **Gmail API**.
    - Configure the **OAuth Consent Screen** (Internal or External).
    - Create a **Desktop App** OAuth 2.0 Client ID.
    - Download the JSON and save it as `credentials.json` in the root folder.

2.  **App Credentials**: 
    - Ensure your `credentials.json` is present in the main directory.

## 🚀 Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/ezaan-amin/gmail-cleanup.git
   cd gmail-cleanup
   ```

2. Create and activate a Virtual Environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/Mac
   # OR
   .\venv\Scripts\activate  # Windows
   ```

3. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```

## ⚙️ Configuration
Create a `.env` file for your account details:
```env
BANK_EMAIL_ACCOUNT=yourname@gmail.com
```

## 🎯 Usage
Simply run the script to start the **Midnight Scheduler**:
```bash
python3 clean_email.py
```

### Manual Trigger
The script will now calculate the time until the next **12:00 AM** and display a countdown:
`2026-03-27 23:05:00 SCHEDULER: Next cleanup at 12:00 AM. Waiting 0h 55m...`

## 🔒 Security Note
This script uses `https://mail.google.com/` scope for permanent deletion. Be careful with your `credentials.json` and `token.json` files; never commit them to public repositories.

---
Created by [Ezaan Amin](https://github.com/ezaan-amin)
# Cleanup-email-
