"""
Shiye Usage Examples

This file demonstrates common usage patterns for Shiye.
These examples can be run interactively or as a reference.
"""

# Example 1: Setting up the workspace programmatically
# =====================================================

from workspace import MemoryWorkspace
from datatypes import Message, Role
from datetime import datetime, UTC

# Initialize workspace with default storage
workspace = MemoryWorkspace()

# Add a simple message
msg = Message(
    content="Remember to follow up on the research paper about transformers",
    role=Role.USER,
    created_at=datetime.now(UTC)
)
workspace.add(msg)

# List recent messages
recent = workspace.list_recent(n=10)
print(f"Found {len(recent)} recent messages")


# Example 2: Adding notes programmatically
# =========================================

# Save a note
note = workspace.save_note(
    content="# Meeting Notes\n\nDiscussed the new architecture for the project.",
    title="Project Meeting"
)
print(f"Saved note with ID: {note['id']}")

# List all notes
notes = workspace.list_notes(limit=10)
print(f"Found {len(notes)} notes")


# Example 3: URL fetching and storage
# ====================================

from fetcher import extract_urls, fetch_url_content
from handlers import handle_add
from orchestrator import Orchestrator

# Extract URLs from text
text = "Check out https://arxiv.org/abs/2103.03206 for the paper"
urls = extract_urls(text)
print(f"Found URLs: {urls}")

# Fetch URL content
if urls:
    title, content, method = fetch_url_content(urls[0])
    print(f"Fetched: {title} using {method}")
    
    # Store with document metadata
    doc_msg = Message(
        content=content or "",
        role=Role.SYSTEM,
        metadata={"url": urls[0], "title": title, "source": "url_fetch"}
    )
    workspace.add_with_document(
        [doc_msg],
        document_meta={
            "doc_type": "web_page",
            "title": title,
            "source": "url",
            "uri": urls[0]
        }
    )


# Example 4: RSS feed aggregation
# ================================

from rss import load_feed_urls, fetch_all, format_digest

# Load configured feeds
feeds = load_feed_urls()
print(f"Loaded {len(feeds)} RSS feeds")

# Fetch latest items
items = fetch_all(feeds, per_feed_limit=3, total_limit=10)
print(f"Fetched {len(items)} items")

# Format as digest
digest = format_digest(items, keywords=["AI", "LLM", "Machine Learning"])
print(digest)


# Example 5: Working with the orchestrator
# =========================================

orch = Orchestrator(workspace)

# Generate a reply with context
user_messages = [
    Message(content="What are the latest AI research trends?", role=Role.USER)
]

# Get context from workspace
context_messages = workspace.context_block(n=5)

try:
    # Generate response (requires DS_API_KEY)
    replies = orch.reply(
        question=user_messages,
        context=context_messages,
        instruction="Provide a helpful response based on the context."
    )
    for reply in replies:
        print(f"{reply.role.value}: {reply.content}")
except Exception as e:
    print(f"LLM not configured: {e}")


# Example 6: Searching stored content
# ====================================

# Recall by keyword
result = workspace.recall("transformer")
if result:
    print(f"Found: {result.content[:100]}...")
else:
    print("No matching content found")


# Example 7: Managing notes
# ==========================

# Create a note with images
note_with_image = workspace.save_note(
    content="""# Research Summary

![diagram](assets/img/diagram.png)

This diagram shows the architecture.
""",
    title="Research Summary"
)

# Update an existing note
updated_note = workspace.save_note(
    content="# Updated Research Summary\n\nRevised content here.",
    title="Research Summary Updated",
    note_id=note_with_image['id']
)

# Get a specific note
retrieved = workspace.get_note(note_with_image['id'])
print(f"Note: {retrieved['title']}")


# Example 8: Working with timestamps
# ===================================

from datatypes import ensure_utc

# Create message with explicit timestamp
msg_with_time = Message(
    content="Meeting at 3pm tomorrow",
    role=Role.USER,
    created_at=datetime.now(UTC),
    reference_time=datetime(2024, 1, 15, 15, 0, 0, tzinfo=UTC)
)
workspace.add(msg_with_time)


# Example 9: Deleting content
# ============================

# Delete a chunk by ID
chunk_id = 123  # Example chunk ID
deleted = workspace.delete_chunk(chunk_id)
print(f"Deletion successful: {deleted}")


# Example 10: Clearing workspace (in-memory fallback)
# ====================================================

# Note: This clears the workspace but doesn't delete persisted data
# workspace.clear()
# print("Workspace cleared")


print("\n" + "="*60)
print("All examples completed!")
print("="*60)
print("\nFor web UI usage, start the server with:")
print("  python main.py")
print("\nThen visit http://localhost:8000")
print("\nAvailable commands in the UI:")
print("  /note     - Open note editor")
print("  /add      - Add content or fetch URLs")
print("  /rss      - Generate RSS digest")
print("  /summarize - Summarize conversation")
print("  /clear    - Clear chat display")
