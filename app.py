# --- Textual UI -------------------------------------------------------------
import textwrap
from rich.markdown import Markdown
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Header, Footer, TextArea, RichLog, Static
from workspace import MemoryWorkspace
from orchestrator import Orchestrator
from datatypes import Message, Role
import rss
from handlers import handle_add

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
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear log"),
        ("ctrl+s", "submit_input", "Send"),  # Handle Ctrl+S at app level
        ("ctrl+v", "paste_input", "Paste"),
    ]

    show_help = reactive(False)

    def __init__(self):
        super().__init__()
        self.ws = MemoryWorkspace()
        self.orch = Orchestrator(self.ws)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Hint("Commands: /help, /add, /summarize, /clear, /rss — otherwise chat with the assistant.")
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

    def action_paste_input(self) -> None:
        """Paste from clipboard into the input box."""
        try:
            self.input.paste()
        except Exception:
            # Ignore if clipboard not available
            pass

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
                self._write_assistant(r.content, reference=r.reference_time)
        else:
            self._write_assistant(str(reply))

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
            logs = handle_add(arg, self.ws, self.orch)
            for line in logs:
                self.log_view.write(line)
            return

        if cmd == "/summarize":
            summary = self.orch.summarize()
            if isinstance(summary, list):
                for m in summary:
                    self._write_assistant(m.content)
            else:
                self._write_assistant(str(summary))
            return

        if cmd == "/clear":
            self.log_view.clear()
            self.log_view.write("[ok] UI cleared (storage unchanged)")
            return

        if cmd == "/rss":
            await self._run_rss()
            return

        self.log_view.write(f"[warn] unknown command: {cmd}")

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
            self._write_assistant(msg.content)

    def _write_assistant(self, content: str, reference=None) -> None:
        """Render assistant output as Markdown when possible."""
        suffix = f" [{reference.isoformat()}]" if reference else ""
        try:
            self.log_view.write(Markdown(f"{content}{suffix}"))
        except Exception:
            self.log_view.write(f"assistant> {content}{suffix}")
