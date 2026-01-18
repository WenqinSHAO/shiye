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


def topic_change_instruction(
    *,
    candidates: List[str],
    candidates_with_scores: str = "",
) -> str:
    """Return the unified prompt for topic change operations (Phase 3).
    
    This is the main prompt for all topic operations: create, reuse, merge, split.
    
    Args:
        candidates: List of candidate topic names
        candidates_with_scores: Formatted string with similarity scores
    """
    candidates_text = ", ".join(f"'{c}'" for c in candidates) if candidates else "none"
    
    return (
        "Analyze the content and decide the appropriate topic operation.\n\n"
        f"**Candidate topics**: {candidates_text}\n"
        f"{candidates_with_scores}\n\n"
        "**Possible operations**:\n"
        "1. **REUSE**: Content fits an existing topic well\n"
        "2. **CREATE**: Content represents a genuinely new topic\n"
        "3. **MERGE**: Content bridges multiple topics; merge them into one\n"
        "4. **SPLIT**: Content represents a distinct subtopic that should be separated\n"
        "5. **RENAME**: Content redefines an existing topic; suggest a new name\n\n"
        "**Decision criteria**:\n"
        "- REUSE when similarity ≥ 0.6 with a single topic\n"
        "- CREATE when similarity < 0.3 with all topics\n"
        "- MERGE when content relates to 2+ topics with similar scores (suggests consolidation)\n"
        "- SPLIT when content is related but distinctly different (subtopic emergence)\n"
        "- RENAME when content suggests the topic's focus has evolved\n\n"
        "Return JSON with:\n"
        "- decision: 'reuse' | 'create' | 'merge' | 'split' | 'rename'\n"
        "- topic_name: primary topic name (target for reuse/merge, new name for create/split)\n"
        "- rationale: brief explanation (Chinese preferred)\n"
        "- merge_from: source topic name (only if merge)\n"
        "- split_into: new topic name (only if split)\n"
        "- rename_from: old topic name (only if rename)"
    )
