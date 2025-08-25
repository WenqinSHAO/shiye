"""
AI Assistant Memory — Minimal TUI (single file)

Features
- Single in-memory workspace (no DB, no heuristics to extend for now)
- Optional DSPy LLM workflow for replies/summaries
- Simple Textual TUI: a log + input box

Run
  pip install textual dspy-ai python-dotenv
  # optional: export your LLM creds, e.g.
  export OPENAI_API_KEY=...
  python app.py

Commands (type into the input)
  /help            Show commands
  /add <text>      Add text to memory
  /recall <query>  Recall the most recent memory containing <query>
  /list            Show the last N memory items
  /summarize       Summarize current memory via DSPy (or fallback)
  /clear           Clear memory
  <anything else>  Adds to memory and gets an assistant reply using memory context
"""
from __future__ import annotations

import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# --- Optional DSPy setup ----------------------------------------------------
# The app runs even if dspy isn't installed; we'll gracefully degrade.
DSPY_AVAILABLE = True
try:
    import dspy  # type: ignore
except Exception:
    DSPY_AVAILABLE = False


def _configure_dspy_if_possible() -> Optional[object]:
    """Configure DSPy if available and an API key is present.
    Returns an object with a .predict(instruction: str, context: str) -> str API.
    """
    if not DSPY_AVAILABLE:
        return None

    # TODO: support other LLM providers via env vars?
    # For now, we use Deepseek as an example.
    # Users can set DS_API_KEY in their environment.
    llm_base = "https://api.deepseek.com"
    llm_key = os.getenv("DS_API_KEY")

    if llm_key:
        try:
            # Configure DSPy with Deepseek LLM
            dspy.configure(lm=dspy.LM("deepseek/deepseek-chat", api_key=llm_key, base_url=llm_base))

            class Reply(dspy.Signature):
                """Generate an assistant reply.
                instruction: str
                context: str
                -> response: str
                """
                instruction = dspy.InputField()
                context = dspy.InputField(desc="context block with chat history to keep continuity")
                response = dspy.OutputField()

            predictor = dspy.Predict(Reply)

        
            class _PredictWrapper:
                def predict(self, instruction: str, context: str) -> str:
                    out = predictor(instruction=instruction, context=context)
                    # DSPy returns structured outputs. We expect 'response'.
                    return str(getattr(out, "response", ""))

            return _PredictWrapper()
        except Exception as e:
            # If anything fails, fall back gracefully.
            print(f"[WARN] Failed to configure DSPy: {e}")
            return None

    # No API key: don't configure a remote LM.
    return None


# --- Memory Workspace -------------------------------------------------------
@dataclass
class MemoryWorkspace:
    items: List[Dict[str, str]] = field(default_factory=list)
    max_items: int = 500  # simple cap

    def add(self, role: str, text: str) -> None:
        self.items.append({"role": role, "text": text})
        if len(self.items) > self.max_items:
            # Trim oldest when exceeding cap
            overflow = len(self.items) - self.max_items
            del self.items[:overflow]

    def list_recent(self, n: int = 20) -> List[Dict[str, str]]:
        return self.items[-n:]

    def clear(self) -> None:
        self.items.clear()

    def recall(self, query: str) -> Optional[Dict[str, str]]:
        q = query.lower().strip()
        for item in reversed(self.items):
            if q in item["text"].lower():
                return item
        return None

    def context_block(self, n: int = 12) -> str:
        """Return last n messages as a formatted context block."""
        chunk = self.items[-n:]
        lines = []
        for m in chunk:
            role = m.get("role", "user")
            text = m.get("text", "").strip()
            lines.append(f"{role}: {text}")
        return "\n".join(lines)


# --- LLM Orchestrator (via DSPy or fallback) --------------------------------
class Orchestrator:
    """Orchestrates replies and summaries using DSPy if configured, else falls back to local methods."""
      
    def __init__(self, workspace: MemoryWorkspace):
        self.workspace = workspace
        self.dspy_predictor = _configure_dspy_if_possible()

    def reply(self, user_text: str) -> str:
        context = self.workspace.context_block()
        instruction = textwrap.dedent(
            f"""
            You are a concise helpful assistant. Use the memory context to keep continuity.
            If the user asks to store or recall, reflect that in the answer.
            User says: {user_text}
            """
        ).strip()
        if self.dspy_predictor:
            try:
                out = self.dspy_predictor.predict(instruction=instruction, context=context)
                return out.strip() or self._fallback_reply(user_text)
            except Exception as e:
                return self._fallback_reply(user_text, note=f"(DSPy error: {e})")
        return self._fallback_reply(user_text)

    def summarize(self) -> str:
        context = self.workspace.context_block(n=50)
        instruction = "Summarize the key facts and decisions from the memory context in bullet points."
        if self.dspy_predictor:
            try:
                out = self.dspy_predictor.predict(instruction=instruction, context=context)
                return out.strip() or self._fallback_summary()
            except Exception as e:
                return self._fallback_summary(note=f"(DSPy error: {e})")
        return self._fallback_summary()

    # --- local fallbacks (no network / no DSPy) -----------------------------
    def _fallback_reply(self, user_text: str, note: str = "") -> str:
        context = self.workspace.context_block()
        snippet = (context[-200:] if len(context) > 200 else context) or "(empty memory)"
        ret = [
            "[local] No DSPy LM configured.",
            f"You said: {user_text}",
            "Context tail:",
            snippet,
        ]
        if note:
            ret.append(note)
        return "\n".join(ret)

    def _fallback_summary(self, note: str = "") -> str:
        items = self.workspace.list_recent(20)
        bullets = [f"- {it['role']}: {it['text'][:120]}" for it in items]
        header = "[local] Summary of recent memory (last 20):"
        if note:
            header += f" {note}"
        return "\n".join([header, *bullets])


# --- Textual UI -------------------------------------------------------------
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Header, Footer, Input, RichLog, Static


class Hint(Static):
    DEFAULT_CSS = """
    Hint { color: gray; height: auto; }
    """


class MemoryApp(App):
    CSS = """
    Screen { layout: vertical; }
    # Header and Footer are auto-styled by Textual
    .body { layout: vertical; height: 1fr; }
    # Log takes most of the screen
    # Input sits at the bottom
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear log"),
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
        self.input = Input(placeholder="Type a message or /help … then press Enter")
        yield self.input
        yield Footer()

    def action_clear_log(self) -> None:
        self.log_view.clear()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        self.input.value = ""
        if not text:
            return

        # Show user input in the log
        self.log_view.write(f"you> {text}")

        if text.startswith("/"):
            await self._handle_command(text)
            return

        # Regular chat: add to memory and let orchestrator reply
        self.ws.add("user", text)
        self.input.placeholder = "Thinking..."
        
        reply = self.orch.reply(text)
        self.input.placeholder = "Type a message or /help … then press Enter"
        
        self.ws.add("assistant", reply)
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
                self.log.write("usage: /add <text>")
                return
            self.ws.add("note", arg)
            self.log_view.write("[ok] added to memory")
            return

        if cmd == "/recall":
            if not arg:
                self.log_view.write("usage: /recall <query>")
                return
            hit = self.ws.recall(arg)
            if hit:
                self.log_view.write(f"[recall] {hit['role']}: {hit['text']}")
            else:
                self.log_view.write("[recall] no match")
            return

        if cmd == "/list":
            items = self.ws.list_recent(20)
            if not items:
                self.log_view.write("[list] memory is empty")
                return
            for i, it in enumerate(items, 1):
                self.log_view.write(f"[{i:02d}] {it['role']}: {it['text']}")
            return

        if cmd == "/summarize":
            summary = self.orch.summarize()
            self.ws.add("summary", summary)
            self.log_view.write(summary)
            return

        if cmd == "/clear":
            self.ws.clear()
            self.log_view.write("[ok] memory cleared")
            return

        self.log_view.write(f"[warn] unknown command: {cmd}")


if __name__ == "__main__":
    try:
        app = MemoryApp()
        app.run()
    except KeyboardInterrupt:
        print("\nbye")
        sys.exit(0)
