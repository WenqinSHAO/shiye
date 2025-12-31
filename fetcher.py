import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document
from lxml import html


URL_REGEX = re.compile(r"https?://\S+")


def extract_urls(text: str) -> List[str]:
    return URL_REGEX.findall(text or "")


def github_raw_url(url: str) -> Optional[str]:
    """Map common GitHub URLs to raw content URLs (repo README or blob)."""
    parsed = urlparse(url)
    if parsed.netloc not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo, *rest = parts
    # repo root: fetch README.md from HEAD
    if not rest:
        return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    # blob path: /owner/repo/blob/branch/path
    if rest and rest[0] == "blob" and len(rest) >= 3:
        branch = rest[1]
        file_path = "/".join(rest[2:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
    # tree path: best effort; fetch README.md within tree
    if rest and rest[0] == "tree" and len(rest) >= 2:
        branch = rest[1]
        subpath = "/".join(rest[2:]) if len(rest) > 2 else ""
        if subpath:
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{subpath}/README.md"
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
    return None


def arxiv_meta(url: str) -> Optional[Tuple[str, str]]:
    parsed = urlparse(url)
    if parsed.netloc not in ("arxiv.org", "www.arxiv.org"):
        return None
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "shiye/0.1"})
        resp.raise_for_status()
    except Exception:
        return None
    tree = html.fromstring(resp.text)
    title_nodes = tree.xpath("//meta[@name='citation_title']/@content")
    abstract_nodes = tree.xpath("//meta[@name='citation_abstract']/@content")
    if not abstract_nodes:
        abstract_nodes = tree.xpath("//blockquote[contains(@class,'abstract')]//text()")
    if not title_nodes:
        # h1 title text may be split; join and strip descriptor
        h1 = tree.xpath("//h1[contains(@class,'title')]//text()")
        if h1:
            cleaned = "".join(h1).replace("Title:", "").strip()
            if cleaned:
                title_nodes = [cleaned]
    title = title_nodes[0].strip() if title_nodes else None
    abstract = None
    if abstract_nodes:
        abstract = " ".join([a.strip() for a in abstract_nodes if a.strip()])
        if abstract.lower().startswith("abstract:"):
            abstract = abstract[len("abstract:") :].strip()
    if title and (abstract or abstract == ""):
        return title, abstract or ""
    return None


def fetch_url_content(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str], str]:
    """Fetch URL and return (title, text, method) using readability with fallback."""
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }
    # arXiv metadata first
    arxiv = arxiv_meta(url)
    if arxiv:
        title, abstract = arxiv
        content = "\n\n".join(part for part in (title, abstract) if part)
        return title, content, "arxiv_meta"
    # GitHub raw handling
    gh_raw = github_raw_url(url)
    if gh_raw:
        try:
            resp = session.get(gh_raw, timeout=timeout, headers=headers)
            resp.raise_for_status()
            content = resp.text
            if content and len(content.strip()) > 50:
                return gh_raw.split("/")[-1] or url, content, "github_raw"
        except Exception:
            pass

    try:
        resp = session.get(url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return None, None, "error"

    # Try readability extraction
    try:
        doc = Document(html)
        title = doc.short_title() or url
        summary_html = doc.summary()
        soup = BeautifulSoup(summary_html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        text = soup.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        cleaned = "\n".join(lines)
        if cleaned:
            return title, cleaned, "readability"
    except Exception:
        pass

    # Fallback: full page text
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)
    return title, cleaned, "fallback"
