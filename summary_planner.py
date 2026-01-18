from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable, List, Optional, Tuple


@dataclass
class SummaryRequest:
    facet: str
    topic: Optional[str]
    is_delta: bool
    batch_label: str
    since: Optional[datetime]


class SummaryPlanner:
    """Plan bootstrap and delta summarization batches.
    
    Batches are organized to maximize LLM API KV cache hits by grouping
    all facets for the same time window together. This ensures the document
    content prefix is reused across facet-specific LLM calls.
    """

    def __init__(self, *, batch_days: int = 30) -> None:
        self.batch_days = batch_days

    def plan_bootstrap(self, facets: Iterable[str], *, since: datetime) -> List[SummaryRequest]:
        """Plan bootstrap requests grouped by time window for KV cache optimization.
        
        Returns requests ordered by (time_window, facet) so that all facets for
        the same time window are processed together, maximizing prefix reuse.
        """
        now = datetime.now(UTC)
        facet_list = list(facets)
        
        # Collect all time windows first
        time_windows: List[Tuple[datetime, datetime]] = []
        batch_start = since
        while batch_start < now:
            batch_end = batch_start + timedelta(days=self.batch_days)
            time_windows.append((batch_start, batch_end))
            batch_start = batch_end
        
        # Generate requests: iterate by time window first, then by facet
        # This groups all facets for the same window together for KV cache hits
        requests: List[SummaryRequest] = []
        for window_start, window_end in time_windows:
            for facet in facet_list:
                label = f"{facet}:{window_start.date().isoformat()}"
                requests.append(
                    SummaryRequest(
                        facet=facet,
                        topic=None,
                        is_delta=False,
                        batch_label=label,
                        since=window_start,
                    )
                )
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
