from __future__ import annotations

from typing import List, Optional


LIFELONG_SUMMARY_PROMPT_VERSION = "v0.9"


def lifelong_summary_instruction(*, facet: Optional[str], is_delta: bool) -> str:
    """Return the prompt for lifelong summarization."""
    base = (
        "Summarize recent activity into facets (profile, topics, timeline). "
        "Return JSON only (payload_json) with keys: facets.profile (list), "
        "facets.topics (list of {name, summary}), facets.timeline (list of {date, event}), "
        "plus any notes needed. Use Chinese by default and keep items concise."
    )
    if is_delta:
        base = (
            "Summarize only deltas/new changes since previous_summary. "
            + base
        )
    else:
        base = (
            "Produce a full snapshot when previous_summary is empty. "
            + base
        )
    if facet == "profile":
        base += " Focus on user interests/objectives; ignore topic details."
    elif facet == "timeline":
        base += " Focus on chronological changes derived from profile/topics."
    elif facet == "topics":
        base += " Focus on topic-level updates and emerging themes."
    return base


def rss_summary_instruction(
    *,
    keywords: Optional[list[str]] = None,
    interests_summary: Optional[str] = None,
) -> str:
    """Return the prompt for RSS summarization."""
    keyword_text = ", ".join(keywords or [])
    prompt = (
        "Create a concise daily brief of the following RSS items. "
        "Highlight what might interest the user. Include inline references with the title and URL. "
        f"Keywords to bias toward: {keyword_text}."
    )
    if interests_summary:
        prompt += f" User interests: {interests_summary}."
    return prompt


def note_summary_instruction() -> str:
    """Return the prompt for summarizing a note document."""
    return (
        "Summarize the note into concise bullet points, preserving key facts, decisions, and dates. "
        "If the note contains a timeline, keep it chronological."
    )


def topic_summary_instruction() -> str:
    """Return the prompt for generating a topic summary (Phase 3)."""
    return (
        "Summarize the content into a brief topic overview. "
        "Return JSON with key 'summary' containing a 1-2 sentence description "
        "of what this topic is about. Use Chinese by default and keep it concise."
    )


def topic_assignment_instruction(*, candidates: List[str]) -> str:
    """Return the prompt for topic assignment decision (Phase 3).
    
    Args:
        candidates: List of candidate topic names to consider
    """
    candidates_text = ", ".join(f"'{c}'" for c in candidates) if candidates else "none"
    return (
        f"Given the content, decide whether to:\n"
        f"1. REUSE an existing topic from candidates: {candidates_text}\n"
        f"2. CREATE a new topic (if content is truly novel)\n"
        f"3. MERGE topics (if content bridges multiple topics)\n\n"
        "Return JSON with keys: decision ('reuse'/'create'/'merge'), "
        "topic_name (selected or new name), rationale (brief explanation), "
        "merge_into (target topic name, only if decision is 'merge')."
    )
