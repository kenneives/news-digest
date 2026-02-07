# Daily News Digest

A Python script that fetches news from RSS feeds, uses Claude AI to create a personalized summary, and emails you a daily digest.

## Features

- Fetches from 17+ RSS sources across tech, finance, science, and world news
- Uses Claude AI to summarize and prioritize based on your interests
- **Duplicate detection**: Tracks sent articles to avoid repeating content day-to-day
- **Heavy filtering**: Claude selects only the best 15-25 articles from hundreds fetched
- Sends a clean HTML email digest
- **Error notifications**: Emails you if the script fails or API credits run out

---

## Setup (macOS/Linux)

### 1. Install dependencies

```bash
cd /Users/kenne/ClaudeCode/news-digest
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

- **ANTHROPIC_API_KEY**: Get from https://console.anthropic.com/
- **GMAIL_ADDRESS**: Your Gmail address
- **GMAIL_APP_PASSWORD**: Generate at Google Account → Security → 2-Step Verification → App passwords
- **RECIPIENT_EMAIL**: Where to send the digest

### 3. Test the script

```bash
python news_digest.py
```

### 4. Set up cron job for 8am daily

Open your crontab:
```bash
crontab -e
```

Add this line (runs at 8:00 AM every day):
```
0 8 * * * cd /Users/kenne/ClaudeCode/news-digest && /usr/bin/python3 news_digest.py >> /Users/kenne/ClaudeCode/news-digest/digest.log 2>&1
```

To verify the cron job was added:
```bash
crontab -l
```

---

## Setup (Windows)

### 1. Install dependencies

Open Command Prompt or PowerShell:
```cmd
cd C:\path\to\news-digest
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example file:
```cmd
copy .env.example .env
```

Edit `.env` in Notepad or your preferred editor with your credentials.

### 3. Test the script

```cmd
python news_digest.py
```

### 4. Set up Task Scheduler for 8am daily

**Option A: Using the GUI**

1. Press `Win + R`, type `taskschd.msc`, press Enter
2. Click **Create Basic Task** in the right panel
3. Name: `Daily News Digest`, click Next
4. Trigger: Select **Daily**, click Next
5. Set start time to **8:00:00 AM**, click Next
6. Action: Select **Start a program**, click Next
7. Configure the program:
   - **Program/script**: `python` (or full path like `C:\Python312\python.exe`)
   - **Add arguments**: `news_digest.py`
   - **Start in**: `C:\path\to\news-digest` (your actual folder path)
8. Check **Open the Properties dialog** and click Finish
9. In Properties, go to **Settings** tab:
   - Check "Run task as soon as possible after a scheduled start is missed"
   - Check "If the task fails, restart every: 1 hour"
10. Click OK

**Option B: Using PowerShell (one command)**

Run PowerShell as Administrator and execute:

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "news_digest.py" -WorkingDirectory "C:\path\to\news-digest"
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "DailyNewsDigest" -Action $action -Trigger $trigger -Settings $settings -Description "Fetches news and emails daily digest"
```

**To verify the task:**
```powershell
Get-ScheduledTask -TaskName "DailyNewsDigest"
```

**To run it manually for testing:**
```powershell
Start-ScheduledTask -TaskName "DailyNewsDigest"
```

**To remove the task:**
```powershell
Unregister-ScheduledTask -TaskName "DailyNewsDigest" -Confirm:$false
```

---

## Error Notifications

The script automatically emails you when something goes wrong:

| Error Type | What It Means |
|------------|---------------|
| **API Credits Exhausted** | Your Anthropic account has run out of credits |
| **API Rate Limit Exceeded** | Too many requests; wait or upgrade your plan |
| **API Authentication Failed** | Your API key is invalid or revoked |
| **Email Sending Failed** | Gmail credentials are wrong or app password expired |
| **No New Articles Found** | All articles were already sent previously (slow news day or feed issue) |
| **Unexpected Error** | Something else went wrong (full traceback included) |

All error emails include suggested actions and relevant details to help you fix the issue.

---

## Customization

### Modify your interests

Edit the `INTERESTS` variable in `news_digest.py` to change what topics Claude prioritizes.

### Add/remove news sources

Edit the `RSS_FEEDS` dictionary in `news_digest.py` to add or remove sources.

### Change the number of articles

Set `MAX_ARTICLES_PER_SOURCE` in `.env` (default: 5)

### Use a different Claude model

Set `DIGEST_MODEL` in `.env` (default: claude-sonnet-4-20250514)

---

## Troubleshooting

### Gmail "Less secure app" errors
- Make sure 2-Step Verification is enabled on your Google account
- Use an App Password, not your regular password
- App passwords are 16 characters with no spaces

### Cron job not running (macOS/Linux)
- Check the log file: `tail -f digest.log`
- Ensure Python path is correct: `which python3`
- macOS may require Full Disk Access for cron in System Preferences → Privacy

### Task Scheduler not running (Windows)
- Open Task Scheduler and check the task's **History** tab for errors
- Ensure "Run whether user is logged on or not" is set if needed
- Verify the Python path: `where python` in Command Prompt
- Check that the working directory path is correct

### RSS feed errors
- Some feeds may be rate-limited or require different parsing
- Check the console output for specific feed errors
- Try running manually to see detailed error messages

### API credit issues
- Check your usage at https://console.anthropic.com/
- The script will email you when credits run low or are exhausted
