#!/usr/bin/env python3
"""
Opportunity Scanner v3
Strict 72-hour filter, negative keyword rejection, improved scoring.
Sources: Remotive, RemoteOK, WWR, Arbeitnow, Himalayas, Reddit,
         HN Who's Hiring, Dev.to, Nodesk, Jobicy, X, LinkedIn, Google
"""

import os, json, time, random, hashlib, logging, asyncio
import requests, feedparser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── CONFIG ───────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")

KEYWORDS = [
    "django", "fastapi", "react", "python", "node.js", "nodejs",
    "automation", "scraper", "scraping", "api integration",
    "whatsapp bot", "telegram bot", "n8n", "zapier",
    "full stack", "fullstack", "backend developer", "backend engineer",
    "saas", "crm", "trading bot", "forex", "webhook",
    "rest api", "postgresql", "postgres", "web scraping",
    "data pipeline", "celery", "redis", "freelance developer",
    "remote developer", "hire developer", "need developer",
    "looking for developer", "paying for",
]

HIGH_PRIORITY_KEYWORDS = [
    "django", "fastapi", "trading", "forex", "automation",
    "scraping", "whatsapp bot", "telegram bot", "n8n",
    "hire django", "need django", "django developer",
]

NEGATIVE_KEYWORDS = [
    "german speaking", "french speaking", "spanish speaking",
    "customer success", "customer support manager",
    "revenue operations", "account manager", "account executive",
    "sales manager", "sales engineer", "solutions engineer",
    "site reliability", "sre ", "devops engineer", "cloud engineer",
    "network engineer", "security engineer", "penetration tester",
    "graduate level", "phd", "stem ", "scientific python",
    "data scientist", "machine learning engineer", "research engineer",
    "ios developer", "android developer", "mobile developer",
    "unity developer", "game developer", "blockchain",
    "solidity", "web3", "nft", "crypto developer",
    "sas developer", "cobol", "mainframe",
    "product manager", "project manager", "scrum master",
    "ux designer", "ui designer", "graphic designer",
    "content writer", "copywriter", "seo specialist",
    "social media manager", "marketing manager",
    "hr manager", "recruiter", "talent acquisition",
]

GOOGLE_QUERIES = [
    '"hiring" "django developer" remote',
    '"need a python developer" freelance remote',
    '"looking for" "django" OR "fastapi" developer freelance',
    '"paying" "python developer" OR "backend developer" remote',
]

X_QUERIES = [
    "hiring django developer remote",
    "need python developer freelance paying",
    "looking for backend developer remote",
    "hire fastapi developer",
    "django developer needed",
    "python automation developer needed",
]

LINKEDIN_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=django%20developer&f_WT=2&f_TPR=r86400",
]

SEEN_FILE = Path("/app/data/seen_jobs.json")
MIN_SCORE = 4
MAX_AGE_H = 72

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(list(seen)))

def job_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()

def is_recent(posted_at: str) -> bool:
    if not posted_at:
        return False
    try:
        if str(posted_at).isdigit():
            dt = datetime.fromtimestamp(int(posted_at), tz=timezone.utc)
        else:
            posted_at = posted_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt <= timedelta(hours=MAX_AGE_H)
    except Exception:
        return True

def is_negative(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    return any(kw in text for kw in NEGATIVE_KEYWORDS)

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

def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_alert(title, source, url, budget, score, high) -> str:
    p = "🔴 HIGH PRIORITY" if high else "🟡 Match"
    return (f"{p} — Score {min(score,10)}/10\n\n"
            f"<b>{escape_html(title[:120])}</b>\n"
            f"📌 {escape_html(source)}\n"
            f"💰 {escape_html(budget or 'Not specified')}\n"
            f"🔗 <a href='{url}'>View / Apply</a>")

# ── API SOURCES ───────────────────────────────────────────────────────────────

def fetch_remotive():
    try:
        r = requests.get("https://remotive.com/api/remote-jobs", params={"limit": 100}, timeout=15)
        jobs = [{"title": j.get("title",""), "url": j.get("url",""),
                 "budget": j.get("salary",""), "description": j.get("description","")[:500],
                 "posted_at": j.get("publication_date",""), "source": "Remotive"}
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
                 "budget": j.get("salary",""), "description": j.get("description","")[:500],
                 "posted_at": str(j.get("epoch","")), "source": "Remote OK"}
                for j in data if isinstance(j, dict) and "position" in j]
        log.info(f"Remote OK: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Remote OK: {e}"); return []

def fetch_wwr():
    results = []
    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    ]
    for url in feeds:
        try:
            for e in feedparser.parse(url).entries[:30]:
                results.append({"title": e.get("title",""), "url": e.get("link",""),
                                 "budget": "", "description": e.get("summary","")[:500],
                                 "posted_at": e.get("published",""), "source": "We Work Remotely"})
        except Exception as ex:
            log.error(f"WWR: {ex}")
    log.info(f"WWR: {len(results)}")
    return results

def fetch_arbeitnow():
    try:
        data = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=15).json()
        jobs = [{"title": j.get("title",""), "url": j.get("url",""), "budget": "",
                 "description": j.get("description","")[:500],
                 "posted_at": str(j.get("created_at","")), "source": "Arbeitnow"}
                for j in data.get("data", []) if j.get("remote")]
        log.info(f"Arbeitnow: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Arbeitnow: {e}"); return []

def fetch_himalayas():
    try:
        data = requests.get("https://himalayas.app/jobs/api", params={"limit": 100}, timeout=15).json()
        jobs = [{"title": j.get("title",""), "url": j.get("applicationLink","https://himalayas.app"),
                 "budget": j.get("salary",""), "description": j.get("description","")[:500],
                 "posted_at": j.get("createdAt",""), "source": "Himalayas"}
                for j in data.get("jobs", [])]
        log.info(f"Himalayas: {len(jobs)}")
        return jobs
    except Exception as e:
        log.error(f"Himalayas: {e}"); return []

def fetch_devto():
    results = []
    try:
        feed = feedparser.parse("https://dev.to/feed/tag/hiring")
        for e in feed.entries[:30]:
            results.append({"title": e.get("title",""), "url": e.get("link",""),
                             "budget": "", "description": e.get("summary","")[:500],
                             "posted_at": e.get("published",""), "source": "Dev.to"})
        log.info(f"Dev.to: {len(results)}")
    except Exception as e:
        log.error(f"Dev.to: {e}")
    return results

def fetch_nodesk():
    results = []
    try:
        feed = feedparser.parse("https://nodesk.co/remote-jobs/rss.xml")
        for e in feed.entries[:30]:
            results.append({"title": e.get("title",""), "url": e.get("link",""),
                             "budget": "", "description": e.get("summary","")[:500],
                             "posted_at": e.get("published",""), "source": "Nodesk"})
        log.info(f"Nodesk: {len(results)}")
    except Exception as e:
        log.error(f"Nodesk: {e}")
    return results

def fetch_jobicy():
    results = []
    try:
        r = requests.get("https://jobicy.com/api/v2/remote-jobs",
                         params={"count": 50, "tag": "python"}, timeout=15)
        for j in r.json().get("jobs", []):
            results.append({"title": j.get("jobTitle",""), "url": j.get("url",""),
                             "budget": str(j.get("annualSalaryMin","")),
                             "description": j.get("jobDescription","")[:500],
                             "posted_at": j.get("pubDate",""), "source": "Jobicy"})
        log.info(f"Jobicy: {len(results)}")
    except Exception as e:
        log.error(f"Jobicy: {e}")
    return results

def fetch_reddit():
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OpportunityBot/3.0; +https://github.com)"}
    subreddits = ["forhire", "slavelabour", "remotework", "django", "python", "webdev"]
    for sub in subreddits:
        try:
            r = requests.get(f"https://www.reddit.com/r/{sub}/new.json",
                             params={"limit": 25}, headers=headers, timeout=15)
            if r.status_code != 200:
                log.warning(f"Reddit r/{sub}: HTTP {r.status_code}")
                continue
            for p in r.json()["data"]["children"]:
                d = p["data"]
                title   = d.get("title", "")
                created = d.get("created_utc", 0)
                if sub in ["forhire", "slavelabour"] and "[hiring]" not in title.lower():
                    continue
                results.append({"title": title,
                                 "url": f"https://reddit.com{d.get('permalink','')}",
                                 "budget": "", "description": d.get("selftext","")[:500],
                                 "posted_at": str(int(created)) if created else "",
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
            r2  = requests.get("https://hn.algolia.com/api/v1/search",
                               params={"tags": f"comment,story_{sid}", "hitsPerPage": 100}, timeout=15)
            for c in r2.json().get("hits", []):
                text = c.get("comment_text", "")
                results.append({"title": text[:80].strip(),
                                 "url": f"https://news.ycombinator.com/item?id={c.get('objectID','')}",
                                 "budget": "", "description": text[:500],
                                 "posted_at": c.get("created_at",""), "source": "HN Who's Hiring"})
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
            divs = await page.query_selector_all("div.g")
            for div in divs[:8]:
                try:
                    title_el   = await div.query_selector("h3")
                    link_el    = await div.query_selector("a[href^='http']")
                    snippet_el = await div.query_selector(".VwiC3b")
                    title   = await title_el.inner_text() if title_el else ""
                    href    = await link_el.get_attribute("href") if link_el else ""
                    snippet = await snippet_el.inner_text() if snippet_el else ""
                    if title and href and "google.com" not in href:
                        results.append({"title": title.strip(), "url": href, "budget": "",
                                         "description": snippet[:500], "posted_at": datetime.now(timezone.utc).isoformat(), "source": "Google Search"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(6, 12))
        except Exception as e:
            log.error(f"Google '{query[:40]}': {e}")
    log.info(f"Google: {len(results)}")
    return results

async def scrape_x(page, queries):
    results = []
    for query in queries:
        try:
            await page.goto(f"https://x.com/search?q={requests.utils.quote(query)}&src=typed_query&f=live",
                            wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(3, 6))
            for _ in range(3):
                await page.keyboard.press("End")
                await asyncio.sleep(random.uniform(1, 2))
            tweets = await page.query_selector_all("article[data-testid='tweet']")
            for tweet in tweets[:15]:
                try:
                    text_el = await tweet.query_selector("[data-testid='tweetText']")
                    link_el = await tweet.query_selector("a[href*='/status/']")
                    if not text_el: continue
                    text = await text_el.inner_text()
                    href = await link_el.get_attribute("href") if link_el else ""
                    url  = f"https://x.com{href}" if href.startswith("/") else href
                    results.append({"title": text[:100].strip(), "url": url or "https://x.com",
                                     "budget": "", "description": text[:500],
                                     "posted_at": datetime.now(timezone.utc).isoformat(), "source": "X (Twitter)"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(5, 10))
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
            cards = await page.query_selector_all(".job-search-card, .base-card")
            for card in cards[:20]:
                try:
                    title_el = await card.query_selector("h3, .base-search-card__title")
                    link_el  = await card.query_selector("a[href*='/jobs/']")
                    time_el  = await card.query_selector("time")
                    title  = await title_el.inner_text() if title_el else ""
                    href   = await link_el.get_attribute("href") if link_el else ""
                    posted = await time_el.get_attribute("datetime") if time_el else ""
                    if title and href:
                        results.append({"title": title.strip(), "url": href.split("?")[0],
                                         "budget": "", "description": title,
                                         "posted_at": posted, "source": "LinkedIn Jobs"})
                except Exception:
                    continue
            await asyncio.sleep(random.uniform(5, 10))
        except Exception as e:
            log.error(f"LinkedIn: {e}")
    log.info(f"LinkedIn: {len(results)}")
    return results

async def run_playwright_sources():
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1366, "height": 768},
                locale="en-US", timezone_id="America/New_York",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()
            results += await scrape_google(page, GOOGLE_QUERIES)
            results += await scrape_x(page, X_QUERIES)
            results += await scrape_linkedin(page, LINKEDIN_URLS)
            await browser.close()
    except Exception as e:
        log.error(f"Playwright: {e}")
    return results

# ── MAIN ─────────────────────────────────────────────────────────────────────

def run_scan():
    log.info("=" * 55)
    log.info(f"Scan started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    seen, new_seen, alerts = load_seen(), set(), 0
    rejected_old = rejected_neg = rejected_score = 0

    all_jobs = (
        fetch_remotive()   +
        fetch_remoteok()   +
        fetch_wwr()        +
        fetch_arbeitnow()  +
        fetch_himalayas()  +
        fetch_devto()      +
        fetch_nodesk()     +
        fetch_jobicy()     +
        fetch_reddit()     +
        fetch_hackernews()
    )

    try:
        all_jobs += asyncio.run(run_playwright_sources())
    except Exception as e:
        log.error(f"Playwright failed: {e}")

    log.info(f"Total fetched: {len(all_jobs)}")

    for job in all_jobs:
        title = job.get("title","").strip()
        url   = job.get("url","").strip()
        if not title or not url: continue

        jid = job_id(title, url)
        new_seen.add(jid)
        if jid in seen: continue

        if not is_recent(job.get("posted_at","")):
            rejected_old += 1; continue

        if is_negative(title, job.get("description","")):
            rejected_neg += 1; continue

        score, high = score_job(title, job.get("description",""))
        if score < MIN_SCORE:
            rejected_score += 1; continue

        send_telegram(format_alert(title, job.get("source","Unknown"), url,
                                   job.get("budget",""), score, high))
        alerts += 1
        time.sleep(1)

    save_seen(seen | new_seen)
    log.info(f"Filtered — old: {rejected_old} | negative: {rejected_neg} | low score: {rejected_score}")
    log.info(f"Done. Alerts sent: {alerts}")
    log.info("=" * 55)

if __name__ == "__main__":
    run_scan()