#!/usr/bin/env python3
# to figure out why ctrl+enter is not working in textual TextArea

from textual.app import App, ComposeResult
from textual.widgets import TextArea, Static, RichLog
from textual import events

class SimpleApp(App):
    CSS = """
    TextArea { height: 5; }
    #log { height: 15; border: solid white; }
    """
    
    BINDINGS = [
        ("ctrl+enter", "submit_text", "Submit"),
        ("ctrl+s", "submit_text", "Submit Alt"),  # Alternative key
    ]
    
    def compose(self) -> ComposeResult:
        yield Static("Type in text area, press Ctrl+Enter (or Ctrl+S as backup):")
        yield TextArea(id="input")
        yield RichLog(id="log")
    
    def on_mount(self) -> None:
        self.query_one("#input").focus()
    
    def action_submit_text(self) -> None:
        log_view = self.query_one("#log")
        text_area = self.query_one("#input")
        log_view.write(f"✓ Submit action triggered! Text: '{text_area.text}'")
        text_area.clear()
        text_area.focus()
    
    def on_key(self, event: events.Key) -> None:
        # Log all keys at app level
        log_view = self.query_one("#log")
        log_view.write(f"App level key: '{event.key}'")

if __name__ == "__main__":
    SimpleApp().run()