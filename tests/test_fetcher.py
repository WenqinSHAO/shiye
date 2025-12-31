from fetcher import extract_urls
from fetcher import github_raw_url


def test_extract_urls_finds_multiple():
    text = "check https://example.com and also http://test.com/page"
    urls = extract_urls(text)
    assert "https://example.com" in urls
    assert "http://test.com/page" in urls


def test_github_raw_url_repo_root():
    raw = github_raw_url("https://github.com/user/repo")
    assert raw == "https://raw.githubusercontent.com/user/repo/HEAD/README.md"


def test_github_raw_url_blob():
    raw = github_raw_url("https://github.com/user/repo/blob/main/docs/index.md")
    assert raw == "https://raw.githubusercontent.com/user/repo/main/docs/index.md"
