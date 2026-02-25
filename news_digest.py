#!/usr/bin/env python3
"""
Daily News Digest Generator

Fetches news from RSS feeds, summarizes using Claude API,
and sends a personalized email digest.
"""

import hashlib
import json
import os
import random
import smtplib
import ssl
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import anthropic
import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from audio_generator import cleanup_old_audio, generate_audio
from audiobookshelf_client import get_podcast_url, trigger_library_scan
from podcast_generator import extract_text_from_html, generate_podcast_script, parse_script

load_dotenv()

# History file to track sent articles (prevents duplicates)
HISTORY_FILE = Path(__file__).parent / "digest_history.json"
MODEL_CACHE_FILE = Path(__file__).parent / "model_cache.json"
DEFAULT_LOG_FILE = Path(__file__).parent / "digest.log"

# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Article:
    title: str
    link: str
    summary: str
    source: str
    published: Optional[datetime] = None


# Your interests - Claude will prioritize and contextualize based on these
INTERESTS = """
## PRIORITY INTERESTS (Feature prominently in Top Stories)

0. **Project Liberty, Frequency, Tala, Luma AI & Angler AI** (ALWAYS include if ANY article matches — from ANY source)
   - Project Liberty (projectliberty.io) - digital rights, data ownership, human-centric AI
   - Frequency (frequency.xyz) - layer 1 blockchain for user-controlled data
   - Tala (tala.co) - fintech, AI-powered credit, financial inclusion in emerging markets
   - Luma AI (lumalabs.ai) - AI video generation, Dream Machine, 3D/visual AI
   - Angler AI (getangler.ai) - predictive AI for growth marketing, audience targeting
   - Kenne has personal ties to ALL of these — ANY news is personally important
   - Funding rounds, product launches, partnerships, and key announcements are CRITICAL
   - Include in Top Priority as ADDITIONAL items (never replace other top items)
   - SCAN ALL SOURCES: If any article mentions "Project Liberty", "Frequency blockchain",
     "DSNP", "Tala" (fintech), "Luma AI", "Luma Labs", "Dream Machine", "Angler AI",
     or "getangler", ALWAYS include it

1. **AI/ML & LLMs**
   - New AI tools, frameworks, and developer resources
   - Funding rounds and acquisitions in AI space
   - Business applications and industry adoption trends
   - Coding tips, tutorials, and best practices for AI development
   - Breakthrough research papers and their practical implications
   - Anthropic news specifically (Claude, company updates, research)

2. **Tech Job Market & Opportunities**
   - Companies actively hiring in tech/AI
   - Startup funding announcements (signals growth/hiring)
   - Layoffs or hiring freezes at major tech companies
   - Remote work trends and compensation data

3. **Robotics + AI Convergence**
   - Humanoid robots, industrial automation
   - AI-powered robotics breakthroughs
   - Companies like Boston Dynamics, Figure, Tesla Bot, etc.

4. **Bio-hacking & Longevity**
   - GLP-1 receptor agonists research (Ozempic, Mounjaro, etc.)
   - Supplement science and nootropics
   - Health optimization technology and wearables
   - Longevity research and anti-aging breakthroughs

## HIGH INTEREST (Include if noteworthy)

5. **Social Networks & Platforms**
   - Social media platform developments, policy changes, and new features
   - Decentralized social platforms (Bluesky, Mastodon, Frequency.xyz/Project Liberty, Farcaster, Lens)
   - Creator economy trends and monetization
   - Content moderation, algorithmic transparency, and platform governance
   - **Social + AI intersection**: AI-powered social features, recommendation systems, AI content detection
   - **Social + Blockchain/Web3 intersection**: decentralized identity, token-gated communities, on-chain social graphs
   - **Social + AI + Web3 convergence**: AI agents on social platforms, decentralized AI training on social data

6. **Web3 & Blockchain** (NO crypto price speculation)
   - Regulatory developments and legal clarity
   - Practical enterprise use cases
   - Infrastructure and developer tooling
   - SKIP: Price predictions, "to the moon" hype, memecoins

7. **Automotive Innovation**
   - Chinese EV manufacturers (BYD, NIO, Xpeng) and their tech
   - Suspension technology and driving dynamics
   - AI/self-driving developments (Tesla FSD, Waymo, etc.)
   - Range extension and battery technology
   - Performance car news relevant to BMW M3/M4 and Toyota GR86 platforms

8. **Climate Tech**
   - Technology-driven environmental solutions
   - Marine conservation technology
   - Carbon capture and clean energy innovation
   - Sustainable transportation

## MODERATE INTEREST (Include selectively)

9. **Finance, Fintech & Crypto Industry**
   - M&A activity, major funding rounds, IPOs in fintech/crypto
   - Global macro signals: rate decisions, currency moves, recession indicators
   - Stock market catalysts: earnings surprises, sector rotations
   - Crypto industry news: exchange developments, DeFi milestones, institutional adoption
   - Fintech product launches: neobanks, payment rails, embedded finance
   - SKIP: Day-trading tips, price predictions, "get rich" schemes

10. **Legal & Regulatory Landscape**
    - Tech regulation: AI governance, antitrust actions, platform liability
    - Crypto/fintech regulation: SEC enforcement, stablecoin rules, CBDC developments
    - Automotive/EV policy: emissions rules, trade tariffs, safety mandates
    - Robotics & AI labor law: automation impact, liability frameworks
    - Global regulatory divergence: US vs EU vs Asia approaches
    - M&A antitrust: major deal approvals/blocks, FTC/DOJ actions
    - SKIP: Partisan framing, opinion pieces about regulation

11. **Entertainment**
   - Award-winning films and TV (Emmys, Oscars, critical acclaim)
   - Must-watch sci-fi releases
   - Popular streaming shows worth watching
   - SKIP: Celebrity gossip, relationship drama, tabloid content

12. **Space Exploration**
    - Major mission updates (NASA, SpaceX, etc.)
    - Scientific discoveries from space missions

13. **Biomedical Breakthroughs**
    - FDA approvals for significant treatments
    - Medical research with near-term patient impact

14. **Political & Economic Trends**
    - Factual policy changes affecting tech, business, or science
    - Economic indicators and market trends
    - SKIP: Partisan opinion pieces, political drama

## STRICT FILTERS (Always exclude)
- Celebrity gossip and entertainment drama
- Crypto price speculation and "get rich" schemes
- Partisan political commentary and opinion pieces
- Clickbait and sensationalized headlines
- Promotional content disguised as news
"""

# RSS Feeds organized by category
RSS_FEEDS = {
    # Tech & AI (Priority)
    "Hacker News": "https://hnrss.org/frontpage",
    "TechCrunch": "https://techcrunch.com/feed/",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "The Information": "https://www.theinformation.com/feed",

    # Robotics & Automation
    "IEEE Spectrum Robotics": "https://spectrum.ieee.org/feeds/topic/robotics",
    "The Robot Report": "https://www.therobotreport.com/feed/",

    # Automotive & EVs
    "Electrek": "https://electrek.co/feed/",
    "InsideEVs": "https://insideevs.com/rss/news/",
    "The Drive": "https://www.thedrive.com/feed",

    # Social Platforms & Policy
    "Platformer": "https://www.platformer.news/rss/",

    # Personal priority companies (invested in / working at)
    "Project Liberty": "https://www.projectliberty.io/feed/",
    "Tala": "https://tala.co/feed/",

    # Web3 & Blockchain (filtered by Claude for non-price content)
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",

    # Health & Longevity
    "STAT News": "https://www.statnews.com/feed/",
    "Longevity Technology": "https://longevity.technology/feed/",

    # Climate Tech
    "Canary Media": "https://www.canarymedia.com/feed",
    "CleanTechnica": "https://cleantechnica.com/feed/",

    # Major News Outlets
    "BBC News": "https://feeds.bbci.co.uk/news/rss.xml",
    "Reuters": "https://www.reutersagency.com/feed/",
    "NPR News": "https://feeds.npr.org/1001/rss.xml",

    # Finance & Business
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "Financial Times": "https://www.ft.com/rss/home",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",

    # Fintech & Crypto Industry
    "Finextra": "https://www.finextra.com/rss/headlines.aspx",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "TechCrunch Fintech": "https://techcrunch.com/category/fintech/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",

    # Legal & Regulatory
    "Reuters Legal": "https://www.reuters.com/legal/rss",
    "The Register": "https://www.theregister.com/headlines.atom",
    "Rest of World": "https://restofworld.org/feed/",

    # Science & Space
    "Science Daily": "https://www.sciencedaily.com/rss/all.xml",
    "Phys.org": "https://phys.org/rss-feed/",
    "Nature News": "https://www.nature.com/nature.rss",
    "NASA": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "Space.com": "https://www.space.com/feeds/all",

    # Entertainment (filtered for quality)
    "The Hollywood Reporter": "https://www.hollywoodreporter.com/feed/",
}

# =============================================================================
# Duplicate Detection
# =============================================================================

def get_article_hash(article: Article) -> str:
    """Generate a unique hash for an article based on title and link."""
    unique_str = f"{article.title.lower().strip()}|{article.link.lower().strip()}"
    return hashlib.md5(unique_str.encode()).hexdigest()


def load_history() -> dict:
    """Load the history of sent articles."""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"sent_articles": {}, "last_cleanup": None}
    return {"sent_articles": {}, "last_cleanup": None}


def save_history(history: dict) -> None:
    """Save the history of sent articles."""
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except IOError as e:
        print(f"Warning: Could not save history file: {e}")


def cleanup_old_history(history: dict, days: int = 7) -> dict:
    """Remove articles older than specified days from history."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.isoformat()

    history["sent_articles"] = {
        k: v for k, v in history["sent_articles"].items()
        if v.get("sent_at", "") > cutoff_str
    }
    history["last_cleanup"] = datetime.now().isoformat()
    return history


def filter_duplicates(articles: list[Article], history: dict) -> list[Article]:
    """Remove articles that were already sent in previous digests."""
    new_articles = []
    sent_hashes = set(history.get("sent_articles", {}).keys())

    for article in articles:
        article_hash = get_article_hash(article)
        if article_hash not in sent_hashes:
            new_articles.append(article)
        else:
            print(f"  Skipping duplicate: {article.title[:50]}...")

    return new_articles


def mark_articles_as_sent(articles: list[Article], history: dict) -> dict:
    """Mark articles as sent in the history."""
    for article in articles:
        article_hash = get_article_hash(article)
        history["sent_articles"][article_hash] = {
            "title": article.title,
            "link": article.link,
            "source": article.source,
            "sent_at": datetime.now().isoformat()
        }
    return history


# =============================================================================
# News Fetching
# =============================================================================

def fetch_rss_feed(name: str, url: str, max_articles: int = 5) -> list[Article]:
    """Fetch articles from an RSS feed."""
    articles = []
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.now() - timedelta(days=1)

        for entry in feed.entries[:max_articles * 2]:  # Fetch extra to filter
            # Try to parse the published date
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6])

            # Filter to last 24 hours if we have a date
            if published and published < cutoff:
                continue

            # Get summary/description
            summary = ""
            if hasattr(entry, 'summary'):
                summary = entry.summary[:500]  # Truncate long summaries
            elif hasattr(entry, 'description'):
                summary = entry.description[:500]

            articles.append(Article(
                title=entry.get('title', 'No title'),
                link=entry.get('link', ''),
                summary=summary,
                source=name,
                published=published
            ))

            if len(articles) >= max_articles:
                break

    except Exception as e:
        print(f"Error fetching {name}: {e}")

    return articles


def fetch_hacker_news_top(max_articles: int = 10) -> list[Article]:
    """Fetch top stories from Hacker News API for better quality."""
    articles = []
    try:
        # Get top story IDs
        response = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=10
        )
        story_ids = response.json()[:max_articles]

        for story_id in story_ids:
            story_resp = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=10
            )
            story = story_resp.json()
            if story and story.get('title'):
                articles.append(Article(
                    title=story['title'],
                    link=story.get('url', f"https://news.ycombinator.com/item?id={story_id}"),
                    summary=f"Score: {story.get('score', 0)} | Comments: {story.get('descendants', 0)}",
                    source="Hacker News (Top)",
                    published=datetime.fromtimestamp(story.get('time', 0)) if story.get('time') else None
                ))
    except Exception as e:
        print(f"Error fetching HN API: {e}")

    return articles


def fetch_all_news() -> list[Article]:
    """Fetch news from all configured sources."""
    all_articles = []
    max_per_source = int(os.getenv('MAX_ARTICLES_PER_SOURCE', 20))

    # Fetch from RSS feeds
    for name, url in RSS_FEEDS.items():
        if name == "Hacker News":
            continue  # We'll use the API instead
        print(f"Fetching {name}...")
        articles = fetch_rss_feed(name, url, max_per_source)
        all_articles.extend(articles)
        print(f"  Got {len(articles)} articles")

    # Fetch Hacker News via API for better data
    print("Fetching Hacker News (API)...")
    hn_articles = fetch_hacker_news_top(max_per_source * 2)
    all_articles.extend(hn_articles)
    print(f"  Got {len(hn_articles)} articles")

    return all_articles


# =============================================================================
# Claude Summarization
# =============================================================================

def cleanup_old_logs(retention_days: int) -> None:
    """Delete rotated log files older than retention_days."""
    if retention_days <= 0:
        return

    log_file = Path(os.getenv("LOG_FILE", str(DEFAULT_LOG_FILE)))
    if not log_file.parent.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    base_name = log_file.name

    for path in log_file.parent.iterdir():
        if not path.is_file():
            continue
        if path.name == base_name:
            # Never delete the active log file.
            continue
        if not path.name.startswith(base_name + "."):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                pass


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _select_latest_model(models: list, family: str) -> Optional[str]:
    family_lower = family.lower()
    candidates = []
    for model in models:
        model_id = getattr(model, "id", None) or model.get("id") if isinstance(model, dict) else None
        if not model_id:
            continue
        if family_lower not in model_id.lower():
            continue
        created_at = getattr(model, "created_at", None) or model.get("created_at") if isinstance(model, dict) else None
        created_dt = _parse_datetime(created_at) if isinstance(created_at, str) else None
        candidates.append((created_dt, model_id))

    if not candidates:
        return None

    # Prefer newest created_at, fall back to lexical ID ordering.
    candidates.sort(key=lambda item: (item[0] is None, item[0], item[1]))
    return candidates[-1][1]


def resolve_model_order(client: anthropic.Anthropic) -> list[str]:
    """Resolve the model fallback order (sonnet -> opus -> haiku)."""

    use_latest = os.getenv("USE_LATEST_MODELS", "false").lower() == "true"
    refresh_days = int(os.getenv("MODEL_REFRESH_DAYS", "7"))
    default_models = {
        "sonnet": "claude-sonnet-4-5",
        "opus": "claude-opus-4-6",
        "haiku": "claude-haiku-4-5",
    }

    cache = {}
    if MODEL_CACHE_FILE.exists():
        try:
            cache = json.loads(MODEL_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}

    cached_models = cache.get("models", {}) if isinstance(cache, dict) else {}
    last_checked = _parse_datetime(cache.get("last_checked", "")) if isinstance(cache, dict) else None

    models_to_use = default_models.copy()

    if use_latest:
        cache_fresh = last_checked and (datetime.utcnow() - last_checked) <= timedelta(days=refresh_days)
        if cache_fresh and cached_models:
            models_to_use.update({k: v for k, v in cached_models.items() if v})
        else:
            try:
                response = client.models.list()
                model_list = getattr(response, "data", response)
                resolved = {
                    "sonnet": _select_latest_model(model_list, "sonnet"),
                    "opus": _select_latest_model(model_list, "opus"),
                    "haiku": _select_latest_model(model_list, "haiku"),
                }
                for key, value in resolved.items():
                    if value:
                        models_to_use[key] = value

                MODEL_CACHE_FILE.write_text(
                    json.dumps(
                        {"last_checked": datetime.utcnow().isoformat(), "models": models_to_use},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception as e:
                print(f"⚠️ Failed to refresh Claude model list, using cached/default models: {e}")
                if cached_models:
                    models_to_use.update({k: v for k, v in cached_models.items() if v})

    # Allow a manual override for the primary model, but keep fallbacks.
    primary_override = os.getenv("DIGEST_MODEL", "").strip()
    order = [models_to_use["sonnet"], models_to_use["opus"], models_to_use["haiku"]]
    if primary_override:
        order = [primary_override] + [m for m in order if m != primary_override]

    # De-duplicate while preserving order.
    deduped = []
    for model_id in order:
        if model_id and model_id not in deduped:
            deduped.append(model_id)

    return deduped


def summarize_with_claude(articles: list[Article]) -> str:
    """Use Claude to create a personalized digest summary."""

    client = anthropic.Anthropic()
    model_order = resolve_model_order(client)
    if model_order:
        print(f"Claude model order: {', '.join(model_order)}")

    # Format articles for Claude
    articles_text = ""
    for i, article in enumerate(articles, 1):
        articles_text += f"""
---
Article {i}:
Source: {article.source}
Title: {article.title}
Link: {article.link}
Summary: {article.summary}
"""

    prompt = f"""You are creating a personalized daily news digest for Kenne, a product executive currently exploring
new opportunities in the tech/AI space. He wants to stay informed on industry trends AND spot potential
job opportunities at innovative companies.

## READER PROFILE
- Product executive with technical background
- Actively looking for next role at innovative tech companies
- Particularly interested in AI/ML, robotics, and emerging tech
- Owns BMW F80 M3 and Toyota GR86 (relevant for automotive content)
- Interested in health optimization and longevity

## INTERESTS (in priority order):

{INTERESTS}

## TODAY'S ARTICLES (pre-filtered to last 24 hours, duplicates from previous days removed):

{articles_text}

---

## INSTRUCTIONS

Create a well-organized, engaging daily digest email with these sections IN THIS ORDER:

---

### 🔥 TOP PRIORITY (Always include - most important section)
The 4-6 most significant stories from these priority areas ONLY:
- AI/ML breakthroughs, tools, and business news
- Job market signals and opportunities
- Robotics + AI convergence
- Social + AI + Web3 convergence (especially intersections of all three)
- Anthropic news (always include if present)

**ADDITIONALLY** (do NOT replace any of the above — add as extra items):
- Project Liberty or Frequency news (Kenne's former employer). Scan ALL articles from
  EVERY source — if ANY article mentions "Project Liberty", "Frequency blockchain",
  "Frequency.xyz", or "DSNP", ALWAYS include it as an additional Top Priority item.
  Token launch / mainnet news is CRITICAL — flag prominently.
- Tala, Luma AI, or Angler AI news (Kenne's personal interest companies). Scan ALL articles
  from EVERY source — if ANY article mentions "Tala" (fintech/credit), "Luma AI", "Luma Labs",
  "Dream Machine", "Angler AI", or "getangler", ALWAYS include as additional Top Priority items.
  Funding rounds, product launches, and partnerships are CRITICAL — flag prominently.

For each article:
- 2-3 sentence summary
- Why it matters to a product executive
- Include the link

---

### 💼 JOB RADAR (Always include if ANY relevant signals exist)
Actively scan for and flag:
- **Funding rounds**: Company, amount, stage - signals hiring
- **Hiring announcements**: Especially product, AI, leadership roles
- **Anthropic news**: ANY news about Anthropic (company Kenne is particularly interested in)
- **Growing startups**: AI, robotics, climate tech companies expanding
- **Executive moves**: Could signal opportunities or industry shifts

Format as a quick-scan list with company name bolded and 1-line context.

---

### 🏢 COMPANIES TO WATCH (Include if 2+ interesting companies mentioned)
Spotlight on startups or companies doing interesting things:
- Company name and what they do
- Why they're notable (funding, tech, growth)
- Link to the article
This helps track potential employers or industry movers.

---

### 🤖 AI & ROBOTICS (High priority section)
- New AI tools, frameworks, models
- Business adoption and trends
- Robotics breakthroughs
- Developer resources and coding tips

---

### 🧬 HEALTH & LONGEVITY (If relevant articles exist)
- GLP-1 research (Ozempic, Mounjaro, etc.)
- Longevity science
- Health optimization tech
- Supplement science with actual evidence

---

### 🚗 AUTOMOTIVE TECH (If relevant articles exist)
- Chinese EV innovations (BYD, NIO, Xpeng)
- Self-driving/ADAS developments
- BMW M / Toyota GR platform news
- Battery and range breakthroughs

---

### 🌐 SOCIAL & WEB3 (Include if relevant articles exist - HIGH INTEREST)
- Social platform developments, policy changes, new features
- Decentralized social platforms (Bluesky, Farcaster, Lens, Frequency)
- AI + social intersection (AI-powered features, content detection, recommendation systems)
- Web3 + social intersection (decentralized identity, on-chain social graphs)
- Web3 regulatory clarity and real use cases
- NO crypto price speculation

---

### 🌍 CLIMATE TECH (If relevant articles exist)
- Climate tech solutions
- Marine conservation technology

---

### 📊 FINANCE & FINTECH RADAR (Include if relevant - quick hits format)
3-5 one-liner quick hits from the global finance, fintech, crypto industry, and stock landscape:
- Major M&A, funding rounds, IPOs in fintech/crypto
- Market-moving macro signals (rate decisions, earnings surprises, sector rotations)
- Crypto industry milestones (exchange news, DeFi, institutional adoption)
- Fintech product launches and neobank developments
- Stock market catalysts worth noting
Format as punchy one-liners with source links. NO price predictions or day-trading tips.

---

### ⚖️ REGULATORY & LEGAL RADAR (Include if relevant - quick hits format)
2-4 one-liner quick hits on the legal and regulatory landscape across tech sectors:
- AI governance and regulation (executive orders, EU AI Act, liability frameworks)
- Crypto/fintech enforcement and rulemaking (SEC, CFTC, stablecoin legislation)
- Tech antitrust actions and major M&A approvals/blocks
- Automotive/EV/robotics policy (safety mandates, tariffs, labor impact)
- Global regulatory divergence (US vs EU vs Asia approaches)
Format as punchy one-liners with source links. NO partisan framing.

---

### 📺 WORTH WATCHING (If relevant - entertainment/space/science)
- Award-winning films/TV
- Must-see sci-fi
- Space exploration milestones
- Major scientific discoveries

---

### ⚡ QUICK HITS (Optional, 3-5 items max)
One-liner mentions of interesting but non-essential articles

## STRICT FILTERING RULES - MUST FOLLOW

ALWAYS EXCLUDE:
- Celebrity gossip, relationship drama, tabloid content
- Crypto price predictions, "to the moon" hype, memecoin news
- Partisan political opinion pieces
- Clickbait and sensationalized headlines
- Promotional content disguised as news
- Minor incremental updates that aren't newsworthy

QUALITY CONTROL:
- When multiple sources cover the same story, pick the BEST one
- Skip sections entirely if fewer than 2 quality articles
- Total digest: 20-30 articles maximum
- Prioritize actionable intelligence over general news

## OUTPUT FORMAT - CRITICAL

You MUST return ONLY valid HTML (no markdown). Use this exact structure:

```html
<h1>🗞️ Kenne's Daily Digest</h1>
<p>Good morning! Here's your personalized news for [DATE].</p>

<h2>🔥 Top Priority</h2>
<ul>
  <li>
    <strong><a href="URL">Article Title</a></strong> (Source)<br>
    Summary of the article and why it matters.
  </li>
</ul>

<h2>💼 Job Radar</h2>
<ul>
  <li><strong>Company Name</strong> - What happened and why it's relevant</li>
</ul>

<!-- Continue with other sections using same pattern -->
```

HTML RULES:
- Use <h2> for section headers (with emoji)
- Use <ul> and <li> for article lists
- Use <strong> for emphasis
- Use <a href="URL">Title</a> for links
- Use <br> for line breaks within list items
- Use <p> for paragraphs
- Use <hr> to separate major sections if needed
- Do NOT use markdown syntax (no ##, no **, no - bullets)
- Do NOT wrap in ```html code blocks - return raw HTML only
"""

    max_retries = 3
    base_wait = 3
    max_wait = 20
    last_error = None

    for model in model_order:
        for attempt in range(max_retries):
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                last_error = None
                print(f"✓ Claude response generated with model: {model}")
                break
            except (anthropic.APIStatusError,) as e:
                if e.status_code in (429, 529):
                    last_error = e
                    if attempt < max_retries - 1:
                        wait = min(max_wait, base_wait * (2 ** attempt))
                        jitter = random.uniform(0.7, 1.3)
                        wait *= jitter
                        print(
                            f"Claude API returned {e.status_code} for {model}, "
                            f"retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(wait)
                    else:
                        print(
                            f"Claude API returned {e.status_code} for {model} after {max_retries} attempts, "
                            "trying next fallback model..."
                        )
                        break
                else:
                    raise

        if last_error is None:
            break

    if last_error is not None:
        raise last_error

    html_content = message.content[0].text

    # Clean up any markdown that slipped through
    html_content = clean_markdown_to_html(html_content)

    return html_content


def clean_markdown_to_html(content: str) -> str:
    """Convert any remaining markdown syntax to HTML and clean up formatting."""
    import re

    # Remove code block wrappers if present
    content = re.sub(r'^```html\s*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'\n?```\s*$', '', content, flags=re.MULTILINE)
    content = content.strip()

    # Check if content already looks like proper HTML (starts with HTML tag)
    if content.startswith('<h1>') or content.startswith('<div') or content.startswith('<!'):
        # Already HTML, just do minimal cleanup
        # Convert any remaining markdown bold
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        # Convert any remaining markdown links
        content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', content)
        return content

    # Content appears to be markdown or mixed - do full conversion

    # Convert markdown headers to HTML
    content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=re.MULTILINE)
    content = re.sub(r'^## (.+)$', r'<h2>\1</h2>', content, flags=re.MULTILINE)
    content = re.sub(r'^# (.+)$', r'<h1>\1</h1>', content, flags=re.MULTILINE)

    # Convert markdown bold **text** to <strong>text</strong>
    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)

    # Convert markdown italic *text* to <em>text</em> (but not inside URLs)
    content = re.sub(r'(?<![:/])\*([^*]+)\*(?![/])', r'<em>\1</em>', content)

    # Convert markdown links [text](url) to <a href="url">text</a>
    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', content)

    # Convert markdown horizontal rules
    content = re.sub(r'^---+$', '<hr>', content, flags=re.MULTILINE)

    # Convert markdown bullet points to HTML list items
    lines = content.split('\n')
    result = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Check if this is a markdown bullet point (starts with - or *)
        is_bullet = (stripped.startswith('- ') or stripped.startswith('* ')) and not stripped.startswith('---')

        if is_bullet:
            if not in_list:
                result.append('<ul>')
                in_list = True
            # Remove the bullet marker and wrap in li
            item_content = stripped[2:]
            result.append(f'<li>{item_content}</li>')
        else:
            if in_list and stripped and not stripped.startswith('<li'):
                result.append('</ul>')
                in_list = False

            # Handle the line
            if stripped:
                # Don't wrap lines that are already HTML tags
                if stripped.startswith('<') or stripped.endswith('>'):
                    result.append(line)
                # Don't wrap lines that are continuations of list items
                elif in_list:
                    result.append(line)
                # Wrap plain text in paragraphs
                else:
                    result.append(f'<p>{stripped}</p>')
            else:
                result.append(line)

    if in_list:
        result.append('</ul>')

    return '\n'.join(result)


def extract_top_topics(html_content: str) -> list[str]:
    """Extract top topic titles from the digest HTML for the podcast email section.

    Pulls article titles from the Top Priority section, falling back to any
    ``<strong><a>`` links found in the first ``<ul>`` block.

    Returns:
        List of up to 5 topic title strings.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    topics = []

    # Look for Top Priority section (first h2 typically)
    for h2 in soup.find_all("h2"):
        if "top" in h2.get_text().lower() or "priority" in h2.get_text().lower():
            # Get the <ul> that follows this h2
            ul = h2.find_next_sibling("ul")
            if ul:
                for li in ul.find_all("li"):
                    link = li.find("a")
                    if link:
                        topics.append(link.get_text(strip=True))
                    elif li.find("strong"):
                        topics.append(li.find("strong").get_text(strip=True))
            break

    # Fallback: grab first few linked titles from any section
    if not topics:
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(strip=True)
            if text and len(text) > 10:
                topics.append(text)
            if len(topics) >= 5:
                break

    return topics[:5]


# =============================================================================
# Email Sending
# =============================================================================

def send_error_email(error_type: str, error_message: str, full_traceback: str = "") -> bool:
    """Send an error notification email."""

    sender_email = os.getenv('GMAIL_ADDRESS')
    app_password = os.getenv('GMAIL_APP_PASSWORD')
    recipient_str = os.getenv('RECIPIENT_EMAIL')

    if not all([sender_email, app_password, recipient_str]):
        print("Error: Missing email configuration - cannot send error notification")
        return False

    recipients = [r.strip() for r in recipient_str.split(',') if r.strip()]

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"⚠️ Kenne's News Digest Failed - {error_type} - {datetime.now().strftime('%A, %B %d, %Y')}"
    msg['From'] = f"News Digest <{sender_email}>"
    msg['To'] = ', '.join(recipients)

    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               line-height: 1.6; color: #333; max-width: 700px; margin: 0 auto; padding: 20px; }}
        .error-box {{ background: #fee; border: 1px solid #c00; border-radius: 5px; padding: 15px; margin: 20px 0; }}
        .error-title {{ color: #c00; margin: 0 0 10px 0; }}
        pre {{ background: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto; font-size: 12px; }}
        .action {{ background: #fffbe6; border: 1px solid #ffe58f; border-radius: 5px; padding: 15px; margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>⚠️ News Digest Error</h1>
    <p>Your daily news digest failed to generate on {datetime.now().strftime('%Y-%m-%d at %H:%M')}.</p>

    <div class="error-box">
        <h3 class="error-title">{error_type}</h3>
        <p>{error_message}</p>
    </div>

    {"<div class='action'><h3>Suggested Action</h3><p>Check your Anthropic API credits at <a href='https://console.anthropic.com/'>console.anthropic.com</a></p></div>" if "credit" in error_message.lower() or "rate" in error_message.lower() or "billing" in error_message.lower() else ""}

    {f"<h3>Full Error Details</h3><pre>{full_traceback}</pre>" if full_traceback else ""}

    <hr style="margin-top: 40px; border: none; border-top: 1px solid #ddd;">
    <p style="color: #666; font-size: 0.85em;">
        This is an automated error notification from your News Digest bot.
    </p>
</body>
</html>
"""

    plain_text = f"News Digest Error: {error_type}\n\n{error_message}\n\n{full_traceback}"

    msg.attach(MIMEText(plain_text, 'plain'))
    msg.attach(MIMEText(html_content, 'html'))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipients, msg.as_string())
        print(f"✓ Error notification sent to {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"✗ Failed to send error notification: {e}")
        return False


def send_email(html_content: str, podcast_url: str | None = None, top_topics: list[str] | None = None) -> bool:
    """Send the digest email via Gmail SMTP."""

    sender_email = os.getenv('GMAIL_ADDRESS')
    app_password = os.getenv('GMAIL_APP_PASSWORD')
    recipient_str = os.getenv('RECIPIENT_EMAIL')

    if not all([sender_email, app_password, recipient_str]):
        print("Error: Missing email configuration in .env file")
        return False

    recipients = [r.strip() for r in recipient_str.split(',') if r.strip()]

    # Create message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"📰 Kenne's Daily News Digest - {datetime.now().strftime('%A, %B %d, %Y')}"
    msg['From'] = f"News Digest <{sender_email}>"
    msg['To'] = ', '.join(recipients)

    # Create plain text version (fallback)
    plain_text = "Your daily news digest is ready. Please view this email in an HTML-capable client."

    # Build podcast section HTML (before template wrapping)
    podcast_section = ""
    if podcast_url:
        topics_html = ""
        if top_topics:
            topic_items = "".join(f"<li>{topic}</li>" for topic in top_topics)
            topics_html = (
                '<p style="margin-top: 12px; font-weight: 600; color: #4a5568;">'
                "Today's top topics:</p><ul>" + topic_items + "</ul>"
            )
        podcast_section = (
            '<div style="margin-top: 32px; padding: 20px; background: #f0f7ff; '
            'border-radius: 10px; border: 1px solid #bee3f8;">'
            '<h2 style="color: #2b6cb0; margin-top: 0;">🎧 Daily News Podcast</h2>'
            "<p style=\"margin: 8px 0;\">Listen to today's digest as a podcast with hosts Alex &amp; Sam:</p>"
            f'<p><a href="{podcast_url}" style="display: inline-block; padding: 10px 20px; '
            "background: #3182ce; color: #ffffff; border-radius: 6px; text-decoration: none; "
            'font-weight: 600;">Listen Now</a></p>'
            '<p style="font-size: 13px; color: #718096;">Available anywhere — log in with your Audiobookshelf account.</p>'
            + topics_html
            + "</div>"
        )

    # Wrap HTML content in a styled email template
    if not html_content.strip().startswith('<!DOCTYPE') and not html_content.strip().startswith('<html'):
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.7;
            color: #2d3748;
            max-width: 680px;
            margin: 0 auto;
            padding: 20px;
            background-color: #ffffff;
        }}
        h1 {{
            color: #1a202c;
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
            border-bottom: 3px solid #3182ce;
            padding-bottom: 12px;
        }}
        h2 {{
            color: #2b6cb0;
            font-size: 20px;
            font-weight: 600;
            margin-top: 32px;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #e2e8f0;
        }}
        h3 {{
            color: #4a5568;
            font-size: 16px;
            font-weight: 600;
            margin-top: 20px;
            margin-bottom: 12px;
        }}
        p {{
            margin: 12px 0;
            color: #4a5568;
        }}
        a {{
            color: #3182ce;
            text-decoration: none;
            font-weight: 500;
        }}
        a:hover {{
            text-decoration: underline;
            color: #2c5282;
        }}
        ul {{
            padding-left: 0;
            list-style: none;
            margin: 16px 0;
        }}
        li {{
            margin: 16px 0;
            padding: 14px 16px;
            background: #f7fafc;
            border-radius: 8px;
            border-left: 4px solid #3182ce;
        }}
        li strong {{
            color: #1a202c;
        }}
        li a {{
            font-size: 15px;
        }}
        hr {{
            border: none;
            border-top: 1px solid #e2e8f0;
            margin: 28px 0;
        }}
        .intro {{
            font-size: 16px;
            color: #718096;
            margin-bottom: 24px;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            color: #a0aec0;
            font-size: 13px;
        }}
        .source {{
            color: #718096;
            font-size: 13px;
            font-weight: normal;
        }}
        /* Special styling for job radar section */
        h2:has(+ ul li strong) {{
            color: #2f855a;
        }}
    </style>
</head>
<body>
{html_content}
{podcast_section}
<div class="footer">
    <p>Generated on {datetime.now().strftime('%A, %B %d, %Y at %H:%M')} by your News Digest bot.</p>
    <p>Powered by Claude AI • Filtering {len(RSS_FEEDS)} sources for the news that matters to you.</p>
</div>
</body>
</html>
"""

    msg.attach(MIMEText(plain_text, 'plain'))
    msg.attach(MIMEText(html_content, 'html'))

    # Send via Gmail SMTP
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipients, msg.as_string())
        print(f"✓ Email sent successfully to {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"✗ Failed to send email: {e}")
        return False


# =============================================================================
# Main
# =============================================================================

def main():
    """Main function to generate and send the daily digest."""
    print(f"\n{'='*60}")
    print(f"Daily News Digest - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    try:
        cleanup_old_logs(int(os.getenv("LOG_RETENTION_DAYS", "30")))

        # Load history for duplicate detection
        print("📂 Loading article history...")
        history = load_history()

        # Cleanup old history entries (older than 7 days)
        history = cleanup_old_history(history, days=7)
        print(f"  History contains {len(history.get('sent_articles', {}))} recent articles\n")

        # Step 1: Fetch news
        print("📥 Fetching news from sources...")
        articles = fetch_all_news()
        print(f"\n✓ Fetched {len(articles)} total articles")

        # Step 2: Filter out duplicates from previous digests
        print("\n🔍 Filtering duplicates...")
        articles = filter_duplicates(articles, history)
        print(f"✓ {len(articles)} new articles after filtering\n")

        if not articles:
            send_error_email(
                "No New Articles Found",
                "The digest could not find any NEW articles from the configured sources. "
                "All fetched articles were already sent in previous digests. "
                "This might be normal on slow news days, or there could be a feed issue."
            )
            print("No new articles found. Error notification sent.")
            sys.exit(1)

        # Step 2: Summarize with Claude
        print("🤖 Generating digest with Claude...")
        try:
            digest_html = summarize_with_claude(articles)
        except anthropic.RateLimitError as e:
            send_error_email(
                "API Rate Limit Exceeded",
                "The Claude API rate limit has been exceeded. This usually means you've "
                "hit your usage cap or need to wait before making more requests.",
                str(e)
            )
            print(f"Rate limit error: {e}")
            sys.exit(1)
        except anthropic.AuthenticationError as e:
            send_error_email(
                "API Authentication Failed",
                "The Anthropic API key is invalid or has been revoked. Please check your "
                "ANTHROPIC_API_KEY in the .env file.",
                str(e)
            )
            print(f"Authentication error: {e}")
            sys.exit(1)
        except anthropic.BadRequestError as e:
            error_msg = str(e)
            if "credit" in error_msg.lower() or "billing" in error_msg.lower():
                send_error_email(
                    "API Credits Exhausted",
                    "Your Anthropic API credits have run out. Please add more credits at "
                    "console.anthropic.com to continue receiving digests.",
                    error_msg
                )
            else:
                send_error_email("API Request Error", error_msg, traceback.format_exc())
            print(f"API error: {e}")
            sys.exit(1)
        except anthropic.APIError as e:
            error_message = str(e)
            error_type = "Claude API Error"
            if isinstance(e, anthropic.APIStatusError) and e.status_code in (429, 529):
                if e.status_code == 529 or "overloaded" in error_message.lower():
                    error_type = "Claude API Overloaded"
                    error_message = (
                        "Claude is overloaded (HTTP 529). The provider could not handle the request "
                        "after retries and fallbacks."
                    )
                else:
                    error_type = "Claude API Rate Limited"
                    error_message = (
                        "Claude returned HTTP 429 (rate limited). The provider could not handle the request "
                        "after retries and fallbacks."
                    )

            send_error_email(error_type, f"An error occurred while calling the Claude API: {error_message}",
                            traceback.format_exc())
            print(f"API error: {e}")
            sys.exit(1)

        print("✓ Digest generated\n")

        # Step 3: Podcast Audio Pipeline
        podcast_url = None
        top_topics = []
        test_mode = os.getenv("PODCAST_TEST_MODE", "false").lower() == "true"
        audio_output_dir = os.getenv("AUDIO_OUTPUT_DIR", "")

        if audio_output_dir:
            try:
                print("🎙️ Generating podcast audio...")
                digest_text = extract_text_from_html(digest_html)
                top_topics = extract_top_topics(digest_html)

                print("  Generating podcast script via local LLM...")
                script = generate_podcast_script(digest_text, test_mode)
                print("  ✓ Script generated")

                segments = parse_script(script)
                print(f"  ✓ Parsed {len(segments)} dialogue segments")

                audio_path = generate_audio(segments, audio_output_dir, test_mode)
                print(f"  ✓ Audio saved to {audio_path}")

                cleanup_old_audio(audio_output_dir)

                # Trigger Audiobookshelf library scan
                abs_url = os.getenv("AUDIOBOOKSHELF_URL", "")
                api_key = os.getenv("AUDIOBOOKSHELF_API_KEY", "")
                library_id = os.getenv("AUDIOBOOKSHELF_LIBRARY_ID", "")

                if all([abs_url, api_key, library_id]):
                    trigger_library_scan(abs_url, api_key, library_id)
                    podcast_url = get_podcast_url(abs_url)
                else:
                    print("  Skipping Audiobookshelf scan (not configured)")

                print("✓ Podcast pipeline complete\n")
            except Exception as e:
                print(f"⚠️ Podcast generation failed: {e}")
                send_error_email("Podcast Generation Failed", str(e), traceback.format_exc())
                # Continue — email digest still sends without audio
        else:
            print("⏭️ Podcast pipeline skipped (AUDIO_OUTPUT_DIR not set)\n")

        # Step 4: Send email
        print("📧 Sending email...")
        success = send_email(digest_html, podcast_url=podcast_url, top_topics=top_topics)

        if success:
            # Mark articles as sent so they won't be included tomorrow
            print("💾 Saving article history...")
            history = mark_articles_as_sent(articles, history)
            save_history(history)
            print(f"✓ Marked {len(articles)} articles as sent\n")

            print(f"{'='*60}")
            print("✓ Daily digest completed successfully!")
            print(f"{'='*60}\n")
        else:
            send_error_email(
                "Email Sending Failed",
                "The digest was generated but could not be sent. Check your Gmail "
                "configuration in the .env file (GMAIL_ADDRESS, GMAIL_APP_PASSWORD)."
            )
            sys.exit(1)

    except Exception as e:
        # Catch-all for unexpected errors
        send_error_email(
            "Unexpected Error",
            f"An unexpected error occurred: {e}",
            traceback.format_exc()
        )
        print(f"Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
