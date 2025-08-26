# --- Textual UI -------------------------------------------------------------
import textwrap
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Header, Footer, TextArea, RichLog, Static
from workspace import MemoryWorkspace
from orchestrator import Orchestrator

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
        yield Hint("Commands: /help, /add, /recall, /list, /summarize, /clear — otherwise chat with the assistant.")
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
                """
            ).strip())
            return

        if cmd == "/add":
            if not arg:
                self.log_view.write("usage: /add <text>")
                return
            # no need to update the memory workspace here, as timechunker does it
            m = self.orch.timechunker(text=arg, role_hint="user") 
            self.log_view.write("[ok] added to memory")
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

        self.log_view.write(f"[warn] unknown command: {cmd}")
