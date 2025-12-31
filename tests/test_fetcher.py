from fetcher import extract_urls
from fetcher import github_raw_url
from fetcher import arxiv_meta, fetch_url_content


ARXIV_HTML = """
<html>
  <head>
    <meta name="citation_title" content="Thought Communication: Mind-to-Mind Multi-Agent Collaboration">
    <meta name="citation_abstract" content="Natural language has long enabled human cooperation, but its lossy, ambiguous, and indirect nature limits the potential of collective intelligence.">
  </head>
  <body>
    <h1 class="title mathjax">Title: Thought Communication: Mind-to-Mind Multi-Agent Collaboration</h1>
    <blockquote class="abstract mathjax">
      <span class="descriptor">Abstract:</span>
      Natural language has long enabled human cooperation, but its lossy, ambiguous, and indirect nature limits the potential of collective intelligence.
    </blockquote>
  </body>
</html>
"""


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


def test_arxiv_meta_handles_non_arxiv():
    assert arxiv_meta("https://example.com") is None


def test_arxiv_meta_extracts_title_and_abstract(monkeypatch):
    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        return DummyResponse(ARXIV_HTML)

    monkeypatch.setattr("fetcher.requests.get", fake_get)

    title, abstract = arxiv_meta("https://arxiv.org/abs/1234.56789")
    assert "Thought Communication" in title
    assert "collective intelligence" in abstract


def test_fetch_url_content_arxiv_includes_title(monkeypatch):
    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        return DummyResponse(ARXIV_HTML)

    class DummySession:
        def get(self, *args, **kwargs):
            return DummyResponse(ARXIV_HTML)

    monkeypatch.setattr("fetcher.requests.get", fake_get)
    monkeypatch.setattr("fetcher.requests.Session", lambda: DummySession())

    title, content, method = fetch_url_content("https://arxiv.org/abs/1234.56789")
    assert method == "arxiv_meta"
    assert title in content
    assert "collective intelligence" in content
