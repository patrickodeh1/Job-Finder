#!/usr/bin/env python3
"""
Full Opportunity Scanner v2
Scans job boards, Reddit, HN, X (Twitter), LinkedIn, and Google Search
Sends instant Telegram alerts for matching opportunities.
"""

import os, json, time, random, hashlib, logging, asyncio
import requests, feedparser
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── CONFIG ───────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")

KEYWORDS = [
    "django", "react", "python", "fastapi", "node.js", "nodejs",
    "automation", "scraper", "scraping", "api integration",
    "whatsapp bot", "telegram bot", "n8n", "zapier", "full stack",
    "fullstack", "backend developer", "saas", "crm", "trading bot",
    "forex", "webhook", "rest api", "postgresql", "postgres",
    "web scraping", "data pipeline", "celery", "redis",
]

HIGH_PRIORITY_KEYWORDS = [
    "django", "fastapi", "python", "trading", "forex",
    "automation", "scraping", "whatsapp", "telegram bot",
]

GOOGLE_QUERIES = [
    "hiring django developer remote 2026",
    "need python developer freelance",
    "paying for automation developer",
    "remote backend developer needed",
    "hire fastapi developer",
    "web scraping developer needed",
]

X_QUERIES = [
    "hiring django developer",
    "need python developer remote",
    "paying for developer",
    "hire backend developer",
    "remote python gig",
    "need automation developer",
]

LINKEDIN_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=django%20developer&f_WT=2",
    "https://www.linkedin.com/jobs/search/?keywords=python%20developer%20remote&f_WT=2",
    "https://www.linkedin.com/jobs/search/?keywords=fastapi%20developer&f_WT=2",
]

SEEN_FILE = Path("/app/data/seen_jobs.json")
MIN_SCORE = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ── HELPERS ──────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(list(seen)))

def job_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()

def score_job(title: str, description: str = "") -> tuple:
    text = f"{title} {description}".lower()
    score, high = 0, False
    for kw in KEYWORDS:
        if kw in text: score += 1
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in text: high = True; score += 2
    return score, high

def send_telegram(message: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        ).raise_for_status()
        log.info("Alert sent")
    except Exception as e:
        log.error(f"Telegram: {e}")

def format_alert(title, source, url, budget, score, high) -> str:
    p = "🔴 HIGH PRIORITY" if high else "🟡 Match"
    return (f"{p} — Score {min(score,10)}/10\n\n"
            f"<b>{title[:120]}</b>\n"
            f"📌 {source}\n"
            f"💰 {budget or 'Not specified'}\n"
            f"🔗 <a href='{url}'>View / Apply</a>")

# ── API SOURCES ───────────────────────────────────────────────────────────────

def fetch_remotive():
    try:
        r = requests.get("https://remotive.com/api/remote-jobs", params={"limit": 50}, timeout=15)
        jobs = [{"title": j.get("title",""), "url": j.get("url",""), "budget": j.get("salary",""),
                 "description": j.get("description","")[:400], "source": "Remotive"}
                for j in r.json().get("jobs", [])]
        log.info(f"Remotive: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Remotive: {e}"); return []

def fetch_remoteok():
    try:
        data = requests.get("https://remoteok.com/api", headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
        jobs = [{"title": j.get("position",""),
                 "url": j.get("url", f"https://remoteok.com/remote-jobs/{j.get('id','')}"),
                 "budget": j.get("salary",""), "description": j.get("description","")[:400], "source": "Remote OK"}
                for j in data if isinstance(j, dict) and "position" in j]
        log.info(f"Remote OK: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Remote OK: {e}"); return []

def fetch_wwr():
    results = []
    for url in ["https://weworkremotely.com/categories/remote-programming-jobs.rss",
                "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"]:
        try:
            for e in feedparser.parse(url).entries[:20]:
                results.append({"title": e.get("title",""), "url": e.get("link",""),
                                 "budget": "", "description": e.get("summary","")[:400], "source": "We Work Remotely"})
        except Exception as ex:
            log.error(f"WWR: {ex}")
    log.info(f"WWR: {len(results)}")
    return results

def fetch_arbeitnow():
    try:
        data = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=15).json()
        jobs = [{"title": j.get("title",""), "url": j.get("url",""), "budget": "",
                 "description": j.get("description","")[:400], "source": "Arbeitnow"}
                for j in data.get("data", []) if j.get("remote")]
        log.info(f"Arbeitnow: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Arbeitnow: {e}"); return []

def fetch_himalayas():
    try:
        data = requests.get("https://himalayas.app/jobs/api", params={"limit": 50}, timeout=15).json()
        jobs = [{"title": j.get("title",""), "url": j.get("applicationLink","https://himalayas.app"),
                 "budget": j.get("salary",""), "description": j.get("description","")[:400], "source": "Himalayas"}
                for j in data.get("jobs", [])]
        log.info(f"Himalayas: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Himalayas: {e}"); return []

def fetch_reddit():
    results = []
    headers = {"User-Agent": "OpportunityScanner/2.0"}
    for sub in ["forhire", "slavelabour", "remotework", "django", "python"]:
        try:
            data = requests.get(f"https://www.reddit.com/r/{sub}/new.json",
                                params={"limit": 25}, headers=headers, timeout=15).json()
            for p in data["data"]["children"]:
                d = p["data"]
                title = d.get("title", "")
                if "[hiring]" in title.lower() or sub in ["django", "python", "remotework"]:
                    results.append({"title": title,
                                    "url": f"https://reddit.com{d.get('permalink','')}",
                                    "budget": "", "description": d.get("selftext","")[:400],
                                    "source": f"Reddit r/{sub}"})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            log.error(f"Reddit r/{sub}: {e}")
    log.info(f"Reddit: {len(results)}")
    return results

def fetch_hackernews():
    results = []
    try:
        r = requests.get("https://hn.algolia.com/api/v1/search",
                         params={"query": "Ask HN: Who is hiring", "tags": "story", "hitsPerPage": 1}, timeout=15)
        hits = r.json().get("hits", [])
        if hits:
            sid = hits[0]["objectID"]
            r2 = requests.get("https://hn.algolia.com/api/v1/search",
                               params={"tags": f"comment,story_{sid}", "hitsPerPage": 50}, timeout=15)
            for c in r2.json().get("hits", []):
                text = c.get("comment_text", "")
                results.append({"title": text[:80].strip(),
                                 "url": f"https://news.ycombinator.com/item?id={c.get('objectID','')}",
                                 "budget": "", "description": text[:400], "source": "HN Who's Hiring"})
        log.info(f"HN: {len(results)}")
    except Exception as e:
        log.error(f"HN: {e}")
    return results

# ── PLAYWRIGHT SOURCES ────────────────────────────────────────────────────────

async def scrape_google(page, queries):
    results = []
    for query in queries:
        try:
            await page.goto(f"https://www.google.com/search?q={requests.utils.quote(query)}&tbs=qdr:d",
                            wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(2, 4))
            for div in await page.query_selector_all("div.g")[:8]:
                try:
                    title_el   = await div.query_selector("h3")
                    link_el    = await div.query_selector("a[href^='http']")
                    snippet_el = await div.query_selector(".VwiC3b")
                    title   = await title_el.inner_text() if title_el else ""
                    href    = await link_el.get_attribute("href") if link_el else ""
                    snippet = await snippet_el.inner_text() if snippet_el else ""
                    if title and href and "google.com" not in href:
                        results.append({"title": title.strip(), "url": href, "budget": "",
                                         "description": snippet[:400], "source": "Google Search"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(5, 10))
        except Exception as e:
            log.error(f"Google '{query[:30]}': {e}")
    log.info(f"Google: {len(results)}")
    return results

async def scrape_x(page, queries):
    results = []
    for query in queries:
        try:
            await page.goto(f"https://x.com/search?q={requests.utils.quote(query)}&src=typed_query&f=live",
                            wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(3, 6))
            for _ in range(2):
                await page.keyboard.press("End")
                await asyncio.sleep(random.uniform(1, 2))
            for tweet in await page.query_selector_all("article[data-testid='tweet']")[:10]:
                try:
                    text_el = await tweet.query_selector("[data-testid='tweetText']")
                    link_el = await tweet.query_selector("a[href*='/status/']")
                    if not text_el: continue
                    text = await text_el.inner_text()
                    href = await link_el.get_attribute("href") if link_el else ""
                    url  = f"https://x.com{href}" if href.startswith("/") else href
                    results.append({"title": text[:100].strip(), "url": url or "https://x.com",
                                     "budget": "", "description": text[:400], "source": "X (Twitter)"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(4, 8))
        except Exception as e:
            log.error(f"X '{query}': {e}")
    log.info(f"X: {len(results)}")
    return results

async def scrape_linkedin(page, urls):
    results = []
    for url in urls:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(3, 5))
            for card in await page.query_selector_all(".job-search-card, .base-card")[:15]:
                try:
                    title_el = await card.query_selector("h3, .base-search-card__title")
                    link_el  = await card.query_selector("a[href*='/jobs/']")
                    title = await title_el.inner_text() if title_el else ""
                    href  = await link_el.get_attribute("href") if link_el else ""
                    if title and href:
                        results.append({"title": title.strip(), "url": href.split("?")[0],
                                         "budget": "", "description": title, "source": "LinkedIn Jobs"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(5, 10))
        except Exception as e:
            log.error(f"LinkedIn: {e}")
    log.info(f"LinkedIn: {len(results)}")
    return results

async def run_playwright_sources():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="en-US", timezone_id="America/New_York",
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()

        results += await scrape_google(page, GOOGLE_QUERIES)
        results += await scrape_x(page, X_QUERIES)
        results += await scrape_linkedin(page, LINKEDIN_URLS)

        await browser.close()
    return results

# ── MAIN ─────────────────────────────────────────────────────────────────────

def run_scan():
    log.info("=" * 55)
    log.info(f"Scan started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    seen, new_seen, alerts = load_seen(), set(), 0

    all_jobs = (fetch_remotive() + fetch_remoteok() + fetch_wwr() +
                fetch_arbeitnow() + fetch_himalayas() + fetch_reddit() + fetch_hackernews())

    try:
        all_jobs += asyncio.run(run_playwright_sources())
    except Exception as e:
        log.error(f"Playwright failed: {e}")

    log.info(f"Total: {len(all_jobs)} opportunities")

    for job in all_jobs:
        title = job.get("title", "").strip()
        url   = job.get("url", "").strip()
        if not title or not url: continue

        jid = job_id(title, url)
        new_seen.add(jid)
        if jid in seen: continue

        score, high = score_job(title, job.get("description", ""))
        if score < MIN_SCORE: continue

        send_telegram(format_alert(title, job.get("source","Unknown"), url,
                                   job.get("budget",""), score, high))
        alerts += 1
        time.sleep(1)

    save_seen(seen | new_seen)
    log.info(f"Done. Alerts sent: {alerts}")
    log.info("=" * 55)

if __name__ == "__main__":
    run_scan()
