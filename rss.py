import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import feedparser


def load_feed_urls(path: Path = Path("rss_feeds.txt")) -> List[str]:
    """Load RSS feed URLs from a text file.
    
    Args:
        path: Path to the file containing feed URLs (one per line).
              Lines starting with # are treated as comments.
              
    Returns:
        List of feed URL strings.
    """
    if not path.exists():
        return []
    lines = [ln.strip() for ln in path.read_text().splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse datetime string from RSS feeds.
    
    Tries ISO format first, then RFC 822 format.
    
    Args:
        dt_str: Datetime string to parse.
        
    Returns:
        Parsed datetime with UTC timezone or None if parsing fails.
    """
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
    except Exception:
        try:
            dt = datetime.strptime(dt_str, "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_feed(url: str, per_feed_limit: int = 3) -> List[Dict]:
    """Fetch and parse RSS feed entries from a URL.
    
    Args:
        url: RSS feed URL.
        per_feed_limit: Maximum number of items to return (default: 3).
        
    Returns:
        List of feed items sorted by publication date (newest first),
        each item containing title, link, published, feed, and summary.
    """
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


def fetch_all(feeds: Iterable[str], per_feed_limit: int = 3, total_limit: int = 20, exclude_hashes: Optional[set] = None) -> List[Dict]:
    """Fetch items from multiple RSS feeds with deduplication.
    
    Args:
        feeds: Iterable of feed URLs.
        per_feed_limit: Maximum items per feed (default: 3).
        total_limit: Maximum total items across all feeds (default: 20).
        exclude_hashes: Set of item hashes to exclude (already processed items).
        
    Returns:
        Deduplicated list of feed items from all feeds, capped at total_limit.
    """
    all_items: List[Dict] = []
    seen_hashes = exclude_hashes or set()
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
    """Format feed items into a human-readable daily digest.
    
    Args:
        items: List of feed items to format.
        keywords: Optional list of focus keywords to append.
        
    Returns:
        Formatted digest string with numbered items and metadata.
    """
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
