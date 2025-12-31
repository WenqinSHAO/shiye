from fetcher import extract_urls


def test_extract_urls_finds_multiple():
    text = "check https://example.com and also http://test.com/page"
    urls = extract_urls(text)
    assert "https://example.com" in urls
    assert "http://test.com/page" in urls
