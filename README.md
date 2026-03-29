# Opportunity Scanner v2 — Setup Guide

## Sources covered
- Remotive, Remote OK, We Work Remotely, Arbeitnow, Himalayas (free APIs)
- Reddit: r/forhire, r/slavelabour, r/remotework, r/django, r/python
- Hacker News Who's Hiring
- X (Twitter) — live search via Playwright
- LinkedIn Jobs — via Playwright
- Google Search — broad web coverage via Playwright

## Step 1 — Create Telegram Bot

1. Message @BotFather on Telegram
2. Send /newbot, follow prompts, copy the token
3. Message your bot anything, then visit:
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
4. Find "chat": {"id": XXXXXXX} — that is your Chat ID

## Step 2 — Deploy on VPS

```bash
mkdir ~/scanner && cd ~/scanner

# Copy all files here:
# opportunity_scanner.py, requirements.txt
# Dockerfile, docker-compose.yml, .env.example

# Set credentials
cp .env.example .env
nano .env   # paste your token and chat ID

# Create data folder
mkdir data

# Build (takes 3-5 mins first time, downloads Chromium)
docker compose build

# Run in background
docker compose up -d

# Watch logs
docker logs opportunity_scanner -f
```

## Step 3 — Verify it works

You should see Telegram alerts within the first scan.
Check logs for "Scan started" and "Alerts sent: X"

## Customise keywords

Edit KEYWORDS in opportunity_scanner.py then rebuild:
```bash
docker compose down
docker compose build
docker compose up -d
```

## Adjust scan frequency

Change sleep 900 in docker-compose.yml command:
- 900  = every 15 minutes
- 600  = every 10 minutes
- 1800 = every 30 minutes

## Adjust sensitivity

Change MIN_SCORE in opportunity_scanner.py:
- 1 = more alerts, lower relevance
- 3 = fewer alerts, higher relevance

## Notes on X and LinkedIn

Both platforms actively block scrapers. The script uses
human-like delays and masks automation signals. If they
start blocking consistently, the API sources will still
run fine — Playwright sources fail gracefully without
stopping the rest of the scan.
