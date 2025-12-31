import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import feedparser


def load_feed_urls(path: Path = Path("rss_feeds.txt")) -> List[str]:
    if not path.exists():
        return []
    lines = [ln.strip() for ln in path.read_text().splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        try:
            return datetime.strptime(dt_str, "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            return None


def fetch_feed(url: str, per_feed_limit: int = 3) -> List[Dict]:
    parsed = feedparser.parse(url)
    items: List[Dict] = []
    for entry in parsed.entries[: per_feed_limit * 3]:  # grab extra before filtering
        title = entry.get("title") or "(no title)"
        link = entry.get("link")
        published = (
            entry.get("published")
            or entry.get("updated")
            or entry.get("pubDate")
        )
        published_dt = _parse_datetime(published)
        items.append(
            {
                "title": title,
                "link": link,
                "published": published_dt,
                "feed": url,
                "summary": entry.get("summary", ""),
            }
        )
    # sort newest first and cap
    items.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:per_feed_limit]


def fetch_all(feeds: Iterable[str], per_feed_limit: int = 3, total_limit: int = 20) -> List[Dict]:
    all_items: List[Dict] = []
    seen_hashes = set()
    for feed in feeds:
        for item in fetch_feed(feed, per_feed_limit=per_feed_limit):
            h = hashlib.md5(f"{item.get('title')}|{item.get('link')}".encode("utf-8")).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            all_items.append(item)
            if len(all_items) >= total_limit:
                return all_items
    return all_items


def format_digest(items: List[Dict], keywords: Optional[List[str]] = None) -> str:
    if not items:
        return "[rss] no items found."
    lines = ["Daily RSS brief:"]
    for idx, item in enumerate(items, 1):
        title = item.get("title") or "(no title)"
        link = item.get("link") or ""
        feed = item.get("feed", "")
        published = item.get("published")
        published_str = published.isoformat() if isinstance(published, datetime) else ""
        lines.append(f"- [{idx}] {title} ({feed}) {published_str} {link}")
    if keywords:
        lines.append(f"\nFocus keywords: {', '.join(keywords)}")
    return "\n".join(lines)
