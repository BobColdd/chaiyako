import re
from datetime import datetime, timezone
from calendar import timegm

from flask import Blueprint, render_template
from flask_login import login_required
import feedparser

news_bp = Blueprint("news", __name__)

# Each feed is tagged with a category so the page can offer filter tabs.
FEEDS = [
    {
        "url": "https://news.google.com/rss/search?q=Kenya+tea+prices+OR+KTDA+auction&hl=en-KE&gl=KE&ceid=KE:en",
        "category": "Market & Prices",
    },
    {
        "url": "https://news.google.com/rss/search?q=Kericho+tea+farmers&hl=en-KE&gl=KE&ceid=KE:en",
        "category": "Kericho & Local",
    },
    {
        "url": "https://news.google.com/rss/search?q=tea+farming+East+Africa&hl=en-KE&gl=KE&ceid=KE:en",
        "category": "Regional Farming",
    },
    {
        "url": "https://news.google.com/rss/search?q=global+tea+market+OR+tea+export&hl=en&gl=KE&ceid=KE:en",
        "category": "Global Tea Market",
    },
]

IMG_TAG_RE = re.compile(r'<img[^>]+src="([^"]+)"')


def _extract_image(entry):
    """Best-effort thumbnail extraction from an RSS entry."""
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if media and isinstance(media, list) and media[0].get("url"):
        return media[0]["url"]

    for enc in entry.get("links", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("href")

    summary = entry.get("summary", "")
    match = IMG_TAG_RE.search(summary)
    if match:
        return match.group(1)

    return None


def _time_ago(entry):
    parsed = entry.get("published_parsed")
    if not parsed:
        return entry.get("published", "")
    try:
        published_dt = datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - published_dt
        seconds = delta.total_seconds()
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        if seconds < 604800:
            return f"{int(seconds // 86400)}d ago"
        return published_dt.strftime("%d %b %Y")
    except Exception:
        return entry.get("published", "")


@news_bp.route("/news")
@login_required
def tea_news():
    articles = []
    for feed_def in FEEDS:
        try:
            feed = feedparser.parse(feed_def["url"])
            for entry in feed.entries[:8]:
                articles.append({
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", "#"),
                    "time_ago": _time_ago(entry),
                    "source": entry.get("source", {}).get("title", "News"),
                    "category": feed_def["category"],
                    "image": _extract_image(entry),
                    "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:160],
                })
        except Exception:
            continue

    # de-duplicate by title, keep first occurrence
    seen, unique_articles = set(), []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique_articles.append(a)

    unique_articles = unique_articles[:32]
    categories = ["All"] + sorted({f["category"] for f in FEEDS})
    featured = unique_articles[0] if unique_articles else None
    rest = unique_articles[1:] if unique_articles else []

    return render_template(
        "news.html",
        featured=featured,
        articles=rest,
        categories=categories,
    )
