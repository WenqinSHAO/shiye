from handlers import handle_add
from datatypes import Message, Role


class DummyWS:
    def __init__(self):
        self.added = []
        self.docs = []

    def add(self, m):
        self.added.append(m)

    def add_with_document(self, msgs, document_meta):
        self.docs.append((msgs, document_meta))

class DummyOrch:
    def __init__(self):
        self.calls = []

    def timechunker(self, text: str, role_hint: str = "user"):
        self.calls.append((text, role_hint))
        return []


def test_handle_add_refs_only():
    ws = DummyWS()
    orch = DummyOrch()
    logs = handle_add("refs https://example.com note here", ws, orch)
    assert ws.added  # note stored
    assert not ws.docs  # nothing fetched
    assert logs and "references" in logs[0]


def test_handle_add_fetch_single(monkeypatch):
    ws = DummyWS()
    orch = DummyOrch()
    # mock fetcher
    monkeypatch.setattr("handlers.fetch_url_content", lambda url: ("Title", "Body text"))
    logs = handle_add("fetch https://example.com", ws, orch)
    assert ws.docs  # fetched content stored as document
    assert any("fetched" in line for line in logs)
