from typing import List
from pathlib import Path
import secrets

from fastapi import Body, FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import UTC, datetime

from datatypes import Message, ensure_utc
from handlers import handle_add
from orchestrator import Orchestrator
from storage import NoteConflictError
from workspace import MemoryWorkspace
import rss

app = FastAPI(title="Shiye Web")

workspace = MemoryWorkspace()
orchestrator = Orchestrator(workspace)
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
IMAGES_DIR = ASSETS_DIR / "img"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


def msg_to_dict(m: Message) -> dict:
    return {
        "content": m.content,
        "role": m.role.value,
        "created_at": ensure_utc(m.created_at).isoformat(),
        "reference_time": ensure_utc(m.reference_time).isoformat() if m.reference_time else None,
        "metadata": m.metadata,
        "chunk_id": m.metadata.get("chunk_id"),
    }

def make_system_msg(content: str) -> dict:
    return {
        "content": content,
        "role": "system",
        "created_at": datetime.now(UTC).isoformat(),
        "reference_time": None,
        "metadata": {},
        "chunk_id": None,
    }


def note_dict(note: dict) -> dict:
    if not note:
        return {}
    return {
        "id": note.get("id"),
        "title": note.get("title"),
        "content": note.get("content", ""),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "images": note.get("images") or [],
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = """
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8" />
        <title>Shiye</title>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script>
            window.MathJax = {
                tex: {
                    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
                },
                options: { skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }
            };
        </script>
        <script id="mathjax-script" defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
        <style>
            :root {
                --bg: #f6f8fb;
                --panel: #ffffff;
                --ink: #1f2933;
                --subtle: #62738a;
                --border: #d7deea;
                --accent: #5b8def;
                --accent-soft: #e8efff;
                --assistant: #e8f5e9;
                --assistant-border: #cfead4;
                --user: #e7f1ff;
                --user-border: #c8dcff;
                --system: #fff7e6;
                --system-border: #f4e3b5;
                --action-bg: #edf1f7;
                --action-border: #cfd7e2;
            }
            body { font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--ink); min-height: 100vh; overflow: hidden; }
            header { padding: 12px 16px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 12px; position: sticky; top: 0; z-index: 10; }
            main { display: grid; grid-template-columns: 1fr; gap: 12px; padding: 12px 16px; height: calc(100vh - 64px); box-sizing: border-box; overflow: hidden; }
            body.show-history main { grid-template-columns: 2fr auto; }
            body.note-mode main { grid-template-columns: 1fr; }
            body.note-mode section.chat, body.note-mode #history-wrapper { display: none; }
            #note-shell { display: none; height: 100%; min-height: 0; grid-template-columns: 260px 1.6fr 1.2fr; gap: 12px; }
            body.note-mode #note-shell { display: grid; }
            main section.chat { display: grid; grid-template-rows: 1fr auto; min-height: 0; height: 100%; min-width: 0; overflow: hidden; }
            #log { padding: 16px; height: 100%; min-height: 0; overflow-y: auto; background: linear-gradient(180deg, #fafdff 0%, var(--bg) 100%); border-radius: 12px; border: 1px solid var(--border); box-sizing: border-box; }
            .msg { margin-bottom: 12px; position: relative; padding-right: 90px; }
            .role { font-size: 12px; color: var(--subtle); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
            .bubble { padding: 12px 14px; border-radius: 12px; background: var(--panel); border: 1px solid var(--border); box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); }
            .role-user .bubble { background: var(--user); border-color: var(--user-border); }
            .role-assistant .bubble { background: var(--assistant); border-color: var(--assistant-border); }
            .role-system .bubble { background: var(--system); border-color: var(--system-border); }
            .msg .actions { position: absolute; right: 8px; top: 12px; display: inline-flex; gap: 6px; opacity: 0; transition: opacity 0.15s ease; }
            .msg:hover .actions { opacity: 1; }
            .actions button { font-size: 11px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--action-border); background: var(--action-bg); color: var(--ink); cursor: pointer; }
            .actions button:disabled { opacity: 0.4; cursor: not-allowed; }
            form { padding: 12px 16px; background: var(--panel); border-top: 1px solid var(--border); display: grid; gap: 8px; position: static; border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); }
            .input-wrap { position: relative; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: #fff; }
            textarea { width: 100%; min-height: 110px; resize: vertical; border: none; background: transparent; color: var(--ink); padding: 14px 14px 48px 14px; box-sizing: border-box; display: block; }
            .input-footer { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-top: 1px solid var(--border); background: linear-gradient(180deg, rgba(255,255,255,0.94), #f7f9ff); }
            .input-footer span { font-size: 12px; color: var(--subtle); }
            button { border: none; border-radius: 10px; padding: 10px 14px; cursor: pointer; font-weight: 600; color: #fff; background: var(--accent); box-shadow: 0 2px 6px rgba(91, 141, 239, 0.35); }
            button.secondary { background: #5fc49e; box-shadow: 0 2px 6px rgba(95, 196, 158, 0.35); }
            button.ghost { background: var(--panel); color: var(--ink); border: 1px solid var(--border); box-shadow: none; }
            .row { display: flex; gap: 8px; align-items: center; }
            .debug-only { display: none; }
            body.debug-mode .debug-only { display: block; }
            details.debug-block { margin-top: 8px; border: 1px dashed var(--border); border-radius: 8px; background: #f9fbff; }
            details.debug-block summary { padding: 8px 10px; cursor: pointer; color: var(--subtle); font-size: 12px; }
            details.debug-block pre { margin: 0; padding: 8px 10px 10px; font-size: 12px; background: transparent; color: #0f172a; overflow-x: auto; }
            #history-wrapper { display: none; height: 100%; min-width: 240px; max-width: 520px; position: relative; min-height: 0; }
            body.show-history #history-wrapper { display: flex; }
            #history-panel { width: 100%; height: 100%; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
            #history-resize { width: 6px; cursor: col-resize; position: absolute; left: -4px; top: 0; bottom: 0; }
            #history-header { padding: 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
            #history-list { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; background: #f9fbff; }
            #history-list .item { position: relative; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border); background: #fff; margin-bottom: 8px; }
            #history-list .item.role-user { background: var(--user); border-color: var(--user-border); }
            #history-list .item.role-assistant { background: var(--assistant); border-color: var(--assistant-border); }
            #history-list .item.role-system { background: var(--system); border-color: var(--system-border); }
            #history-list .item .role { margin: 0 0 4px 0; }
            #history-list .item .actions { position: absolute; right: 6px; top: 6px; display: inline-flex; gap: 4px; opacity: 0; transition: opacity 0.15s ease; }
            #history-list .item:hover .actions { opacity: 1; }
            .note-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
            .note-list-header, .note-editor-header, .note-preview-header { padding: 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 8px; }
            .note-subtle { font-size: 12px; color: var(--subtle); }
            .note-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--ink); }
            .note-pill.subtle { background: #f0f4ff; color: var(--subtle); }
            .note-list-body { padding: 10px; overflow-y: auto; flex: 1; background: #f9fbff; }
            .note-item { padding: 10px 12px; border-radius: 10px; border: 1px solid transparent; cursor: pointer; margin-bottom: 8px; transition: border-color 0.12s ease, background 0.12s ease; }
            .note-item:hover { border-color: var(--border); background: #fff; }
            .note-item.active { border-color: var(--accent); background: var(--accent-soft); }
            .note-item .title { font-weight: 600; margin-bottom: 4px; white-space: nowrap; overflow: hidden; }
            .note-item .meta { font-size: 12px; color: var(--subtle); }
            .note-editor-body { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
            #note-input { width: 100%; height: 100%; border: none; outline: none; padding: 16px; padding-bottom: 140px; font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; font-size: 14px; line-height: 1.35; resize: none; background: #fdfdff; color: var(--ink); border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; box-sizing: border-box; overflow: auto; scroll-padding-bottom: 140px; }
            #note-preview { padding: 16px; overflow-y: auto; flex: 1; background: linear-gradient(180deg, #fff, #f7f9ff); line-height: 1.35; }
            #note-preview h1, #note-preview h2, #note-preview h3 { margin-top: 0; }
            #note-preview img { max-width: 100%; border-radius: 10px; border: 1px solid var(--border); }
            .note-title { font-weight: 600; color: var(--ink); }
            .note-actions { display: inline-flex; gap: 8px; align-items: center; }
            .note-exit { display: none; }
            body.note-mode .note-exit { display: inline-flex; }
            .history-body { position: relative; }
            .history-body.collapsed { max-height: 140px; overflow: hidden; }
            .history-body.collapsed::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 32px; background: linear-gradient(180deg, transparent, #fff); }
            .history-expand { margin-top: 6px; font-size: 11px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--border); background: var(--panel); cursor: pointer; }
            .command-strip { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--subtle); }
            .command-pill { padding: 4px 8px; border-radius: 999px; border: 1px solid var(--border); background: #f3f6fb; color: var(--ink); font-weight: 600; }
            .history-month { margin-bottom: 10px; }
            .history-month-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: #eef3fb; cursor: pointer; }
            .history-month-title { font-weight: 600; color: var(--ink); }
            .history-month-count { font-size: 12px; color: var(--subtle); }
            .history-days { margin-top: 6px; display: flex; flex-direction: column; gap: 8px; }
            .history-day { border: 1px solid var(--border); border-radius: 10px; background: #fff; }
            .history-day-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; cursor: pointer; }
            .history-day-label { font-weight: 600; }
            .history-day-count { font-size: 12px; color: var(--subtle); }
            .history-day-body { padding: 8px 10px; display: none; background: #f9fbff; border-top: 1px solid var(--border); }
            .history-subtle { font-size: 12px; color: var(--subtle); }
            #toast-container { position: fixed; inset: auto 26px 26px auto; display: flex; flex-direction: column-reverse; gap: 12px; z-index: 180; pointer-events: none; align-items: flex-end; width: min(360px, calc(100vw - 52px)); }
            .toast { width: 100%; max-width: 340px; padding: 12px 14px; border-radius: 14px; border: 1px solid rgba(98, 115, 138, 0.22); background: rgba(255, 255, 255, 0.92); box-shadow: 0 16px 38px rgba(15, 23, 42, 0.18); opacity: 0; transform: translateY(14px) scale(0.98); transition: transform 0.22s ease, opacity 0.22s ease; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink); pointer-events: auto; backdrop-filter: blur(8px); }
            .toast.show { opacity: 1; transform: translateY(0) scale(1); }
            .toast-info { background: #e9f1ff; border-color: #cddffb; }
            .toast-success { background: #ecfdf3; border-color: #bbf7d0; color: #166534; }
            .toast-error { background: #fff1f2; border-color: #fecdd3; color: #991b1b; }
            .toast button.toast-action { margin-left: auto; border: 1px solid var(--border); background: var(--panel); border-radius: 8px; padding: 6px 8px; cursor: pointer; font-size: 12px; }
            .note-banner { display: none; margin-top: 6px; padding: 8px 10px; border-radius: 10px; border: 1px dashed var(--border); background: #f5f7fb; font-size: 12px; color: var(--ink); align-items: center; gap: 8px; }
            .note-banner.show { display: inline-flex; }
            .note-banner button { font-size: 12px; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); cursor: pointer; }
            .note-pill.inline { padding: 4px 10px; font-size: 12px; }
            
            /* Search Results Styling */
            .search-results { margin: 10px 0; }
            .search-header { margin-bottom: 12px; padding: 8px 12px; background: var(--accent-soft); border: 1px solid var(--border); border-radius: 8px; font-size: 13px; color: var(--ink); }
            .search-hit { margin-bottom: 12px; padding: 12px 14px; border-radius: 10px; background: #fff; border: 1px solid var(--border); box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04); transition: border-color 0.15s ease, box-shadow 0.15s ease; }
            .search-hit:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(91, 141, 239, 0.15); }
            .search-hit .hit-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 12px; }
            .search-hit .hit-type { padding: 3px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); font-weight: 600; }
            .search-hit .hit-score { color: var(--subtle); }
            .search-hit .hit-date { margin-left: auto; color: var(--subtle); }
            .search-hit .hit-title { font-weight: 600; margin-bottom: 6px; color: var(--ink); }
            .search-hit .hit-location { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; font-size: 11px; color: var(--subtle); }
            .search-hit .hit-location-item { padding: 2px 6px; background: var(--action-bg); border-radius: 4px; border: 1px solid var(--border); }
            .search-hit .hit-text { line-height: 1.5; color: var(--ink); margin-bottom: 6px; }
            .search-hit .hit-source { font-size: 12px; }
            .search-hit .hit-source a { color: var(--accent); text-decoration: none; }
            .search-hit .hit-source a:hover { text-decoration: underline; }
            .hit-scores { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
            .hit-score-chips, .score-chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
            .score-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 8px; background: #eef3ff; border: 1px solid var(--border); font-size: 12px; color: var(--ink); }
            .score-chip .score-label { font-weight: 700; letter-spacing: 0.04em; font-size: 11px; color: var(--subtle); text-transform: uppercase; }
            .search-debug { margin-top: 20px; padding: 14px; background: #f8f9fa; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; font-family: 'Monaco', 'Menlo', 'Courier New', monospace; }
            .search-debug .debug-header { font-weight: 600; margin-bottom: 10px; color: var(--ink); cursor: pointer; user-select: none; }
            .search-debug .debug-header:hover { color: var(--accent); }
            .search-debug .debug-section { margin-bottom: 12px; }
            .search-debug .debug-section-title { font-weight: 600; color: var(--subtle); margin-bottom: 4px; }
            .search-debug .debug-item { padding: 4px 0; color: var(--ink); }
            .search-debug .debug-candidate { padding: 8px; margin: 4px 0; background: white; border: 1px solid var(--border); border-radius: 4px; }
            .search-debug .debug-candidate-header { font-weight: 600; margin-bottom: 4px; color: var(--ink); }
            .search-debug .debug-score-history { margin-top: 4px; padding-left: 12px; }
            .search-debug .debug-score-item { color: var(--subtle); }
            .search-debug .debug-collapsed { display: none; }
            .score-chart { margin-top: 8px; padding: 10px; background: white; border: 1px solid var(--border); border-radius: 8px; }
            .score-chart svg { width: 100%; height: 180px; overflow: visible; }
            .score-chart .legend { margin-top: 8px; display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; color: var(--subtle); }
            .legend .swatch { display: inline-flex; align-items: center; gap: 6px; }
            .legend .dot { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
        </style>
    </head>
    <body>
        <header>
            <div><strong>Shiye</strong> — Your personal knowledge base</div>
            <div class="row">
                <label style="display:flex;align-items:center;gap:6px;color:#4b5563;font-size:12px;">
                    <input type="checkbox" id="debugToggle" onclick="toggleDebug()" checked />
                    debug
                </label>
                <button id="noteBackBtn" type="button" class="ghost note-exit" onclick="exitNoteMode()">Back to chat</button>
                <button id="historyBtn" type="button" class="ghost" onclick="toggleHistory()">History</button>
            </div>
        </header>
        <main>
            <section class="chat">
                <div id="log"></div>
                <form onsubmit="event.preventDefault(); sendChat();">
                    <div class="input-wrap">
                        <textarea id="input" placeholder="Type a message... (slash commands supported)"></textarea>
                        <div class="input-footer">
                            <div class="command-strip">
                                <span class="command-pill">/note</span>
                                <span class="command-pill">/find</span>
                                <span class="command-pill">/add</span>
                                <span class="command-pill">/rss</span>
                                <span class="command-pill">/summarize</span>
                                <span class="command-pill">/clear</span>
                            </div>
                            <div style="flex:1;"></div>
                            <span id="sendStatus">Ctrl+Enter to send</span>
                            <button id="sendBtn" type="submit">Send</button>
                        </div>
                    </div>
                </form>
            </section>
            <section id="note-shell" aria-label="note mode">
                <div class="note-panel note-list-panel">
                    <div class="note-list-header">
                        <div>
                            <div style="font-weight:700;">Notebook</div>
                            <div id="noteStatus" class="note-subtle">Capture Markdown notes with previews.</div>
                        </div>
                        <div class="note-actions">
                            <button type="button" class="ghost" onclick="refreshNotes(true, true)">Refresh</button>
                            <button type="button" class="secondary" onclick="newNote()">New</button>
                        </div>
                    </div>
                    <div class="note-list-body" id="note-list"></div>
                </div>
                <div class="note-panel note-editor-panel">
                    <div class="note-editor-header">
                        <div>
                            <div class="note-pill">Editor</div>
                            <div id="note-updated" class="note-subtle"></div>
                            <div id="note-recovery" class="note-banner"></div>
                        </div>
                        <div class="note-actions">
                            <span id="note-saving" class="note-pill subtle" style="display:none;">Saving…</span>
                            <span id="note-dirty" class="note-pill subtle" style="display:none;">Unsaved</span>
                            <button type="button" class="ghost" onclick="insertTimestamp()">Insert date/time</button>
                            <button id="saveNoteBtn" type="button" class="ghost" onclick="saveActiveNote()">Save</button>
                        </div>
                    </div>
                    <div class="note-editor-body">
                        <textarea id="note-input" placeholder="Markdown note... Paste images to attach."></textarea>
                    </div>
                </div>
                <div class="note-panel note-preview-panel">
                    <div class="note-preview-header">
                        <div class="note-pill">Preview</div>
                        <div id="note-title-display" class="note-title"></div>
                    </div>
                    <div id="note-preview"></div>
                </div>
            </section>
            <aside id="history-wrapper">
                <div id="history-resize"></div>
                    <div id="history-panel">
                    <div id="history-header">
                        <div style="font-weight:600;">History</div>
                        <button class="ghost" type="button" onclick="refreshHistory()">Refresh</button>
                    </div>
                    <div id="history-list"></div>
                </div>
            </aside>
        </main>
        <div id="toast-container" aria-live="polite"></div>
        <script>
            const logEl = document.getElementById('log');
            const inputEl = document.getElementById('input');
            const sendBtn = document.getElementById('sendBtn');
            const sendStatus = document.getElementById('sendStatus');
            const debugToggle = document.getElementById('debugToggle');
            const historyBtn = document.getElementById('historyBtn');
            const historyList = document.getElementById('history-list');
            const historyWrapper = document.getElementById('history-wrapper');
            const historyPanel = document.getElementById('history-panel');
            const historyResize = document.getElementById('history-resize');
            const toastContainer = document.getElementById('toast-container');
            const noteShell = document.getElementById('note-shell');
            const noteList = document.getElementById('note-list');
            const noteInput = document.getElementById('note-input');
            const notePreview = document.getElementById('note-preview');
            const saveNoteBtn = document.getElementById('saveNoteBtn');
            const noteTitleDisplay = document.getElementById('note-title-display');
            const noteUpdated = document.getElementById('note-updated');
            const noteSaving = document.getElementById('note-saving');
            const noteDirty = document.getElementById('note-dirty');
            const noteStatus = document.getElementById('noteStatus');
            const noteRecovery = document.getElementById('note-recovery');
            const mathScript = document.getElementById('mathjax-script');
            function normalizeLinkValue(val) {
                if (typeof val === "string") return val;
                if (!val) return "";
                if (typeof val === "object") {
                    if (typeof val.href === "string") return val.href;
                    if (typeof val.url === "string") return val.url;
                    if (typeof val.link === "string") return val.link;
                    if (typeof val.text === "string") return val.text;
                    if (typeof val.title === "string") return val.title;
                    if (typeof val.raw === "string") return val.raw;
                }
                return "";
            }

            function getMessageSourceUrl(metadata) {
                if (!metadata) return null;
                const direct = normalizeLinkValue(metadata.url);
                if (direct) return direct;
                if (Array.isArray(metadata.urls) && metadata.urls.length) {
                    const candidate = normalizeLinkValue(metadata.urls[0]);
                    if (candidate) return candidate;
                }
                if (typeof metadata.link === "string") return metadata.link;
                if (typeof metadata.href === "string") return metadata.href;
                return null;
            }

            function showToast(message, type = "info", options = {}) {
                if (!toastContainer) return;
                const toast = document.createElement("div");
                toast.className = `toast toast-${type}`;
                toast.textContent = message || "";
                toast.addEventListener("click", () => toast.remove());
                if (options.actionText && typeof options.onAction === "function") {
                    const action = document.createElement("button");
                    action.className = "toast-action";
                    action.textContent = options.actionText;
                    action.onclick = () => {
                        try { options.onAction(); } catch (e) { console.warn("toast action failed", e); }
                        toast.remove();
                    };
                    toast.appendChild(action);
                }
                toastContainer.appendChild(toast);
                requestAnimationFrame(() => toast.classList.add("show"));
                const ttl = options.duration ?? 4300;
                if (ttl !== null && ttl !== undefined) {
                    setTimeout(() => {
                        toast.classList.remove("show");
                        setTimeout(() => toast.remove(), 240);
                    }, ttl);
                }
            }

            let statusHideTimer = null;
            function clearStatus() {
                if (statusHideTimer) {
                    clearTimeout(statusHideTimer);
                    statusHideTimer = null;
                }
            }

            function setStatus(message, type = "info", options = {}) {
                clearStatus();
                const duration = options.duration ?? options.ttl ?? (type === "error" ? 5400 : 3600);
                showToast(message || "", type, {
                    duration: duration === null ? null : duration,
                    actionText: options.actionText,
                    onAction: options.onAction,
                });
                if (duration && duration !== null) {
                    statusHideTimer = setTimeout(() => {
                        statusHideTimer = null;
                    }, duration);
                }
            }

            function surfaceError(message, options = {}) {
                if (options.toast === false) {
                    clearStatus();
                    return;
                }
                const msg = message || "Something went wrong — please retry.";
                setStatus(msg, "error", {
                    ttl: options.ttl ?? 9000,
                    duration: options.duration,
                    actionText: options.actionText,
                    onAction: options.onAction,
                });
            }

            async function fetchJson(url, options = {}) {
                const res = await fetch(url, options);
                let raw = "";
                let data = null;
                try {
                    raw = await res.text();
                    data = raw ? JSON.parse(raw) : null;
                } catch (e) {
                    data = null;
                }
                if (!res.ok) {
                    const detail = data && data.detail;
                    let reason = "";
                    if (typeof detail === "string") {
                        reason = detail;
                    } else if (detail && typeof detail === "object") {
                        reason = detail.reason || detail.message || detail.error || "";
                    }
                    if (!reason && raw) {
                        reason = raw.slice(0, 300);
                    }
                    const msg = reason || `Request failed (${res.status})`;
                    const err = new Error(msg);
                    err.status = res.status;
                    err.payload = data;
                    err.raw = raw;
                    throw err;
                }
                return data || {};
            }

            const renderer = new marked.Renderer();
            renderer.link = (href, title, text) => {
                const t = title ? ` title="${title}"` : "";
                const safeHref = normalizeLinkValue(href) || "#";
                const rawLabel = normalizeLinkValue(text);
                const label = rawLabel && rawLabel.trim() !== "" ? rawLabel : safeHref;
                return `<a href="${safeHref}"${t} target="_blank" rel="noopener noreferrer">${label}</a>`;
            };
            
            // Helper to resolve relative GitHub URLs
            function resolveGitHubUrl(imageUrl, sourceUrl) {
                const safeImageUrl = normalizeLinkValue(imageUrl);
                const safeSourceUrl = normalizeLinkValue(sourceUrl);
                if (!safeImageUrl && !safeSourceUrl) return "";
                if (!safeImageUrl) return "";
                // If already absolute, return as-is
                if (safeImageUrl && (safeImageUrl.startsWith('http://') || safeImageUrl.startsWith('https://'))) {
                    return safeImageUrl;
                }
                // Parse GitHub URL to extract owner, repo, and branch
                try {
                    const url = new URL(safeSourceUrl);
                    if (!url.hostname.includes('github')) return safeImageUrl;
                    
                    const pathParts = url.pathname.split('/').filter(p => p);
                    if (pathParts.length < 2) return safeImageUrl;
                    
                    const owner = pathParts[0];
                    const repo = pathParts[1];
                    let branch = 'HEAD';
                    let basePath = '';
                    
                    // Extract branch and path from URL patterns like:
                    // /owner/repo/blob/branch/path/file.md
                    // /owner/repo/tree/branch/path
                    if (pathParts[2] === 'blob' || pathParts[2] === 'tree') {
                        branch = pathParts[3] || 'HEAD';
                        basePath = pathParts.slice(4, -1).join('/');
                    }
                    
                    // Remove leading ./ or /
                    let relPath = safeImageUrl.replace(/^[./]+/, '');
                    
                    // Construct full path
                    const fullPath = basePath ? `${basePath}/${relPath}` : relPath;
                    
                    // Return raw.githubusercontent.com URL
                    return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${fullPath}`;
                } catch (e) {
                    console.warn('Failed to resolve GitHub URL:', e);
                    return safeImageUrl;
                }
            }
            
            // Store current message context for image rendering
            let currentMessageUrl = null;
            
            renderer.image = (href, title, text) => {
                const baseHref = normalizeLinkValue(href);
                const resolvedHref = currentMessageUrl ? resolveGitHubUrl(baseHref, currentMessageUrl) : baseHref;
                const t = title ? ` title="${title}"` : "";
                const alt = text || "";
                if (!resolvedHref) return alt ? `<span>${alt}</span>` : "";
                return `<img src="${resolvedHref}" alt="${alt}"${t} />`;
            };
            
            marked.setOptions({ renderer });
            let mathQueue = [];
            let historyOpen = false;
            let isResizing = false;
            let noteMode = false;
            let activeNoteId = null;
            let noteChanged = false;
            let noteCache = [];
            let notesLoadedOnce = false;
            let sending = false;
            let historyDayMeta = [];
            let historyLoadedDays = {};
            let expandedMonths = new Set();
            let openHistoryDays = new Set();
            let activeNoteVersion = null;
            let noteAutosaveTimer = null;
            let noteSavingNow = false;
            let lastSavedContent = "";
            const NOTE_DRAFT_PREFIX = "shiye.note.draft";

            function deriveNoteTitle(text) {
                if (!text) return "Untitled note";
                const lines = text.split("\\n");
                for (const line of lines) {
                    const trimmed = line.trim().replace(/^#+\\s*/, "");
                    if (trimmed) return trimmed.slice(0, 160);
                }
                return "Untitled note";
            }

            function typesetMath(el) {
                if (!el) return;
                if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                    MathJax.typesetPromise([el]).catch((e) => console.warn("mathjax render failed", e));
                } else {
                    mathQueue.push(el);
                }
            }

            function flushMathQueue() {
                if (!mathQueue.length) return;
                if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                    const unique = Array.from(new Set(mathQueue));
                    mathQueue = [];
                    MathJax.typesetPromise(unique).catch((e) => console.warn("mathjax queued render failed", e));
                }
            }

            function formatNoteListTitle(title) {
                const full = title || "Untitled note";
                const maxLen = 42;
                if (full.length <= maxLen) return full;
                return full.slice(0, maxLen - 6).trimEnd() + " [...]";
            }

            function markNoteDirty(on) {
                noteChanged = on;
                if (noteDirty) noteDirty.style.display = on ? "inline-flex" : "none";
            }

            function setNoteSaving(on, label = "Saving…") {
                noteSavingNow = on;
                if (noteSaving) {
                    noteSaving.style.display = on ? "inline-flex" : "none";
                    noteSaving.textContent = label;
                }
                if (saveNoteBtn) {
                    saveNoteBtn.disabled = on;
                }
            }

            function getDraftKey(noteId = activeNoteId) {
                const keyId = noteId === undefined || noteId === null ? "new" : noteId;
                return `${NOTE_DRAFT_PREFIX}:${keyId}`;
            }

            function persistDraft(content, noteId = activeNoteId) {
                try {
                    const payload = {
                        id: noteId,
                        content: content ?? (noteInput ? noteInput.value : ""),
                        savedAt: new Date().toISOString(),
                        updatedAt: activeNoteVersion,
                    };
                    localStorage.setItem(getDraftKey(noteId), JSON.stringify(payload));
                } catch (e) {
                    console.warn("draft persist failed", e);
                }
            }

            function readDraft(noteId = activeNoteId) {
                try {
                    const raw = localStorage.getItem(getDraftKey(noteId));
                    return raw ? JSON.parse(raw) : null;
                } catch (e) {
                    console.warn("draft read failed", e);
                    return null;
                }
            }

            function clearDraft(noteId = activeNoteId) {
                try {
                    localStorage.removeItem(getDraftKey(noteId));
                } catch (e) {
                    console.warn("draft clear failed", e);
                }
            }

            function hideNoteRecovery() {
                if (!noteRecovery) return;
                noteRecovery.classList.remove("show");
                noteRecovery.innerHTML = "";
            }

            function renderNoteRecovery(message, onRestore) {
                if (!noteRecovery) return;
                noteRecovery.innerHTML = "";
                const span = document.createElement("span");
                span.textContent = message;
                noteRecovery.appendChild(span);
                if (onRestore) {
                    const btn = document.createElement("button");
                    btn.textContent = "Restore";
                    btn.onclick = () => {
                        onRestore();
                        hideNoteRecovery();
                    };
                    noteRecovery.appendChild(btn);
                }
                noteRecovery.classList.add("show");
            }

            function scheduleAutosave(reason = "typing") {
                if (noteAutosaveTimer) clearTimeout(noteAutosaveTimer);
                noteAutosaveTimer = setTimeout(() => autosaveNote(reason), 1600);
            }

            async function autosaveNote(reason = "typing") {
                if (noteAutosaveTimer) {
                    noteAutosaveTimer = null;
                }
                if (!noteChanged) return;
                await saveActiveNote({ isAutosave: true, reason });
            }

            function setSending(on) {
                sending = on;
                if (sendBtn) {
                    sendBtn.disabled = on;
                    sendBtn.textContent = on ? "Waiting..." : "Send";
                }
                if (sendStatus) {
                    if (on) {
                        sendStatus.textContent = "Waiting for response...";
                        sendStatus.style.color = "#dc2626";
                    } else {
                        sendStatus.textContent = "Ctrl+Enter to send";
                        sendStatus.style.color = "var(--subtle)";
                    }
                }
            }

            function renderNotePreview() {
                if (!notePreview) return;
                const text = noteInput ? noteInput.value : "";
                notePreview.innerHTML = text ? marked.parse(text) : '<div class="note-subtle">Nothing to preview yet.</div>';
                if (noteTitleDisplay) noteTitleDisplay.textContent = deriveNoteTitle(text);
                typesetMath(notePreview);
            }

            function updateNoteMeta(ts, prefix = "Last saved") {
                if (noteUpdated) {
                    const label = ts ? `${prefix} ${new Date(ts).toLocaleString()}` : "";
                    noteUpdated.textContent = label;
                }
            }

            function maybeOfferDraft(noteId, serverContent, serverUpdatedAt) {
                const draft = readDraft(noteId);
                if (draft && draft.content && draft.content !== (serverContent || "")) {
                    const draftTime = draft.savedAt ? new Date(draft.savedAt) : null;
                    const serverTime = serverUpdatedAt ? new Date(serverUpdatedAt) : null;
                    const newer = draftTime && serverTime ? draftTime > serverTime : true;
                    const label = newer ? "Recovered newer local draft" : "Local draft available";
                    renderNoteRecovery(label, () => {
                        if (noteInput) {
                            noteInput.value = draft.content;
                            renderNotePreview();
                            markNoteDirty(true);
                            scheduleAutosave("restore-draft");
                        }
                        hideNoteRecovery();
                        showToast("Draft restored", "success");
                    });
                    showToast(label, "info", {
                        actionText: "Restore",
                        onAction: () => {
                            if (noteInput) {
                                noteInput.value = draft.content;
                                renderNotePreview();
                                markNoteDirty(true);
                                scheduleAutosave("restore-draft");
                            }
                            hideNoteRecovery();
                        },
                        duration: 7000,
                    });
                    setStatus(label, "info", { ttl: 6400 });
                    return;
                }
                hideNoteRecovery();
            }

            function hasLocalDraft(noteId, serverUpdatedAt) {
                const draft = readDraft(noteId);
                if (!draft || !draft.content) return false;
                if (!serverUpdatedAt) return true;
                try {
                    const draftTime = draft.savedAt ? new Date(draft.savedAt) : null;
                    const serverTime = serverUpdatedAt ? new Date(serverUpdatedAt) : null;
                    if (!draftTime) return false;
                    if (!serverTime) return true;
                    return draftTime > serverTime;
                } catch (e) {
                    return true;
                }
            }

            async function refreshNotes(autoSelect = true, notifySuccess = false) {
                if (noteStatus) {
                    noteStatus.textContent = "Loading notes…";
                }
                setStatus("Loading notes…", "info", { ttl: 4200 });
                try {
                    const data = await fetchJson("/api/notes");
                    noteCache = data.notes || [];
                    notesLoadedOnce = true;
                    renderNotesList(noteCache);
                    if (noteStatus) {
                        noteStatus.textContent = noteCache.length ? `${noteCache.length} stored note(s)` : "No notes yet — start with a new one.";
                    }
                    setStatus(`Notebook synced (${noteCache.length} note${noteCache.length === 1 ? "" : "s"})`, "success", { ttl: 3200 });
                    if (notifySuccess) {
                        showToast("Notes refreshed", "success", { duration: 1800 });
                    }
                    if (autoSelect) {
                        if (activeNoteId && noteCache.some(n => n.id === activeNoteId)) return;
                        if (noteCache.length) {
                            await loadNote(noteCache[0].id);
                        } else {
                            newNote();
                        }
                    }
                } catch (e) {
                    console.warn("Failed to load notes", e);
                    if (noteStatus) noteStatus.textContent = "Notes unavailable — please retry.";
                    surfaceError(e.message || "Failed to load notes");
                }
            }

            function renderNotesList(notes) {
                if (!noteList) return;
                noteList.innerHTML = "";
                if (!notes || !notes.length) {
                    const empty = document.createElement("div");
                    empty.className = "note-subtle";
                    empty.textContent = "No notes yet.";
                    noteList.appendChild(empty);
                    return;
                }
                notes.forEach(n => {
                    const item = document.createElement("div");
                    item.className = "note-item" + (n.id === activeNoteId ? " active" : "");
                    const title = document.createElement("div");
                    title.className = "title";
                    const display = formatNoteListTitle(n.title || "Untitled note");
                    title.textContent = display;
                    title.title = n.title || "Untitled note";
                    const meta = document.createElement("div");
                    meta.className = "meta";
                    const updated = n.updated_at ? new Date(n.updated_at).toLocaleString() : "";
                    meta.textContent = updated ? `Updated ${updated}` : "Draft";
                    item.appendChild(title);
                    item.appendChild(meta);
                    if (hasLocalDraft(n.id, n.updated_at)) {
                        const draftTag = document.createElement("div");
                        draftTag.className = "note-pill inline";
                        draftTag.textContent = "Local draft";
                        draftTag.style.marginTop = "6px";
                        item.appendChild(draftTag);
                    }
                    item.onclick = () => selectNote(n.id);
                    noteList.appendChild(item);
                });
            }

            async function loadNote(noteId) {
                if (!noteId) return;
                setStatus("Loading note…", "info", { ttl: 2600 });
                try {
                    const data = await fetchJson(`/api/notes/${noteId}`);
                    const note = data.note;
                    activeNoteId = note.id;
                    activeNoteVersion = note.updated_at || null;
                    lastSavedContent = note.content || "";
                    if (noteInput) noteInput.value = note.content || "";
                    markNoteDirty(false);
                    renderNotePreview();
                    updateNoteMeta(note.updated_at, "Last saved");
                    renderNotesList(noteCache);
                    maybeOfferDraft(note.id, note.content, note.updated_at);
                    setStatus(`Loaded "${deriveNoteTitle(note.content)}"`, "success", { ttl: 2600 });
                } catch (e) {
                    console.warn("Failed to load note", e);
                    if (noteStatus) noteStatus.textContent = "Failed to load note.";
                    surfaceError(e.message || "Failed to load note");
                }
            }

            async function saveActiveNote(options = {}) {
                if (!noteInput) return null;
                const content = noteInput.value || "";
                const isAutosave = !!options.isAutosave;
                const reason = options.reason || "manual";
                if (isAutosave && !noteChanged) {
                    return null;
                }
                if (noteAutosaveTimer) {
                    clearTimeout(noteAutosaveTimer);
                    noteAutosaveTimer = null;
                }
                if (!content.trim() && !activeNoteId) {
                    markNoteDirty(false);
                    hideNoteRecovery();
                    clearDraft(null);
                    return null;
                }
                const payload = { content, title: deriveNoteTitle(content) };
                if (activeNoteVersion) {
                    payload.updated_at = activeNoteVersion;
                }
                const url = activeNoteId ? `/api/notes/${activeNoteId}` : "/api/notes";
                const method = activeNoteId ? "PUT" : "POST";
                const statusLabel = isAutosave ? "Autosaving…" : "Saving…";
                setNoteSaving(true, statusLabel);
                setStatus(statusLabel.replace("…", "..."), "info", { ttl: isAutosave ? 3600 : 5200 });
                try {
                    const data = await fetchJson(url, {
                        method,
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload),
                    });
                    const note = data.note;
                    activeNoteId = note.id;
                    activeNoteVersion = note.updated_at || null;
                    lastSavedContent = note.content || content;
                    markNoteDirty(false);
                    updateNoteMeta(note.updated_at, isAutosave ? "Autosaved" : "Last saved");
                    hideNoteRecovery();
                    clearDraft(null);
                    clearDraft(activeNoteId);
                    await refreshNotes(false);
                    setStatus(isAutosave ? "Autosaved" : "Note saved", "success", { ttl: isAutosave ? 2400 : 3600 });
                    if (!isAutosave) {
                        showToast("Note saved", "success");
                    } else if (options.reason === "exit-note") {
                        showToast("Autosaved before leaving notes", "info", { duration: 2400 });
                    }
                    return note;
                } catch (e) {
                    if (e.status === 409) {
                        const serverNote =
                            (e.payload && e.payload.detail && e.payload.detail.note) || (e.payload && e.payload.note);
                        const userCopy = content;
                        if (serverNote) {
                            activeNoteId = serverNote.id || activeNoteId;
                            activeNoteVersion = serverNote.updated_at || null;
                            lastSavedContent = serverNote.content || "";
                            if (noteInput) noteInput.value = serverNote.content || "";
                            renderNotePreview();
                            updateNoteMeta(serverNote.updated_at, "Reloaded");
                            markNoteDirty(false);
                        }
                        persistDraft(userCopy, activeNoteId);
                        showToast("Newer version found — loaded server copy.", "error", {
                            actionText: "Reapply my draft",
                            onAction: () => {
                                if (noteInput) {
                                    noteInput.value = userCopy;
                                    renderNotePreview();
                                    markNoteDirty(true);
                                    scheduleAutosave("conflict");
                                }
                            },
                            duration: 9000,
                        });
                        renderNoteRecovery("Conflict detected — newer copy on server.", () => {
                            if (noteInput) {
                                noteInput.value = userCopy;
                                renderNotePreview();
                                markNoteDirty(true);
                                scheduleAutosave("conflict");
                            }
                            showToast("Draft restored", "success");
                        });
                        setStatus("Loaded newer server copy; your draft is preserved.", "error", { ttl: 9000 });
                        return null;
                    }
                    console.warn("Failed to save note", e);
                    if (noteStatus) noteStatus.textContent = "Save failed — try again.";
                    surfaceError(e.message || "Failed to save note");
                    persistDraft(content, activeNoteId);
                    return null;
                } finally {
                    setNoteSaving(false);
                }
            }

            async function maybeSaveActiveNote(reason = "autosave") {
                if (noteChanged) {
                    return await saveActiveNote({ isAutosave: true, reason });
                }
                return null;
            }

            async function selectNote(noteId) {
                if (noteId === activeNoteId) return;
                await maybeSaveActiveNote("switch-note");
                await loadNote(noteId);
            }

            function newNote() {
                activeNoteId = null;
                activeNoteVersion = null;
                lastSavedContent = "";
                hideNoteRecovery();
                if (noteAutosaveTimer) {
                    clearTimeout(noteAutosaveTimer);
                    noteAutosaveTimer = null;
                }
                setStatus("Starting a new draft", "info", { ttl: 2400 });
                let restoredDraft = null;
                if (noteInput) {
                    const draft = readDraft(null);
                    restoredDraft = draft && draft.content;
                    noteInput.value = restoredDraft || "";
                    noteInput.focus();
                }
                markNoteDirty(!!restoredDraft);
                if (restoredDraft) {
                    persistDraft(restoredDraft, null);
                    scheduleAutosave("restore-draft");
                }
                renderNotePreview();
                if (noteUpdated) noteUpdated.textContent = "Draft note — not saved yet.";
                maybeOfferDraft(null, noteInput ? noteInput.value : "", null);
                renderNotesList(noteCache);
            }

            async function enterNoteMode() {
                noteMode = true;
                document.body.classList.add("note-mode");
                document.body.classList.remove("show-history");
                historyOpen = false;
                setStatus("Entering note mode…", "info", { ttl: 3200 });
                if (noteStatus) noteStatus.textContent = "Loading notebook…";
                if (historyBtn) {
                    historyBtn.textContent = "History";
                    historyBtn.disabled = true;
                }
                if (!notesLoadedOnce) {
                    await refreshNotes();
                } else {
                    await refreshNotes(false);
                    if (activeNoteId) {
                        await loadNote(activeNoteId);
                    } else if (noteCache.length) {
                        await loadNote(noteCache[0].id);
                    } else {
                        maybeOfferDraft(null, noteInput ? noteInput.value : "", null);
                    }
                }
                if (noteInput) noteInput.focus();
                setStatus("Notebook ready", "success", { ttl: 2400 });
                flushMathQueue();
            }

            async function exitNoteMode() {
                if (noteAutosaveTimer) {
                    clearTimeout(noteAutosaveTimer);
                    noteAutosaveTimer = null;
                }
                if (noteChanged) {
                    setStatus("Autosaving before leaving notes…", "info", { ttl: 3200 });
                }
                await autosaveNote("exit-note");
                noteMode = false;
                document.body.classList.remove("note-mode");
                setStatus("Back to chat mode", "success", { ttl: 2200 });
                if (historyBtn) {
                    historyBtn.disabled = false;
                }
            }

            async function uploadImage(file) {
                const form = new FormData();
                form.append("file", file, file.name || "pasted-image");
                try {
                    const data = await fetchJson("/api/note_assets", { method: "POST", body: form });
                    showToast("Image uploaded", "success", { duration: 1800 });
                    return data.path;
                } catch (e) {
                    surfaceError(e.message || "Upload failed");
                    return null;
                }
            }

            async function handleNotePaste(event) {
                if (!event.clipboardData) return;
                const items = event.clipboardData.items || [];
                for (const item of items) {
                    if (item.kind === "file") {
                        const file = item.getAsFile();
                        if (file && file.type && file.type.startsWith("image/")) {
                            event.preventDefault();
                            const path = await uploadImage(file);
                            if (path) {
                                insertAtCursor(noteInput, `![pasted image](${path})\\n`);
                                renderNotePreview();
                                markNoteDirty(true);
                                persistDraft();
                                scheduleAutosave("pasted-image");
                            }
                            return;
                        }
                    }
                }
            }

            function insertAtCursor(textarea, text) {
                if (!textarea) return;
                const start = textarea.selectionStart || 0;
                const end = textarea.selectionEnd || 0;
                const before = textarea.value.substring(0, start);
                const after = textarea.value.substring(end);
                textarea.value = before + text + after;
                const pos = start + text.length;
                textarea.selectionStart = textarea.selectionEnd = pos;
            }

            function insertTimestamp() {
                if (!noteInput) return;
                const now = new Date();
                const pad = (v) => String(v).padStart(2, "0");
                const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
                insertAtCursor(noteInput, `${stamp}\\n`);
                markNoteDirty(true);
                renderNotePreview();
                persistDraft();
                scheduleAutosave("timestamp");
                noteInput.focus();
            }

            function renderMessage(role, content, chunkId, createdAt, debug, metadata) {
                const wrap = document.createElement('div');
                wrap.className = 'msg role-' + (role || 'system');
                if (chunkId) wrap.dataset.chunkId = chunkId;
                const roleEl = document.createElement('div');
                roleEl.className = 'role';
                const ts = createdAt ? new Date(createdAt).toLocaleString() : new Date().toLocaleString();
                roleEl.textContent = role + " • " + ts;
                const bubble = document.createElement('div');
                bubble.className = 'bubble';
                // Set context for image URL resolution
                currentMessageUrl = getMessageSourceUrl(metadata);
                bubble.innerHTML = marked.parse(content || '');
                currentMessageUrl = null; // Reset after parsing
                const actions = document.createElement('div');
                actions.className = 'actions';
                const del = document.createElement('button');
                del.textContent = '✕';
                del.title = chunkId ? 'Delete' : 'Delete unavailable';
                del.disabled = !chunkId;
                del.onclick = () => chunkId && deleteMessage(chunkId, wrap);
                actions.appendChild(del);
                const copyBtn = document.createElement('button');
                copyBtn.textContent = 'Copy';
                copyBtn.onclick = () => copyMessage(content);
                actions.appendChild(copyBtn);
                if (role !== 'user') {
                    const sections = [];
                    if (metadata && Object.keys(metadata).length) {
                        sections.push({ label: metadata.source ? `Trace: ${metadata.source}` : 'Trace: metadata', payload: metadata });
                    }
                    if (debug) {
                        const label = debug.kind ? `Trace: ${debug.kind}` : 'Trace: debug';
                        sections.push({ label, payload: debug });
                    }
                    sections.forEach(sec => {
                        const details = document.createElement('details');
                        details.className = 'debug-block';
                        details.classList.add('debug-only');
                        const summary = document.createElement('summary');
                        summary.textContent = sec.label;
                        const pre = document.createElement('pre');
                        pre.textContent = JSON.stringify(sec.payload, null, 2);
                        details.appendChild(summary);
                        details.appendChild(pre);
                        bubble.appendChild(details);
                    });
                }
                wrap.appendChild(actions);
                wrap.appendChild(roleEl);
                wrap.appendChild(bubble);
                logEl.appendChild(wrap);
                logEl.scrollTop = logEl.scrollHeight;
            }

            async function copyMessage(text) {
                try {
                    await navigator.clipboard.writeText(text || '');
                } catch (e) {
                    console.warn('copy failed', e);
                }
            }

            async function deleteMessage(chunkId, el) {
                try {
                    const data = await fetchJson(`/api/messages/${chunkId}`, { method: 'DELETE' });
                    if (data.deleted && el) {
                        el.remove();
                    }
                } catch (e) {
                    console.warn("Delete failed", e);
                    surfaceError(e.message || "Failed to delete message");
                }
            }

            async function sendChat() {
                if (sending) return;
                const text = inputEl.value.trim();
                if (!text) return;
                if (text === '/note') {
                    inputEl.value = '';
                    setStatus("Opening notebook…", "info", { ttl: 2400 });
                    await enterNoteMode();
                    return;
                }
                if (text === '/clear') {
                    if (logEl) logEl.innerHTML = '';
                    inputEl.value = '';
                    clearStatus();
                    setSending(false);
                    return;
                }
                if (text === '/rss') {
                    inputEl.value = '';
                    renderMessage('system', '[rss] fetching feeds...', null, new Date().toISOString());
                    setSending(true);
                    setStatus("Refreshing RSS feeds…", "info", { ttl: 6000 });
                    try {
                        const debug = !!(debugToggle && debugToggle.checked);
                        const data = await fetchJson('/api/rss', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ debug })
                        });
                        (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at, m.debug, m.metadata));
                        showToast("RSS refresh complete", "success");
                        setStatus("RSS refresh complete", "success", { ttl: 3200 });
                    } catch (e) {
                        console.warn('rss failed', e);
                        renderMessage('system', `[rss] failed: ${e.message || 'error'}`, null, new Date().toISOString());
                        surfaceError(e.message || "RSS fetch failed");
                    } finally {
                        setSending(false);
                        if (inputEl) inputEl.focus();
                    }
                    return;
                }
                renderMessage('user', text, null, new Date().toISOString());
                inputEl.value = '';
                setSending(true);
                setStatus("Contacting backend…", "info", { ttl: 6000 });
                try {
                    const data = await fetchJson('/api/chat', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ text, debug: !!(debugToggle && debugToggle.checked) })
                    });
                    (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at, m.debug, m.metadata));
                    if (!data.messages || !data.messages.length) {
                        renderMessage('system', 'No response from backend.', null, new Date().toISOString());
                        surfaceError("Backend returned no response", { duration: 4200 });
                    } else {
                        setStatus(`LLM replied at ${new Date().toLocaleTimeString()}`, "success", { ttl: 3600 });
                    }
                } catch (e) {
                    console.warn('chat failed', e);
                    renderMessage('system', `Chat failed: ${e.message || 'error'}`, null, new Date().toISOString());
                    surfaceError(e.message || "Chat failed");
                } finally {
                    setSending(false);
                    if (inputEl) inputEl.focus();
                }
            }

            function isTodayDay(dayStr) {
                const today = new Date().toISOString().slice(0, 10);
                return dayStr === today;
            }

            function formatMonthLabel(monthStr) {
                if (!monthStr) return "Unknown month";
                try {
                    const [y, m] = monthStr.split("-");
                    const d = new Date(`${y}-${m}-01T00:00:00Z`);
                    return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
                } catch (e) {
                    return monthStr;
                }
            }

            function createHistoryItem(m) {
                const item = document.createElement('div');
                item.className = 'item role-' + (m.role || 'system');
                const ts = m.created_at ? new Date(m.created_at).toLocaleString() : '';
                const roleEl = document.createElement('div');
                roleEl.className = 'role';
                roleEl.textContent = `${m.role} • ${ts}`;
                const body = document.createElement('div');
                body.className = 'history-body';
                // Set context for image URL resolution
                currentMessageUrl = getMessageSourceUrl(m.metadata);
                body.innerHTML = marked.parse(m.content || '');
                currentMessageUrl = null; // Reset after parsing
                const actions = document.createElement('div');
                actions.className = 'actions';
                const del = document.createElement('button');
                del.textContent = '✕';
                del.title = m.chunk_id ? 'Delete' : 'Delete unavailable';
                del.disabled = !m.chunk_id;
                del.onclick = () => m.chunk_id && deleteMessage(m.chunk_id, item);
                const copyBtn = document.createElement('button');
                copyBtn.textContent = 'Copy';
                copyBtn.onclick = () => copyMessage(m.content);
                actions.appendChild(del);
                actions.appendChild(copyBtn);
                item.appendChild(actions);
                item.appendChild(roleEl);
                item.appendChild(body);
                const shouldCollapse = (m.content || '').length > 360 || (body.textContent || '').length > 360;
                if (shouldCollapse) {
                    body.classList.add('collapsed');
                    const toggle = document.createElement('button');
                    toggle.className = 'history-expand';
                    toggle.textContent = 'Expand';
                    toggle.onclick = () => {
                        const collapsed = body.classList.toggle('collapsed');
                        toggle.textContent = collapsed ? 'Expand' : 'Collapse';
                    };
                    item.appendChild(toggle);
                }
                return item;
            }

            function renderDayMessages(day, messages, container) {
                if (!container) return;
                container.innerHTML = '';
                if (!messages || !messages.length) {
                    const empty = document.createElement('div');
                    empty.className = 'history-subtle';
                    empty.textContent = 'No messages for this day.';
                    container.appendChild(empty);
                    return;
                }
                messages.forEach(m => container.appendChild(createHistoryItem(m)));
            }

            async function fetchHistoryDay(day) {
                const data = await fetchJson(`/api/messages?day=${encodeURIComponent(day)}`);
                const msgs = data.messages || [];
                historyLoadedDays[day] = msgs;
                return msgs;
            }

            function groupDaysByMonth(days) {
                const buckets = {};
                (days || []).forEach(d => {
                    const month = (d.day || '').slice(0, 7);
                    if (!month) return;
                    if (!buckets[month]) buckets[month] = [];
                    buckets[month].push(d);
                });
                const order = Object.keys(buckets).sort().reverse();
                return order.map(month => ({
                    month,
                    days: buckets[month].sort((a, b) => {
                        if (a.day === b.day) return 0;
                        return a.day < b.day ? 1 : -1;
                    }),
                }));
            }

            function renderHistoryDays() {
                if (!historyList) return;
                historyList.innerHTML = '';
                if (!historyDayMeta.length) {
                    const empty = document.createElement('div');
                    empty.className = 'history-subtle';
                    empty.textContent = 'No history yet.';
                    historyList.appendChild(empty);
                    return;
                }
                const grouped = groupDaysByMonth(historyDayMeta);
                grouped.forEach(group => {
                    const monthWrap = document.createElement('div');
                    monthWrap.className = 'history-month';
                    const monthHeader = document.createElement('div');
                    monthHeader.className = 'history-month-header';
                    const monthLabel = document.createElement('div');
                    monthLabel.className = 'history-month-title';
                    monthLabel.textContent = formatMonthLabel(group.month);
                    const monthCount = document.createElement('div');
                    monthCount.className = 'history-month-count';
                    const totalCount = group.days.reduce((acc, d) => acc + (d.count || 0), 0);
                    monthCount.textContent = `${totalCount} message${totalCount === 1 ? '' : 's'}`;
                    monthHeader.appendChild(monthLabel);
                    monthHeader.appendChild(monthCount);
                    monthHeader.onclick = () => toggleMonth(group.month);
                    monthWrap.appendChild(monthHeader);
                    const daysWrap = document.createElement('div');
                    daysWrap.className = 'history-days';
                    daysWrap.dataset.month = group.month;
                    daysWrap.style.display = expandedMonths.has(group.month) ? 'flex' : 'none';
                    group.days.forEach(d => {
                        const dayWrap = document.createElement('div');
                        dayWrap.className = 'history-day';
                        dayWrap.dataset.day = d.day;
                        const dayHeader = document.createElement('div');
                        dayHeader.className = 'history-day-header';
                        const label = document.createElement('div');
                        label.className = 'history-day-label';
                        label.textContent = isTodayDay(d.day) ? `Today (${d.day})` : d.day;
                        const count = document.createElement('div');
                        count.className = 'history-day-count';
                        count.textContent = `${d.count || 0} message${(d.count || 0) === 1 ? '' : 's'}`;
                        dayHeader.appendChild(label);
                        dayHeader.appendChild(count);
                        dayHeader.onclick = () => toggleDay(d.day);
                        const body = document.createElement('div');
                        body.className = 'history-day-body';
                        if (openHistoryDays.has(d.day)) {
                            dayWrap.classList.add('open');
                            body.style.display = 'block';
                            if (historyLoadedDays[d.day]) {
                                renderDayMessages(d.day, historyLoadedDays[d.day], body);
                            }
                        }
                        dayWrap.appendChild(dayHeader);
                        dayWrap.appendChild(body);
                        daysWrap.appendChild(dayWrap);
                    });
                    monthWrap.appendChild(daysWrap);
                    historyList.appendChild(monthWrap);
                });
            }

            function toggleMonth(month) {
                if (expandedMonths.has(month)) {
                    expandedMonths.delete(month);
                } else {
                    expandedMonths.add(month);
                }
                renderHistoryDays();
            }

            async function toggleDay(day, forceOpen = false) {
                if (!historyList) return;
                const dayEl = historyList.querySelector(`[data-day="${day}"]`);
                if (!dayEl) return;
                const body = dayEl.querySelector('.history-day-body');
                const isOpen = openHistoryDays.has(day);
                if (isOpen && !forceOpen) {
                    openHistoryDays.delete(day);
                    dayEl.classList.remove('open');
                    body.style.display = 'none';
                    body.innerHTML = '';
                    return;
                }
                openHistoryDays.add(day);
                dayEl.classList.add('open');
                body.style.display = 'block';
                if (!historyLoadedDays[day]) {
                    body.innerHTML = '<div class="history-subtle">Loading...</div>';
                    try {
                        const msgs = await fetchHistoryDay(day);
                        renderDayMessages(day, msgs, body);
                    } catch (e) {
                        console.warn('history day load failed', e);
                        body.innerHTML = '<div class="history-subtle">Failed to load.</div>';
                        surfaceError(e.message || "Failed to load history day");
                    }
                } else {
                    renderDayMessages(day, historyLoadedDays[day], body);
                }
            }

            async function openDay(day) {
                const month = (day || '').slice(0, 7);
                if (month) expandedMonths.add(month);
                renderHistoryDays();
                await toggleDay(day, true);
            }

            async function loadHistory(autoExpand = true, notifySuccess = false) {
                if (historyList) {
                    historyList.innerHTML = '<div class="history-subtle">Loading history…</div>';
                }
                setStatus("Loading history…", "info", { ttl: 3600 });
                try {
                    const data = await fetchJson('/api/messages/days');
                    historyDayMeta = data.days || [];
                    if (!expandedMonths.size && historyDayMeta.length) {
                        const todayMonth = new Date().toISOString().slice(0, 7);
                        const hasToday = historyDayMeta.some(d => d.day && d.day.startsWith(todayMonth));
                        if (hasToday) expandedMonths.add(todayMonth);
                    }
                    renderHistoryDays();
                    if (autoExpand && historyDayMeta.length) {
                        const today = new Date().toISOString().slice(0, 10);
                        const preferred = historyDayMeta.find(d => d.day === today)?.day || historyDayMeta[0].day;
                        if (preferred) {
                            await openDay(preferred);
                        }
                    }
                    setStatus("History refreshed", "success", { ttl: 2600 });
                    if (notifySuccess) {
                        showToast("History refreshed", "success", { duration: 1800 });
                    }
                } catch (e) {
                    console.warn('history load failed', e);
                    if (historyList) historyList.innerHTML = '<div class="history-subtle">History unavailable.</div>';
                    surfaceError(e.message || "History unavailable");
                }
            }

            function toggleDebug() {
                const on = debugToggle.checked;
                document.body.classList.toggle('debug-mode', on);
            }

            function refreshHistory() {
                historyLoadedDays = {};
                historyDayMeta = [];
                openHistoryDays = new Set();
                expandedMonths = new Set();
                loadHistory(true, true);
            }

            function toggleHistory() {
                if (noteMode) return;
                historyOpen = !historyOpen;
                document.body.classList.toggle('show-history', historyOpen);
                if (historyOpen) {
                    historyLoadedDays = {};
                    historyDayMeta = [];
                    openHistoryDays = new Set();
                    expandedMonths = new Set();
                    loadHistory();
                    if (historyBtn) historyBtn.textContent = 'Hide history';
                    setStatus("History opened", "info", { ttl: 2200 });
                } else {
                    if (historyBtn) historyBtn.textContent = 'History';
                    setStatus("History hidden", "info", { ttl: 1800 });
                }
            }

            function startResize(e) {
                isResizing = true;
                document.body.classList.add('resizing');
                e.preventDefault();
            }

            function onResize(e) {
                if (!isResizing || !historyWrapper) return;
                const min = 240;
                const max = 520;
                const rect = historyWrapper.getBoundingClientRect();
                const newWidth = Math.min(Math.max(rect.right - e.clientX, min), max);
                historyWrapper.style.width = `${newWidth}px`;
            }

            function stopResize() {
                isResizing = false;
                document.body.classList.remove('resizing');
            }

            if (historyResize) {
                historyResize.addEventListener('mousedown', startResize);
                window.addEventListener('mousemove', onResize);
                window.addEventListener('mouseup', stopResize);
            }
            document.body.classList.remove('show-history');

            if (noteInput) {
                noteInput.addEventListener('input', () => {
                    const content = noteInput.value;
                    const changed = content !== lastSavedContent;
                    markNoteDirty(changed);
                    renderNotePreview();
                    if (changed) {
                        persistDraft(content);
                        scheduleAutosave("typing");
                    }
                });
                noteInput.addEventListener('paste', handleNotePaste);
                noteInput.addEventListener('keydown', (e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                        e.preventDefault();
                        saveActiveNote();
                    }
                });
                noteInput.addEventListener('blur', () => {
                    if (noteChanged) {
                        persistDraft();
                        scheduleAutosave("blur");
                    }
                });
            }
            window.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden" && noteMode && noteInput && noteChanged) {
                    persistDraft();
                    autosaveNote("visibilitychange");
                }
            });
            window.addEventListener("beforeunload", () => {
                if (noteInput && noteChanged) {
                    persistDraft();
                }
            });
            if (mathScript) {
                mathScript.addEventListener('load', flushMathQueue);
            }
            if (debugToggle) {
                debugToggle.checked = true;
                toggleDebug();
            }
            renderNotePreview();
            flushMathQueue();
            setSending(false);

            document.addEventListener('keydown', (e) => {
                if (noteMode) return;
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    if (sending) {
                        e.preventDefault();
                        return;
                    }
                    e.preventDefault();
                    sendChat();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/notes")
def api_list_notes(limit: int = 50) -> dict:
    notes = workspace.list_notes(limit=limit)
    return {"notes": [note_dict(n) for n in notes]}


@app.get("/api/notes/{note_id}")
def api_get_note(note_id: int) -> dict:
    note = workspace.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note_dict(note)}


@app.post("/api/notes")
def api_create_note(payload=Body(...)) -> dict:
    content = (payload or {}).get("content", "")
    title = (payload or {}).get("title")
    try:
        note = workspace.save_note(content or "", title=title)
    except NoteConflictError as e:
        raise HTTPException(status_code=409, detail={"reason": "conflict", "note": note_dict(e.note) if e.note else None})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not note:
        raise HTTPException(status_code=500, detail="failed to save note")
    return {"note": note_dict(note)}


@app.put("/api/notes/{note_id}")
def api_update_note(note_id: int, payload=Body(...)) -> dict:
    content = (payload or {}).get("content", "")
    title = (payload or {}).get("title")
    expected_updated_at = (payload or {}).get("updated_at") or (payload or {}).get("expected_updated_at")
    try:
        note = workspace.save_note(content or "", title=title, note_id=note_id, expected_updated_at=expected_updated_at)
    except NoteConflictError as e:
        raise HTTPException(status_code=409, detail={"reason": "conflict", "note": note_dict(e.note) if e.note else None})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note_dict(note)}


@app.post("/api/note_assets")
async def upload_note_asset(file: UploadFile = File(...)) -> dict:
    if not file:
        raise HTTPException(status_code=400, detail="missing file")
    suffix = Path(file.filename or "").suffix or ".bin"
    safe_suffix = suffix if suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else ".bin"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    fname = f"{int(datetime.now(UTC).timestamp() * 1000)}-{secrets.token_hex(4)}{safe_suffix}"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = IMAGES_DIR / fname
    out_path.write_bytes(data)
    rel_path = f"/assets/img/{fname}"
    return {"path": rel_path}


@app.get("/api/messages")
def get_messages(day: str | None = None, limit: int = 50, mode: str | None = None) -> dict:
    """
    Fetch messages. Default: messages from today.
    mode=recent (or day=all) preserves old behavior of last-N messages.
    """
    day = (day or "").strip()
    mode = (mode or "").strip().lower()
    if mode == "recent" or day.lower() == "all":
        msgs: List[Message] = workspace.list_recent(limit)
    else:
        target_day = day or datetime.now(UTC).date().isoformat()
        try:
            parsed = datetime.fromisoformat(target_day).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid day; expected YYYY-MM-DD")
        msgs = workspace.list_messages_by_day(parsed.isoformat(), limit=limit)
    def _ts(msg: Message) -> float:
        dt = msg.created_at
        if dt.tzinfo:
            return dt.timestamp()
        return dt.replace(tzinfo=UTC).timestamp()
    msgs = sorted(msgs, key=_ts)
    return {"messages": [msg_to_dict(m) for m in msgs]}


@app.get("/api/messages/days")
def get_message_days(limit: int = 180) -> dict:
    days = workspace.list_message_days(limit=limit)
    return {"days": days}


@app.delete("/api/messages/{chunk_id}")
def delete_message(chunk_id: int) -> dict:
    ok = workspace.delete_chunk(chunk_id)
    return {"deleted": ok}


@app.post("/api/chat")
def chat(payload=Body(...)) -> dict:
    text = (payload or {}).get("text", "")
    debug = bool((payload or {}).get("debug"))
    if not text:
        return {"messages": []}
    try:
        if text.strip().startswith("/add"):
            logs = handle_add(text.strip().removeprefix("/add").strip(), workspace, orchestrator, debug=debug)
            return {"messages": [make_system_msg(log["text"]) | {"debug": log.get("debug")} for log in logs]}
        
        # Handle /find command for semantic search
        if text.strip().startswith("/find"):
            query = text.strip().removeprefix("/find").strip()
            if not query:
                return {"messages": [make_system_msg("[find] Please provide a search query. Usage: /find <query>")]}
            
            try:
                from retrieval import SearchRequest
                
                # Parse filters from query (simple implementation)
                # e.g., "/find type:note kubernetes" → filters={'doc_type': 'note'}, query='kubernetes'
                filters = {}
                query_parts = []
                
                for part in query.split():
                    if ':' in part and part.split(':')[0] in ['type', 'tag', 'before', 'after', 'time']:
                        key, value = part.split(':', 1)
                        if key == 'type':
                            filters['doc_type'] = value
                        elif key == 'tag':
                            filters['tags'] = value
                        elif key == 'before':
                            filters['before'] = value
                        elif key == 'after':
                            filters['after'] = value
                        elif key == 'time':
                            # time:event or time:created to select which timestamp field to use
                            filters['time_field'] = f'{value}_at' if value in ['event', 'created', 'ingested'] else 'created_at'
                    else:
                        query_parts.append(part)
                
                from config import SHIYE_SEARCH_TOP_K
                
                clean_query = ' '.join(query_parts) if query_parts else query
                request = SearchRequest(query=clean_query, filters=filters, top_k=SHIYE_SEARCH_TOP_K, debug=debug)
                hits = workspace.search(request)
                
                if not hits:
                    return {"messages": [make_system_msg(f"[find] No results found for: {clean_query}")]}
                
                # Prepare debug context and helpers
                debug_info = workspace.get_last_search_debug_info() if debug else None
                score_order = (debug_info.get("score_keys") if debug_info else None) or ["dense", "sparse", "exact", "fused", "rerank", "recency_boost", "type_boost", "exact_match_boost", "final"]

                def fmt_score_value(val):
                    try:
                        return f"{float(val):.4f}"
                    except Exception:
                        return str(val)

                def render_score_chips(scores: dict) -> str:
                    if not scores:
                        return ""
                    chips = []
                    seen = set()
                    for stage in score_order:
                        if stage in scores:
                            chips.append(f"<span class='score-chip'><span class='score-label'>{stage}</span> {fmt_score_value(scores.get(stage))}</span>")
                            seen.add(stage)
                    for stage, value in (scores or {}).items():
                        if stage in seen:
                            continue
                        chips.append(f"<span class='score-chip'><span class='score-label'>{stage}</span> {fmt_score_value(value)}</span>")
                    if not chips:
                        return ""
                    return "<div class='hit-scores debug-only'><div class='score-chip-row'>" + "".join(chips) + "</div></div>"

                def render_score_chart(candidates: list) -> str:
                    """Render a simple multi-line dot plot for key score stages."""
                    if not candidates:
                        return "<div class='debug-item'>No candidates to chart.</div>"
                    top = sorted(candidates, key=lambda c: c.get("rank") or 0)[:20]
                    stages = ["dense", "sparse", "fused", "rerank", "final"]
                    colors = {
                        "dense": "#5b8def",
                        "sparse": "#fb923c",
                        "fused": "#22c55e",
                        "rerank": "#a855f7",
                        "final": "#111827",
                    }
                    max_score = 0.0
                    for cand in top:
                        sh = cand.get("score_history") or {}
                        for stage in stages:
                            if stage in sh and isinstance(sh[stage], (int, float)):
                                max_score = max(max_score, float(sh[stage]))
                        if isinstance(cand.get("final_score"), (int, float)):
                            max_score = max(max_score, float(cand["final_score"]))
                    if max_score <= 0:
                        max_score = 1.0
                    n = len(top)
                    step = max(24, int(320 / max(1, n - 1)))
                    width = max(360, step * (n - 1) + 40)
                    height = 140
                    points = {stage: [] for stage in stages}
                    rects = []
                    labels = []
                    for idx, cand in enumerate(top):
                        x = 20 + idx * step
                        sh = cand.get("score_history") or {}
                        final_val = cand.get("final_score") if cand.get("final_score") is not None else sh.get("final")
                        if isinstance(final_val, (int, float)):
                            fy = height - (float(final_val) / max_score) * (height - 20)
                            rects.append(f"<rect x='{x-4}' y='{fy}' width='8' height='{height-fy}' fill='{colors['final']}' opacity='0.4' rx='2' />")
                        for stage in stages:
                            if stage == "final":
                                val = final_val
                            else:
                                val = sh.get(stage)
                            if not isinstance(val, (int, float)):
                                continue
                            y = height - (float(val) / max_score) * (height - 20)
                            points[stage].append((x, y))
                        labels.append(f"<text x='{x}' y='{height + 12}' text-anchor='middle' font-size='10' fill='#6b7280'>#{cand.get('rank')}</text>")
                    polylines = []
                    dots = []
                    for stage in stages:
                        if not points[stage]:
                            continue
                        pts = " ".join(f"{x},{y:.2f}" for x, y in points[stage])
                        polylines.append(f"<polyline fill='none' stroke='{colors[stage]}' stroke-width='{3 if stage=='final' else 2}' points='{pts}' />")
                        dots.extend(
                            f"<circle cx='{x}' cy='{y:.2f}' r='3' fill='{colors[stage]}' />"
                            for x, y in points[stage]
                        )
                    legend_items = "".join(
                        f"<span class='swatch'><span class='dot' style='background:{colors[s]}'></span>{s}</span>"
                        for s in stages if points[s]
                    )
                    legend = f"<div class='legend'>{legend_items}</div>"
                    svg_parts = [
                        f"<svg viewBox='0 0 {width} {height+20}' role='img' aria-label='Score drop-off'>",
                        *rects,
                        *polylines,
                        *dots,
                        *labels,
                        "</svg>",
                    ]
                    return "<div class='score-chart'>" + "".join(svg_parts) + legend + "</div>"

                def render_debug_panel(info: dict) -> str:
                    queries = info.get("queries", {})
                    stages = info.get("stages", {})
                    filters_applied = info.get("filters", {})
                    dense_stage = stages.get("dense", {})
                    sparse_stage = stages.get("sparse", {})
                    exact_stage = stages.get("exact", {})
                    fusion_stage = stages.get("fusion", {})
                    rerank_stage = stages.get("rerank", {})
                    final_stage = stages.get("final", {})
                    post_list = stages.get("post_processors") or info.get("post_processors") or []
                    parts = [
                        "<div class='search-debug'>",
                        "<div class='debug-header' onclick='this.nextElementSibling.classList.toggle(\"debug-collapsed\")'>🔍 Retrieval Debug (click to toggle)</div>",
                        "<div class='debug-content'>",
                        "<div class='debug-section'>",
                        "<div class='debug-section-title'>Queries</div>",
                        f"<div class='debug-item'>Raw: <strong>{info.get('query', '')}</strong></div>",
                        f"<div class='debug-item'>Dense (semantic): <strong>{(queries.get('dense') or {}).get('query', info.get('dense_query') or info.get('query', ''))}</strong> • Filters: {(queries.get('dense') or {}).get('filters', filters_applied)}</div>",
                        f"<div class='debug-item'>Sparse (BM25): <strong>{(queries.get('sparse') or {}).get('query', info.get('sparse_query') or info.get('query', ''))}</strong> • Filters: {(queries.get('sparse') or {}).get('filters', filters_applied)}</div>",
                        f"<div class='debug-item'>Exact match: <strong>{(queries.get('exact') or {}).get('query', info.get('exact_query', info.get('query', '')))}</strong> • Filters: {(queries.get('exact') or {}).get('filters', filters_applied)}</div>",
                        "</div>",
                        "<div class='debug-section'>",
                        "<div class='debug-section-title'>Retrieval Pipeline</div>",
                        f"<div class='debug-item'>Dense (FAISS): {dense_stage.get('retrieved', info.get('dense_results_count', 0))} results → {dense_stage.get('after_filters', info.get('dense_filtered_count', 0))} after filters</div>",
                        f"<div class='debug-item'>Sparse (BM25): {sparse_stage.get('retrieved', info.get('sparse_results_count', 0))} results</div>",
                        f"<div class='debug-item'>Exact match: {exact_stage.get('retrieved', info.get('exact_results_count', 0))} results</div>",
                        f"<div class='debug-item'>RRF Fusion: {fusion_stage.get('unique', info.get('fused_count', 0))} unique candidates</div>",
                        f"<div class='debug-item'>Reranked: {'Yes' if rerank_stage.get('applied', info.get('reranked')) else 'No'} (top {rerank_stage.get('top_k', info.get('rerank_count', 0)) or 'N/A'})</div>",
                        f"<div class='debug-item'>Post-processors: {', '.join(post_list) if post_list else 'none'}</div>",
                        f"<div class='debug-item'>Final: {final_stage.get('returned', info.get('final_count', 0))} results</div>",
                        "</div>",
                    ]
                    candidates = info.get('candidates') or info.get('top_candidates') or []
                    if candidates:
                        parts.append("<div class='debug-section'><div class='debug-section-title'>Score drop-off (final scores, top 20)</div>")
                        parts.append(render_score_chart(candidates))
                        parts.append("</div>")
                    parts.append("</div></div>")
                    return "".join(parts)

                # Format results as HTML
                results_parts = [f"<div class='search-results'><div class='search-header'>Found {len(hits)} results for: <strong>{clean_query}</strong></div>"]
                
                for hit in hits:
                    # Format timestamp
                    timestamp = hit.event_at or hit.created_at
                    timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M') if timestamp else 'unknown'
                    
                    # Use chunk_window for preview if available (includes neighbor context)
                    # otherwise fall back to truncated text
                    preview_text = hit.chunk_window if hit.chunk_window else hit.text
                    if len(preview_text) > 300:
                        preview_text = preview_text[:300] + "..."
                    
                    # Format score
                    score = hit.scores.get('final', 0)
                    
                    # Build location info from chunk metadata
                    location_parts = []
                    if hit.heading_path:
                        location_parts.append(f"<span class='hit-location-item' title='Section'>{hit.heading_path}</span>")
                    if hit.page_number:
                        location_parts.append(f"<span class='hit-location-item' title='Page'>p.{hit.page_number}</span>")
                    if hit.seq is not None:
                        location_parts.append(f"<span class='hit-location-item' title='Chunk sequence'>chunk #{hit.seq}</span>")
                    location_html = f"<div class='hit-location'>{' • '.join(location_parts)}</div>" if location_parts else ""
                    
                    hit_parts = [
                        f"<div class='search-hit' data-chunk-id='{hit.chunk_id}' data-doc-id='{hit.doc_id}'>",
                        "<div class='hit-header'>",
                        f"<span class='hit-type'>{hit.doc_type}</span>",
                        f"<span class='hit-score'>Score: {score:.3f}</span>",
                        f"<span class='hit-date'>{timestamp_str}</span>",
                        "</div>",
                        f"<div class='hit-title'>{hit.doc_title or 'Untitled'}</div>",
                        location_html,  # Add location info
                        f"<div class='hit-text'>{preview_text}</div>",
                    ]
                    
                    if hit.doc_source:
                        hit_parts.append(f"<div class='hit-source'><a href='{hit.doc_source}' target='_blank'>{hit.doc_source}</a></div>")
                    
                    if debug:
                        hit_parts.append(render_score_chips(getattr(hit, "scores", {}) or {}))
                    
                    hit_parts.append("</div>")
                    results_parts.append("".join(hit_parts))
                
                results_parts.append("</div>")
                
                # Add debug information if debug mode is enabled
                if debug and debug_info:
                    results_parts.append(render_debug_panel(debug_info))
                
                results_html = "".join(results_parts)
                
                return {"messages": [make_system_msg(results_html)]}
            except Exception as e:
                import traceback
                traceback.print_exc()
                return {"messages": [make_system_msg(f"[find] Search failed: {str(e)}")]}
        
        reply = orchestrator.timelinereply(text)
        if isinstance(reply, list):
            messages = [msg_to_dict(m) for m in reply]
        else:
            messages = [{
                "content": str(reply),
                "role": "assistant",
                "created_at": datetime.now(UTC).isoformat(),
                "reference_time": None,
                "metadata": {},
                "chunk_id": None,
            }]
        if debug:
            for m in messages:
                m["debug"] = orchestrator.last_llm_trace
        return {"messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/add")
def add(payload=Body(...)) -> dict:
    text = (payload or {}).get("text", "")
    debug = bool((payload or {}).get("debug"))
    if not text:
        return {"logs": ["[add] missing text"]}
    try:
        logs = handle_add(text, workspace, orchestrator, debug=debug)
        return {"logs": [log["text"] for log in logs]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/rss")
async def run_rss(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = None
    debug = bool((payload or {}).get("debug"))
    orchestrator.last_llm_trace = None
    feeds = rss.load_feed_urls()
    if not feeds:
        return {"messages": [make_system_msg("[rss] no feeds configured (rss_feeds.txt)")] }
    try:
        # Get previously processed item hashes to exclude them
        exclude_hashes = workspace.store.get_rss_item_hashes() if workspace.store else set()
        items = rss.fetch_all(feeds, per_feed_limit=3, total_limit=20, exclude_hashes=exclude_hashes)
    except Exception as e:
        return {"messages": [make_system_msg(f"[rss] fetch failed: {e}")] }
    if not items:
        return {"messages": [make_system_msg("[rss] no new items found.")]}
    # Store the new items as processed
    if workspace.store:
        try:
            workspace.store.store_rss_items(items)
        except Exception as e:
            print(f"[warn] Failed to store RSS items: {e}")
    keywords = ["AI infra", "LLM", "AI coding", "Agent", "Agentic AI", "machine learning", "attention", "memory"]
    summary = orchestrator.summarize_rss(items, keywords=keywords)
    messages = [msg_to_dict(m) for m in summary]
    if debug and orchestrator.last_llm_trace:
        for m in messages:
            m["debug"] = orchestrator.last_llm_trace
    return {"messages": messages}


@app.get("/api/llm_trace")
def llm_trace() -> dict:
    return {"trace": orchestrator.last_llm_trace}
