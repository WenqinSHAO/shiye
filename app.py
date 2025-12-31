# --- Textual UI -------------------------------------------------------------
import textwrap
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Header, Footer, TextArea, RichLog, Static
from workspace import MemoryWorkspace
from orchestrator import Orchestrator
from datatypes import Message, Role
import rss
from fetcher import extract_urls, fetch_url_content

class Hint(Static):
    DEFAULT_CSS = """
    Hint { color: gray; height: auto; }
    """

class MemoryApp(App):
    CSS = """
    Screen { layout: vertical; }
    .body { layout: vertical; height: 1fr; }
    TextArea { height: 4; border: solid gray; }
    .input-hint { height: 1; color: gray; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear log"),
        ("ctrl+s", "submit_input", "Send"),  # Handle Ctrl+S at app level
    ]

    show_help = reactive(False)

    def __init__(self):
        super().__init__()
        self.ws = MemoryWorkspace()
        self.orch = Orchestrator(self.ws)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Hint("Commands: /help, /add, /recall, /list, /summarize, /clear, /rss — otherwise chat with the assistant.")
        self.log_view = RichLog(wrap=True, name="log")
        yield self.log_view
        yield Static("Type a message or /help … then press Ctrl+S to send", classes="input-hint")
        self.input = TextArea(id="input")
        yield self.input
        yield Footer()

    def on_mount(self) -> None:
        """Focus the input when app starts"""
        self.input.focus()

    def action_submit_input(self) -> None:
        """Handle Ctrl+S submission"""
        text = self.input.text.strip()
        if text:
            self._handle_input(text)
            self.input.clear()
            self.input.focus()

    def action_clear_log(self) -> None:
        self.log_view.clear()

    def _handle_input(self, text: str) -> None:
        """Process input text"""
        if not text.strip():
            return

        # Show user input in the log
        self.log_view.write(f"you> {text}")

        if text.startswith("/"):
            self.run_worker(self._handle_command(text))
            return

        # Regular chat
        self.query_one(".input-hint").update("Thinking...")
        reply = self.orch.timelinereply(text)
        self.query_one(".input-hint").update("Type a message or /help … then press Ctrl+S to send")
        # make the display prettier and handle multiple messages
        if isinstance(reply, list):
            for r in reply:
                self.log_view.write(f"assistant> {r.content} {f'[{r.reference_time.isoformat()}]' if r.reference_time else ''}  ")
        else:
                self.log_view.write(f"assistant> {reply}")

    async def _handle_command(self, cmdline: str) -> None:
        parts = cmdline.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self.log_view.write(textwrap.dedent(
                """
                Commands:
                  /help            Show this help
                  /add <text>      Add <text> to memory
                  /recall <query>  Recall most recent memory containing <query>
                  /list            Show the last 20 memory items
                  /summarize       Summarize current memory (DSPy if configured; else local)
                  /clear           Clear all memory
                  /rss             Fetch configured RSS feeds and summarize
                """
            ).strip())
            return

        if cmd == "/add":
            if not arg:
                self.log_view.write("usage: /add <text>")
                return
            await self._handle_add(arg)
            return

        if cmd == "/recall":
            if not arg:
                self.log_view.write("usage: /recall <query>")
                return
            hit = self.ws.recall(arg)
            if hit:
                self.log_view.write(hit.to_text())
            else:
                self.log_view.write("[recall] no match")
            return

        if cmd == "/list":
            items = self.ws.list_recent(20)
            if not items:
                self.log_view.write("[list] memory is empty")
                return
            for i, it in enumerate(items, 1):
                self.log_view.write(f"[{i:02d}] {it.to_text()}")
            return

        if cmd == "/summarize":
            summary = self.orch.summarize()
            self.log_view.write(summary)
            return

        if cmd == "/clear":
            self.ws.clear()
            self.log_view.write("[ok] memory cleared")
            return

        if cmd == "/rss":
            await self._run_rss()
            return

        self.log_view.write(f"[warn] unknown command: {cmd}")

    async def _handle_add(self, arg: str) -> None:
        tokens = arg.split()
        mode = None
        if tokens and tokens[0].lower() in ("fetch", "refs"):
            mode = tokens[0].lower()
            arg = " ".join(tokens[1:])
        urls = extract_urls(arg)
        note_text = arg.strip()

        if urls:
            if len(urls) > 1 and mode != "fetch":
                # default to references only to avoid heavy fetch; user can re-run with fetch
                self._store_note_and_refs(note_text, urls, refs_only=True)
                self.log_view.write("[add] multiple URLs detected; saved note + references. Re-run with '/add fetch ...' to fetch contents.")
                return

            if mode == "refs":
                self._store_note_and_refs(note_text, urls, refs_only=True)
                self.log_view.write("[add] saved note + references (no fetch).")
                return

            # fetch content for URLs
            fetched = 0
            note_msg = Message(content=note_text, role=Role.USER, metadata={"urls": urls, "note_type": "user_note"})
            self.ws.add(note_msg)
            for url in urls:
                title, content = fetch_url_content(url)
                if not content:
                    self.log_view.write(f"[add] fetch failed for {url}; saved note only.")
                    continue
                msg = Message(
                    content=content,
                    role=Role.SYSTEM,
                    metadata={"url": url, "title": title, "source": "url_fetch"},
                )
                self.ws.add_with_document(
                    [msg],
                    document_meta={
                        "doc_type": "web_page",
                        "title": title,
                        "source": "url",
                        "uri": url,
                        "tags": {"url": url, "note_present": bool(note_text)},
                    },
                )
                fetched += 1
            self.log_view.write(f"[ok] saved note and fetched {fetched}/{len(urls)} URL(s).")
            return

        # no URLs: regular add via timechunker
        m = self.orch.timechunker(text=arg, role_hint="user")
        self.log_view.write("[ok] added to memory")

    def _store_note_and_refs(self, note_text: str, urls: list, refs_only: bool = False) -> None:
        metadata = {"urls": urls, "note_type": "user_note"}
        if refs_only:
            metadata["archive_mode"] = "refs_only"
        msg = Message(content=note_text, role=Role.USER, metadata=metadata)
        self.ws.add(msg)

    async def _run_rss(self) -> None:
        feeds = rss.load_feed_urls()
        if not feeds:
            self.log_view.write("[rss] no feeds configured (rss_feeds.txt).")
            return
        self.log_view.write(f"[rss] fetching from {len(feeds)} feeds...")
        try:
            items = rss.fetch_all(feeds, per_feed_limit=3, total_limit=20)
        except Exception as e:
            self.log_view.write(f"[rss] fetch failed: {e}")
            return
        if not items:
            self.log_view.write("[rss] no items found.")
            return
        keywords = ["AI infra", "LLM", "AI coding", "Agent", "Agentic AI", "machine learning", "attention", "memory"]
        summary = self.orch.summarize_rss(items, keywords=keywords)
        for msg in summary:
            self.log_view.write(f"assistant> {msg.content}")
