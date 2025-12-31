import re
from typing import List, Tuple, Optional

import requests
from bs4 import BeautifulSoup


URL_REGEX = re.compile(r"https?://\S+")


def extract_urls(text: str) -> List[str]:
    return URL_REGEX.findall(text or "")


def fetch_url_content(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    """Fetch URL and return (title, text) with basic cleaning."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "shiye/0.1"})
        resp.raise_for_status()
    except Exception:
        return None, None
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    text = soup.get_text(separator="\n")
    # basic normalization
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)
    return title, cleaned
