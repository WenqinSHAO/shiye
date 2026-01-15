from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable, List, Optional


@dataclass
class SummaryRequest:
    facet: str
    topic: Optional[str]
    is_delta: bool
    batch_label: str
    since: Optional[datetime]


class SummaryPlanner:
    """Plan bootstrap and delta summarization batches."""

    def __init__(self, *, batch_days: int = 30) -> None:
        self.batch_days = batch_days

    def plan_bootstrap(self, facets: Iterable[str], *, since: datetime) -> List[SummaryRequest]:
        now = datetime.now(UTC)
        requests: List[SummaryRequest] = []
        for facet in facets:
            batch_start = since
            while batch_start < now:
                label = f"{facet}:{batch_start.date().isoformat()}"
                requests.append(
                    SummaryRequest(
                        facet=facet,
                        topic=None,
                        is_delta=False,
                        batch_label=label,
                        since=batch_start,
                    )
                )
                batch_start = batch_start.replace(tzinfo=UTC) + timedelta(days=self.batch_days)
        return requests

    def plan_delta(self, *, facet: str, topic: Optional[str], since: Optional[datetime]) -> List[SummaryRequest]:
        label = f"{facet}:{topic or 'global'}:{(since.date().isoformat() if since else 'all')}"
        return [
            SummaryRequest(
                facet=facet,
                topic=topic,
                is_delta=bool(since),
                batch_label=label,
                since=since,
            )
        ]
