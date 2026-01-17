from __future__ import annotations

from typing import Optional


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
