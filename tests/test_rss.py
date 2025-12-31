from datetime import datetime, timezone

from rss import format_digest


def test_format_digest_includes_titles_and_links():
    items = [
        {
            "title": "Post A",
            "link": "https://example.com/a",
            "feed": "feed1",
            "published": datetime(2024, 1, 1, tzinfo=timezone.utc),
        },
        {
            "title": "Post B",
            "link": "https://example.com/b",
            "feed": "feed2",
            "published": datetime(2024, 1, 2, tzinfo=timezone.utc),
        },
    ]
    digest = format_digest(items, keywords=["AI", "LLM"])
    assert "Post A" in digest and "https://example.com/a" in digest
    assert "Post B" in digest and "https://example.com/b" in digest
    assert "AI" in digest and "LLM" in digest
