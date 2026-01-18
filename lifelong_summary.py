from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, Optional, TypedDict, TypeAlias, Literal
import json

SUMMARY_DOC_TYPE = "lifelong_summary"
SUMMARY_SCHEMA_VERSION = "v0.9"
DEFAULT_LANGUAGE = "zh"
DEFAULT_SUMMARY_SOURCE = "system"

ProfileSummaryItem: TypeAlias = str


class TopicSummaryItem(TypedDict):
    name: str
    summary: str


class TimelineSummaryItem(TypedDict):
    date: str
    event: str
    sources: list[str]
    confidence: Literal["high", "medium", "low"]


class LifelongSummaryFacets(TypedDict):
    profile: list[ProfileSummaryItem]
    topics: list[TopicSummaryItem]
    timeline: list[TimelineSummaryItem]


@dataclass
class LifelongSummary:
    """Representation of a lifelong summary document.

    Stores a JSON payload for tool usage and a Markdown body for user-facing views.
    
    The (facet, key) pair uniquely identifies a summary within a time scope:
    - facet: The top-level category ("profile", "topics", "timeline")
    - key: Sub-identifier within the facet (e.g., "interests", "AI", "profile:interests")
    
    Examples:
    - (profile, interests): User interests summary
    - (profile, objectives): User objectives summary
    - (topics, AI): Summary for AI topic
    - (timeline, profile:interests): Timeline tracking profile.interests changes
    """

    payload: Dict[str, Any]
    markdown: str
    summary_date: datetime = field(default_factory=lambda: datetime.now(UTC))
    title: Optional[str] = None
    summary_source: str = DEFAULT_SUMMARY_SOURCE
    facet: Optional[str] = None
    key: Optional[str] = None  # Sub-identifier within facet (renamed from 'topic')
    uri: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None

    def normalized_payload(self) -> Dict[str, Any]:
        payload = dict(self.payload or {})
        payload.setdefault("schema_version", SUMMARY_SCHEMA_VERSION)
        payload.setdefault("language", DEFAULT_LANGUAGE)
        payload.setdefault("summary_date", self.summary_date.date().isoformat())
        if self.facet:
            payload.setdefault("facet", self.facet)
        if self.key:
            payload.setdefault("key", self.key)
        payload.setdefault("facets", {"profile": [], "topics": [], "timeline": []})
        payload.setdefault("references", [])
        return payload

    def render_document(self) -> str:
        payload = self.normalized_payload()
        json_block = json.dumps(payload, ensure_ascii=False, indent=2)
        markdown = (self.markdown or "").strip()
        header = self.title or _build_default_title(payload.get("summary_date"), self.facet, self.key)
        parts = [f"# {header}", "", "```json", json_block, "```", ""]
        if markdown:
            parts.append(markdown)
        return "\n".join(parts).strip() + "\n"

    def document_meta(self) -> Dict[str, Any]:
        now = self.summary_date
        tags = dict(self.tags or {})
        if self.facet:
            tags.setdefault("facet", self.facet)
        if self.key:
            tags.setdefault("key", self.key)
        tags.setdefault("summary_source", self.summary_source)
        return {
            "doc_type": SUMMARY_DOC_TYPE,
            "title": self.title or _build_default_title(now.date().isoformat(), self.facet, self.key),
            "source": self.summary_source,
            "uri": self.uri,
            "tags": tags or None,
            "created_at": now,
            "event_at": now,
        }


def build_lifelong_summary(
    payload: Dict[str, Any],
    markdown: str,
    summary_date: Optional[datetime] = None,
    title: Optional[str] = None,
    summary_source: str = DEFAULT_SUMMARY_SOURCE,
    facet: Optional[str] = None,
    key: Optional[str] = None,
    uri: Optional[str] = None,
    tags: Optional[Dict[str, Any]] = None,
) -> LifelongSummary:
    return LifelongSummary(
        payload=payload,
        markdown=markdown,
        summary_date=summary_date or datetime.now(UTC),
        title=title,
        summary_source=summary_source,
        facet=facet,
        key=key,
        uri=uri,
        tags=tags,
    )


def ensure_reference_ids(reference_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Helper to shape reference payloads using chunk IDs."""
    references = []
    if reference_ids:
        for ref in reference_ids:
            if ref:
                references.append({"chunk_id": str(ref)})
    return {"references": references}


def merge_references(
    base: Optional[Iterable[Dict[str, Any]]],
    extra: Optional[Iterable[Dict[str, Any]]],
) -> list[Dict[str, Any]]:
    """Merge reference payloads, de-duplicating by content."""
    merged: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for collection in (base or [], extra or []):
        for ref in collection:
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            merged.append(ref)
            seen.add(key)
    return merged


def render_markdown_from_payload(payload: Dict[str, Any]) -> str:
    """Render user-facing markdown from a summary payload."""
    facets = payload.get("facets") or {}
    lines = []
    profile = facets.get("profile") or []
    topics = facets.get("topics") or []
    timeline = facets.get("timeline") or []

    if profile:
        lines.append("## Profile")
        lines.extend(_render_list(profile))
        lines.append("")

    if topics:
        lines.append("## Topics")
        lines.extend(_render_topics(topics))
        lines.append("")

    if timeline:
        lines.append("## Timeline")
        lines.extend(_render_timeline(timeline))
        lines.append("")

    return "\n".join(lines).strip()


def _render_list(items: Iterable[Any]) -> list[str]:
    rendered = []
    for item in items:
        if isinstance(item, str):
            rendered.append(f"- {item}")
        elif isinstance(item, dict):
            label = item.get("text") or item.get("summary") or json.dumps(item, ensure_ascii=False)
            rendered.append(f"- {label}")
        else:
            rendered.append(f"- {str(item)}")
    return rendered


def _render_topics(items: Iterable[Any]) -> list[str]:
    rendered = []
    for item in items:
        if isinstance(item, dict):
            title = item.get("name") or item.get("topic") or "Topic"
            summary = item.get("summary") or item.get("text")
            rendered.append(f"- **{title}**")
            if summary:
                rendered.append(f"  - {summary}")
        else:
            rendered.append(f"- {str(item)}")
    return rendered


def _render_timeline(items: Iterable[Any]) -> list[str]:
    rendered = []
    for item in items:
        if isinstance(item, dict):
            ts = item.get("date") or item.get("time")
            event = item.get("event") or item.get("text") or json.dumps(item, ensure_ascii=False)
            if ts:
                rendered.append(f"- {ts}: {event}")
            else:
                rendered.append(f"- {event}")
        else:
            rendered.append(f"- {str(item)}")
    return rendered


def _build_default_title(
    summary_date: Optional[str],
    facet: Optional[str],
    key: Optional[str],
) -> str:
    """Build a default title from facet and key.
    
    Examples:
    - facet=profile, key=interests → "profile · interests (2024-01-01)"
    - facet=topics, key=AI → "topics · AI (2024-01-01)"
    - facet=timeline, key=profile:interests → "timeline · profile:interests (2024-01-01)"
    """
    date_label = summary_date or datetime.now(UTC).date().isoformat()
    if facet and key:
        return f"{facet} · {key} ({date_label})"
    if facet:
        return f"{facet} Summary ({date_label})"
    if key:
        return f"{key} Summary ({date_label})"
    return f"Summary ({date_label})"
