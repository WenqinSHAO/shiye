# --- LLM Orchestrator --------------------------------
import os
import json
from typing import Optional, List, Union
import textwrap
from datetime import UTC, datetime, timedelta
from workspace import MemoryWorkspace
from datatypes import Message, Role, ensure_utc
from lifelong_summary import ensure_reference_ids, merge_references, render_markdown_from_payload
from prompts import LIFELONG_SUMMARY_PROMPT_VERSION, lifelong_summary_instruction, rss_summary_instruction
from summary_planner import SummaryPlanner
from config import SHIYE_SUMMARY_CADENCE_DAYS, SHIYE_SUMMARY_MAX_MESSAGES
import dspy

llm_base = "https://api.deepseek.com"
llm_key = os.getenv("DS_API_KEY")

if llm_key:
    try:
        # Configure DSPy with Deepseek LLM
        dspy.configure(lm=dspy.LM("deepseek/deepseek-chat", api_key=llm_key, base_url=llm_base))
    except Exception:
        # fail explicitly here so we can catch later
        # and degrade gracefully
        raise   

class TimeChunker(dspy.Signature):  
    """Chunk text into smaller pieces based on time references.
    text: str
    -> chunks: List[str]
    """
    role_hint: str = dspy.InputField(default="user")
    text: str = dspy.InputField()
    chunks: List[Message] = dspy.OutputField(
        desc="list of text chunks split by time references, following role hint"
        )
            
            
class Reply(dspy.Signature):
    """Generate an assistant reply.
    instruction: str
    context: str
    -> response: List[Message]
    """
    instruction: str = dspy.InputField()
    question: List[Message] = dspy.InputField(
        desc="user question possibly with time references")
    context:List[Message] = dspy.InputField(
        desc="context block with chat history to keep continuity")
    response: List[Message] = dspy.OutputField(
        desc="list of messages in the reply split by time references if needed")


class LifelongSummarySignature(dspy.Signature):
    """Summarize recent activity into a lifelong summary JSON payload."""
    instruction: str = dspy.InputField()
    recent_messages: str = dspy.InputField(desc="Recent messages to summarize")
    previous_summary: str = dspy.InputField(desc="Previous summary text or empty")
    payload_json: str = dspy.OutputField(desc="JSON payload with facets/topics/references")


class Orchestrator:
    """Orchestrates replies and summaries using DSPy if configured, else falls back to local methods."""
      
    def __init__(self, workspace: MemoryWorkspace):
        self.workspace = workspace
        self.dspy_predictor = dspy.Predict(Reply) if llm_key else None
        self.dspy_chunker = dspy.Predict(TimeChunker) if llm_key else None
        self.dspy_summarizer = dspy.Predict(LifelongSummarySignature) if llm_key else None
        self.last_llm_trace: Optional[dict] = None
        self.last_search_context = None  # Cache last search context for reuse
        self.last_search_time = None  # Track when we last searched
        self.bootstrap_doc_types = [
            "chat",
            "note",
            "rss_daily_summary",
            "web_page",
            "paper",
        ]


    def should_search(self, user_text: List[Message], history_count: int = 0) -> str:
        """Determine whether to search, reuse last context, or skip search.
        
        Args:
            user_text: User messages to analyze
            history_count: Number of messages in recent history
            
        Returns:
            'search_now': Perform new search
            'reuse_last': Reuse cached search context
            'skip': Skip search, use context_block
        """
        # Extract query text
        query_parts = [m.content for m in user_text if m.content]
        if not query_parts:
            return 'skip'
        
        query = " ".join(query_parts).strip()
        
        # Skip search for very short inputs (likely chit-chat or continuation)
        # Use 5 chars to allow concise queries like "k8s?" or "TLS?"
        if len(query) < 5:
            return 'skip'
        
        # Check for continuation signals ("continue", "go on", "more", etc.)
        continuation_signals = ['continue', 'go on', 'more', 'keep going', 'tell me more', 'and then']
        query_lower = query.lower()
        if any(signal in query_lower for signal in continuation_signals):
            # Reuse last context if available and recent
            if self.last_search_context and self.last_search_time:
                elapsed = (datetime.now(UTC) - self.last_search_time).total_seconds()
                if elapsed < 300:  # Within 5 minutes
                    return 'reuse_last'
        
        # Check for cooldown (don't search on rapid successive turns)
        if self.last_search_time:
            elapsed = (datetime.now(UTC) - self.last_search_time).total_seconds()
            if elapsed < 10:  # Less than 10 seconds since last search
                return 'skip'
        
        # Check query intent - look for lookup/question keywords with word boundaries
        import re
        lookup_keywords = ['what', 'when', 'where', 'who', 'why', 'how', 'find', 'search', 
                          'tell me', 'show me', 'explain', 'describe', 'list']
        # Use word boundary checks to avoid false positives like "whatever" matching "what"
        has_lookup_intent = any(re.search(r'\b' + re.escape(keyword) + r'\b', query_lower) 
                               for keyword in lookup_keywords)
        
        # Search if it looks like a lookup query
        if has_lookup_intent:
            return 'search_now'
        
        # Default: skip search for chit-chat
        return 'skip'


    def timechunker(self, text: str, role_hint: str = "user") -> List[Message]:
        if self.dspy_chunker:
            try:
                out = self.dspy_chunker(text=text, role_hint=role_hint)
                now = ensure_utc(datetime.now(UTC))
                for m in out.chunks:
                    m.created_at = now
                    self.workspace.add(m)
                return out.chunks
            except Exception as e:
                print(f"TimeChunker error: {e}")
                msg = Message(content=f"TimeChunker error: {e}", role=Role.SYSTEM)
                self.workspace.add(msg)
                return [msg]
        msg = Message(content=text, role=Role.USER)
        self.workspace.add(msg)
        return [msg]
    

    def basereply(self, instruction: str, user_text: List[Message]) -> List[Message]:
        if self.dspy_predictor:
            try:
                # Determine search policy
                search_policy = self.should_search(user_text)
                
                ctx = []
                context_method = "context_block"
                estimated_tokens = None
                
                if search_policy == 'search_now':
                    # Use search-based context assembly
                    from retrieval import SearchRequest, ContextPacker
                    
                    # Extract query from user messages
                    query_parts = [m.content for m in user_text if m.content]
                    query = " ".join(query_parts)
                    
                    # Search for relevant context (limiting to avoid token overflow)
                    request = SearchRequest(query=query, top_k=10, enable_rerank=True)
                    hits = self.workspace.search(request)
                    
                    if hits:
                        # Pack hits into token-limited context using ContextPacker
                        packer = ContextPacker(max_tokens=6000)  # Leave room for instruction + question
                        context_bundle = packer.pack(hits, query)
                        
                        # Convert packed context back to Message format for DSPy
                        for item in context_bundle.get('context_items', []):
                            # Include citation info in metadata with safe key access
                            ctx.append(Message(
                                content=item.get('text', ''),
                                role=Role.SYSTEM,
                                metadata={
                                    'citation_id': item.get('citation_id'),
                                    'doc_type': item.get('doc_type'),
                                    'source': item.get('source'),
                                    'relevance_score': item.get('relevance_score', 0.0)
                                }
                            ))
                        
                        # Cache context for potential reuse
                        self.last_search_context = ctx
                        self.last_search_time = datetime.now(UTC)
                        context_method = "search+packer"
                        estimated_tokens = context_bundle.get('estimated_tokens', 0)
                    else:
                        # Clear stale cache when search returns no results
                        self.last_search_context = None
                        self.last_search_time = None
                
                elif search_policy == 'reuse_last' and self.last_search_context:
                    # Reuse cached search context
                    ctx = self.last_search_context
                    context_method = "reused_search"
                
                # Fallback to context_block if no search context available
                if not ctx:
                    ctx = self.workspace.context_block(n=200)
                    context_method = "context_block"
                
                self.last_llm_trace = {
                    "type": "reply",
                    "ts": datetime.now(UTC).isoformat(),
                    "instruction": instruction,
                    "question": [m.to_dict() for m in user_text],
                    "context": [m.to_dict() for m in ctx],
                    "context_method": context_method,
                    "search_policy": search_policy,
                    "context_items": len(ctx),
                    "estimated_tokens": estimated_tokens,
                }
                out = self.dspy_predictor(
                    instruction= instruction or "You are a concise, independent-minded assistant.",
                    question=user_text,
                    context=ctx)
                now = ensure_utc(datetime.now(UTC))
                for m in out.response:
                    m.created_at = now
                    self.workspace.add(m)
                return out.response
            except Exception as e:
                return self._fallback_reply(user_text=user_text, note=f"(DSPy error: {e})")
        return self._fallback_reply(user_text=user_text, note="(no DSPy configured)")

    def summarize_lifelong(
        self,
        *,
        manual: bool = False,
        facet: Optional[str] = None,
        key: Optional[str] = None,
    ) -> Message:
        """Summarize recent activity into a lifelong summary.
        
        Args:
            manual: Whether this is a manual trigger (bypasses cadence check)
            facet: Top-level category ("profile", "topics", "timeline")
            key: Sub-identifier within facet (e.g., "interests", "AI")
        """
        # For profile and timeline facets, key is not used in bootstrap (whole-facet summary)
        if manual and not facet and not key:
            latest_summary = self.workspace.get_latest_lifelong_summary()
            if not latest_summary:
                bootstrap_since = self._get_bootstrap_start_date()
                if bootstrap_since:
                    return self.bootstrap_lifelong(since=bootstrap_since)
        latest = self.workspace.get_latest_lifelong_summary(facet=facet, key=key)
        latest_dt = None
        if latest:
            try:
                latest_dt = ensure_utc(datetime.fromisoformat(latest.get("event_at") or latest.get("created_at")))
            except Exception:
                latest_dt = None

        if latest_dt and not manual:
            age_days = (datetime.now(UTC) - latest_dt).days
            if age_days < SHIYE_SUMMARY_CADENCE_DAYS:
                return Message(
                    content=f"[summary] Last summary is {age_days}d old; next cadence not reached.",
                    role=Role.SYSTEM,
                )

        since_dt = latest_dt or datetime.fromtimestamp(0, tz=UTC)
        recent_messages = self.workspace.list_messages_since(since_dt, limit=SHIYE_SUMMARY_MAX_MESSAGES)
        if not recent_messages:
            return Message(
                content="[summary] No new material since the last summary.",
                role=Role.SYSTEM,
            )

        references = [m.metadata.get("chunk_id") for m in recent_messages if m.metadata.get("chunk_id")]
        payload = {
            "facet": facet,
            "key": key,
            "trigger": "manual" if manual else "scheduled",
            "prompt_version": LIFELONG_SUMMARY_PROMPT_VERSION,
            "facets": {"profile": [], "topics": [], "timeline": []},
            **ensure_reference_ids(references),
        }

        recent_text = "\n".join(m.to_text() for m in recent_messages if m.content)
        previous_summary_text = ""
        if latest and latest.get("id"):
            try:
                doc = self.workspace.get_document(latest["id"])
                previous_summary_text = doc.get("content") or ""
            except Exception:
                previous_summary_text = ""

        payload_delta = {}
        if self.dspy_summarizer:
            try:
                instruction = lifelong_summary_instruction(
                    facet=facet,
                    is_delta=bool(previous_summary_text),
                )
                out = self.dspy_summarizer(
                    instruction=instruction,
                    recent_messages=recent_text,
                    previous_summary=previous_summary_text,
                )
                try:
                    payload_delta = json.loads(out.payload_json or "{}")
                except Exception:
                    payload_delta = {}
            except Exception as e:
                payload_delta = {"notes": f"LLM summary failed: {e}"}

        if payload_delta:
            payload = {**payload, **payload_delta}
            payload["facet"] = facet
            payload["key"] = key
            payload["trigger"] = "manual" if manual else "scheduled"
            payload["prompt_version"] = LIFELONG_SUMMARY_PROMPT_VERSION
            payload["references"] = merge_references(
                payload.get("references"),
                payload_delta.get("references"),
            )
        if not payload.get("facets"):
            payload["facets"] = {"profile": [], "topics": [], "timeline": []}
        if not payload_delta:
            payload["facets"]["timeline"].append(
                {
                    "date": datetime.now(UTC).date().isoformat(),
                    "event": f"New messages: {len(recent_messages)}",
                }
            )

        markdown = render_markdown_from_payload(payload)
        if not markdown:
            markdown = f"- New messages: {len(recent_messages)}"

        result = self.workspace.save_lifelong_summary(
            payload=payload,
            markdown=markdown,
            summary_date=datetime.now(UTC),
            summary_source="system",
            facet=facet,
            key=key,
        )
        if result and result.get("document_id"):
            return Message(
                content=f"[summary] Saved summary document #{result['document_id']}.",
                role=Role.SYSTEM,
            )
        return Message(
            content="[summary] Generated summary (storage unavailable).",
            role=Role.SYSTEM,
        )

    def bootstrap_lifelong(
        self,
        *,
        since: datetime,
        batch_days: int = 30,
        facets: Optional[List[str]] = None,
    ) -> Message:
        planner = SummaryPlanner(batch_days=batch_days)
        requested_facets = facets or ["profile", "topics", "timeline"]
        requests = planner.plan_bootstrap(requested_facets, since=since)
        saved = 0
        skipped = 0
        batch_cache: dict[tuple[datetime, datetime], tuple[str, str, list[dict], int]] = {}

        for request in requests:
            batch_start = request.since or since
            batch_end = batch_start + timedelta(days=batch_days)
            cache_key = (batch_start, batch_end)
            if cache_key in batch_cache:
                prefix, recent_text, references, doc_count = batch_cache[cache_key]
            else:
                documents = self.workspace.list_documents(
                    doc_types=self.bootstrap_doc_types,
                    since=batch_start,
                    until=batch_end,
                    limit=1000,
                )
                recent_text, references = self._format_bootstrap_documents(documents)
                doc_count = len(documents)
                prefix = self._bootstrap_prefix(batch_start, batch_end, doc_count)
                batch_cache[cache_key] = (prefix, recent_text, references, doc_count)
            if not recent_text:
                skipped += 1
                continue

            payload = {
                "facet": request.facet,
                "key": request.key,
                "trigger": "bootstrap",
                "prompt_version": LIFELONG_SUMMARY_PROMPT_VERSION,
                "facets": {"profile": [], "topics": [], "timeline": []},
                "references": references,
                "bootstrap": {
                    "since": batch_start.date().isoformat(),
                    "until": batch_end.date().isoformat(),
                    "batch_label": request.batch_label,
                    "doc_count": doc_count,
                },
            }

            payload_delta = {}
            if self.dspy_summarizer:
                try:
                    # Document-first prompt structure for LLM API KV cache optimization:
                    # [document_content] + [facet_instruction]
                    # This allows the LLM provider to cache the document prefix across
                    # facet passes for the same time window.
                    instruction = lifelong_summary_instruction(
                        facet=request.facet,
                        is_delta=False,
                    )
                    # Build document-first prompt: documents as prefix, instruction as suffix
                    document_prefix = f"{prefix}\n\n{recent_text}".strip()
                    out = self.dspy_summarizer(
                        instruction=instruction,
                        recent_messages=document_prefix,
                        previous_summary="",
                    )
                    payload_delta = json.loads(out.payload_json or "{}")
                except Exception as e:
                    payload_delta = {"notes": f"LLM bootstrap summary failed: {e}"}

            if payload_delta:
                payload = {**payload, **payload_delta}
                payload["facet"] = request.facet
                payload["key"] = request.key
                payload["trigger"] = "bootstrap"
                payload["prompt_version"] = LIFELONG_SUMMARY_PROMPT_VERSION
                payload["references"] = merge_references(
                    payload.get("references"),
                    payload_delta.get("references"),
                )

            if not payload.get("facets"):
                payload["facets"] = {"profile": [], "topics": [], "timeline": []}
            if not payload_delta:
                payload["facets"]["timeline"].append(
                    {
                        "date": batch_start.date().isoformat(),
                        "event": f"Bootstrap batch documents: {doc_count}",
                    }
                )

            markdown = render_markdown_from_payload(payload)
            if not markdown:
                markdown = (
                    f"- Bootstrap batch {batch_start.date().isoformat()} to "
                    f"{batch_end.date().isoformat()} ({doc_count} docs)"
                )

            result = self.workspace.save_lifelong_summary(
                payload=payload,
                markdown=markdown,
                summary_date=datetime.now(UTC),
                summary_source="system",
                facet=request.facet,
                key=request.key,
                tags={
                    "facet": request.facet,
                    "key": request.key,
                    "bootstrap_batch": request.batch_label,
                    "bootstrap_since": batch_start.date().isoformat(),
                    "bootstrap_until": batch_end.date().isoformat(),
                },
            )
            if result and result.get("document_id"):
                saved += 1
            else:
                skipped += 1

        return Message(
            content=(
                "[bootstrap] Completed lifelong summary bootstrap. "
                f"Saved: {saved}, Skipped: {skipped}."
            ),
            role=Role.SYSTEM,
        )

    def _format_bootstrap_documents(self, documents: List[dict]) -> tuple[str, list[dict]]:
        blocks: list[str] = []
        references: list[dict] = []
        for doc in documents:
            doc_text = self._extract_document_text(doc)
            if not doc_text:
                continue
            doc_type = doc.get("doc_type") or "unknown"
            title = doc.get("title") or doc.get("uri") or f"Document {doc.get('id')}"
            header = f"[{doc_type}] {title}"
            blocks.append(f"{header}\n{doc_text}".strip())
            if doc.get("id") is not None:
                references.append({"document_id": doc["id"]})
        return "\n\n".join(blocks), references

    def _extract_document_text(self, doc: dict) -> str:
        raw = doc.get("raw_content")
        if raw is None:
            return ""
        doc_type = (doc.get("doc_type") or "").lower()
        if doc_type == "chat":
            try:
                data = json.loads(raw)
            except Exception:
                data = raw
            if isinstance(data, list):
                lines = []
                for item in data:
                    if isinstance(item, dict):
                        role = item.get("role") or "user"
                        content = item.get("content") or ""
                        if content:
                            lines.append(f"{role}: {content}")
                    else:
                        text = str(item)
                        if text:
                            lines.append(text)
                return "\n".join(lines).strip()
            if isinstance(data, dict):
                content = data.get("content") if isinstance(data, dict) else None
                return (content or str(data)).strip()
        if isinstance(raw, str):
            return raw.strip()
        try:
            return json.dumps(raw, ensure_ascii=False).strip()
        except Exception:
            return str(raw).strip()

    def _get_bootstrap_start_date(self) -> Optional[datetime]:
        documents = self.workspace.list_documents(
            doc_types=self.bootstrap_doc_types,
            limit=1,
        )
        if not documents:
            return None
        doc = documents[0]
        timestamp = doc.get("event_at") or doc.get("created_at") or doc.get("ingested_at")
        if not timestamp:
            return None
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        return ensure_utc(dt)

    def _bootstrap_prefix(self, batch_start: datetime, batch_end: datetime, doc_count: int) -> str:
        return (
            "Bootstrap batch context.\n"
            f"Window: {batch_start.date().isoformat()} to {batch_end.date().isoformat()}.\n"
            f"Document count: {doc_count}."
        )

    # --- Phase 3: Topic catalog methods ---

    def _get_topic_catalog(self):
        """Get or create topic catalog instance."""
        if not hasattr(self, "_topic_catalog"):
            from topic_catalog import TopicCatalog
            embedder = self.workspace.store.embedder if self.workspace.store else None
            self._topic_catalog = TopicCatalog(
                store=self.workspace.store,
                embedder=embedder,
            )
        return self._topic_catalog

    def _get_topic_change_detector(self):
        """Get or create topic change detector instance."""
        if not hasattr(self, "_topic_change_detector"):
            from topic_catalog import TopicChangeDetector
            catalog = self._get_topic_catalog()
            self._topic_change_detector = TopicChangeDetector(
                catalog=catalog,
                embedder=catalog.embedder,
                llm_judge=self.dspy_summarizer,
            )
        return self._topic_change_detector

    # Backward compatibility alias
    def _get_novelty_detector(self):
        """Get or create novelty detector instance (alias for _get_topic_change_detector)."""
        return self._get_topic_change_detector()

    def list_topics(self, status: Optional[str] = None) -> List[dict]:
        """List all topics in the catalog.
        
        Args:
            status: Filter by status ('active', 'archived', or None for all)
            
        Returns:
            List of topic dictionaries
        """
        catalog = self._get_topic_catalog()
        topics = catalog.list_topics(status=status)
        return [t.to_payload() for t in topics]

    def assign_to_topic(
        self,
        content: str,
        document_id: Optional[int] = None,
        use_llm: bool = True,
    ) -> Message:
        """Assign content to a topic using topic change detection.
        
        This is the main entry point for Phase 3 topic operations.
        It uses the hybrid pipeline: embedding similarity + optional LLM judge.
        Supports all operations: create, reuse, merge, split, rename.
        
        Args:
            content: Text content to analyze
            document_id: Optional document ID for tracking
            use_llm: Whether to use LLM for complex decisions
            
        Returns:
            Message with operation result
        """
        from topic_catalog import TopicEntry, TopicAssignment
        
        detector = self._get_topic_change_detector()
        catalog = self._get_topic_catalog()
        
        # Run topic change detection
        result = detector.detect(content, document_id=document_id, use_llm=use_llm)
        
        now = datetime.now(UTC)
        
        if result.decision == "create":
            return self._handle_topic_create(
                result, content, document_id, catalog, now, use_llm
            )
        
        elif result.decision == "reuse":
            return self._handle_topic_reuse(
                result, document_id, catalog, now, use_llm
            )
        
        elif result.decision == "merge":
            return self._handle_topic_merge(
                result, content, document_id, catalog, now
            )
        
        elif result.decision == "split":
            return self._handle_topic_split(
                result, content, document_id, catalog, now
            )
        
        elif result.decision == "rename":
            return self._handle_topic_rename(
                result, document_id, catalog, now
            )
        
        return Message(
            content=f"[topic] Unknown decision: {result.decision}",
            role=Role.SYSTEM,
        )

    def _handle_topic_create(
        self, result, content: str, document_id: Optional[int],
        catalog, now: datetime, use_llm: bool
    ) -> Message:
        """Handle topic create operation."""
        from topic_catalog import TopicEntry, TopicAssignment
        
        topic = TopicEntry(
            name=result.topic_name,
            summary=self._generate_topic_summary(content),
            status="active",
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        
        references = [{"document_id": document_id}] if document_id else []
        assignment = TopicAssignment(
            topic_name=topic.name,
            document_id=document_id or 0,
            assigned_at=now,
            rationale=result.rationale,
            similarity_score=0.0,
            scores=result.similarity_scores,
            decision_method="embedding" if not use_llm else "llm",
        )
        
        save_result = catalog.save_topic(topic, references=references, assignments=[assignment])
        
        if save_result:
            return Message(
                content=f"[topic] Created new topic '{topic.name}'. {result.rationale}",
                role=Role.SYSTEM,
            )
        return Message(
            content=f"[topic] Failed to create topic '{topic.name}'.",
            role=Role.SYSTEM,
        )

    def _handle_topic_reuse(
        self, result, document_id: Optional[int],
        catalog, now: datetime, use_llm: bool
    ) -> Message:
        """Handle topic reuse operation."""
        from topic_catalog import TopicAssignment
        
        topic = catalog.get_topic(result.topic_name)
        if not topic:
            return Message(
                content=f"[topic] Topic '{result.topic_name}' not found for reuse.",
                role=Role.SYSTEM,
            )
        
        topic.last_activity_at = now
        
        references = [{"document_id": document_id}] if document_id else []
        assignment = TopicAssignment(
            topic_name=topic.name,
            document_id=document_id or 0,
            assigned_at=now,
            rationale=result.rationale,
            similarity_score=result.similarity_scores.get(topic.name, 0.0),
            scores=result.similarity_scores,
            decision_method="embedding" if not use_llm else "llm",
        )
        
        save_result = catalog.save_topic(topic, references=references, assignments=[assignment])
        
        if save_result:
            return Message(
                content=f"[topic] Assigned to existing topic '{topic.name}'. {result.rationale}",
                role=Role.SYSTEM,
            )
        return Message(
            content=f"[topic] Failed to update topic '{topic.name}'.",
            role=Role.SYSTEM,
        )

    def _handle_topic_merge(
        self, result, content: str, document_id: Optional[int],
        catalog, now: datetime
    ) -> Message:
        """Handle topic merge operation."""
        from topic_catalog import TopicAssignment
        
        target_name = result.topic_name
        source_name = result.merge_from
        
        target_topic = catalog.get_topic(target_name)
        if not target_topic:
            return Message(
                content=f"[topic] Merge target '{target_name}' not found.",
                role=Role.SYSTEM,
            )
        
        # Archive the source topic if specified
        if source_name:
            source_topic = catalog.get_topic(source_name)
            if source_topic:
                # Update target summary to incorporate source
                merged_summary = f"{target_topic.summary}\n\n(Merged from '{source_name}': {source_topic.summary})"
                target_topic.summary = merged_summary[:500]  # Limit length
                
                # Archive source topic
                catalog.archive_topic(source_name)
        
        target_topic.last_activity_at = now
        
        references = [{"document_id": document_id}] if document_id else []
        assignment = TopicAssignment(
            topic_name=target_topic.name,
            document_id=document_id or 0,
            assigned_at=now,
            rationale=f"Merged: {result.rationale}",
            similarity_score=result.similarity_scores.get(target_topic.name, 0.0),
            scores=result.similarity_scores,
            decision_method="llm",
        )
        
        save_result = catalog.save_topic(target_topic, references=references, assignments=[assignment])
        
        if save_result:
            merge_msg = f" (merged from '{source_name}')" if source_name else ""
            return Message(
                content=f"[topic] Merged into topic '{target_topic.name}'{merge_msg}. {result.rationale}",
                role=Role.SYSTEM,
            )
        return Message(
            content=f"[topic] Failed to merge into topic '{target_topic.name}'.",
            role=Role.SYSTEM,
        )

    def _handle_topic_split(
        self, result, content: str, document_id: Optional[int],
        catalog, now: datetime
    ) -> Message:
        """Handle topic split operation."""
        from topic_catalog import TopicEntry, TopicAssignment
        
        source_name = result.topic_name
        new_topic_name = result.split_into
        
        if not new_topic_name:
            return Message(
                content=f"[topic] Split requires new topic name.",
                role=Role.SYSTEM,
            )
        
        source_topic = catalog.get_topic(source_name)
        if not source_topic:
            return Message(
                content=f"[topic] Split source '{source_name}' not found.",
                role=Role.SYSTEM,
            )
        
        # Create the new split topic
        new_topic = TopicEntry(
            name=new_topic_name,
            summary=self._generate_topic_summary(content),
            status="active",
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            tags={"split_from": source_name},
        )
        
        references = [{"document_id": document_id}] if document_id else []
        assignment = TopicAssignment(
            topic_name=new_topic.name,
            document_id=document_id or 0,
            assigned_at=now,
            rationale=f"Split from '{source_name}': {result.rationale}",
            similarity_score=0.0,
            scores=result.similarity_scores,
            decision_method="llm",
        )
        
        save_result = catalog.save_topic(new_topic, references=references, assignments=[assignment])
        
        if save_result:
            return Message(
                content=f"[topic] Split '{new_topic_name}' from '{source_name}'. {result.rationale}",
                role=Role.SYSTEM,
            )
        return Message(
            content=f"[topic] Failed to split topic '{new_topic_name}'.",
            role=Role.SYSTEM,
        )

    def _handle_topic_rename(
        self, result, document_id: Optional[int],
        catalog, now: datetime
    ) -> Message:
        """Handle topic rename operation."""
        from topic_catalog import TopicEntry, TopicAssignment
        
        old_name = result.rename_from
        new_name = result.topic_name
        
        if not old_name:
            return Message(
                content=f"[topic] Rename requires old topic name.",
                role=Role.SYSTEM,
            )
        
        old_topic = catalog.get_topic(old_name)
        if not old_topic:
            return Message(
                content=f"[topic] Topic '{old_name}' not found for rename.",
                role=Role.SYSTEM,
            )
        
        # Create new topic with updated name (preserving content)
        new_topic = TopicEntry(
            name=new_name,
            summary=old_topic.summary,
            status="active",
            created_at=old_topic.created_at,
            updated_at=now,
            last_activity_at=now,
            tags={"renamed_from": old_name, **old_topic.tags},
        )
        
        references = [{"document_id": document_id}] if document_id else []
        assignment = TopicAssignment(
            topic_name=new_topic.name,
            document_id=document_id or 0,
            assigned_at=now,
            rationale=f"Renamed from '{old_name}': {result.rationale}",
            similarity_score=result.similarity_scores.get(old_name, 0.0),
            scores=result.similarity_scores,
            decision_method="llm",
        )
        
        # Archive old topic
        catalog.archive_topic(old_name)
        
        save_result = catalog.save_topic(new_topic, references=references, assignments=[assignment])
        
        if save_result:
            return Message(
                content=f"[topic] Renamed '{old_name}' to '{new_name}'. {result.rationale}",
                role=Role.SYSTEM,
            )
        return Message(
            content=f"[topic] Failed to rename topic to '{new_name}'.",
            role=Role.SYSTEM,
        )

    def _generate_topic_summary(self, content: str) -> str:
        """Generate a brief summary for a new topic."""
        if self.dspy_summarizer:
            try:
                from prompts import topic_summary_instruction
                instruction = topic_summary_instruction()
                out = self.dspy_summarizer(
                    instruction=instruction,
                    recent_messages=content[:4000],  # Limit content
                    previous_summary="",
                )
                try:
                    payload = json.loads(out.payload_json or "{}")
                    return payload.get("summary") or content[:200]
                except (json.JSONDecodeError, AttributeError):
                    pass
            except Exception as e:
                print(f"[warn] Topic summary generation failed: {e}")
        
        # Fallback: use first lines of content
        lines = content.strip().split("\n")[:3]
        return " ".join(line.strip() for line in lines if line.strip())[:200]

    def process_new_documents_for_topics(
        self,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> Message:
        """Process recent documents and assign them to topics.
        
        This is the batch entry point for topic assignment.
        
        Args:
            since: Only process documents after this time
            limit: Maximum documents to process
            
        Returns:
            Message with processing summary
        """
        if not self.workspace.store:
            return Message(
                content="[topics] Store not available.",
                role=Role.SYSTEM,
            )
        
        # Get recent documents
        documents = self.workspace.list_documents(
            doc_types=self.bootstrap_doc_types,
            since=since,
            limit=limit,
        )
        
        if not documents:
            return Message(
                content="[topics] No new documents to process.",
                role=Role.SYSTEM,
            )
        
        assigned = 0
        created = 0
        errors = 0
        
        for doc in documents:
            doc_text = self._extract_document_text(doc)
            if not doc_text or len(doc_text) < 50:
                continue
            
            try:
                result = self.assign_to_topic(
                    content=doc_text,
                    document_id=doc.get("id"),
                    use_llm=bool(self.dspy_summarizer),
                )
                if "Created new" in result.content:
                    created += 1
                    assigned += 1
                elif "Assigned" in result.content or "Merged" in result.content:
                    assigned += 1
                else:
                    errors += 1
            except Exception as e:
                print(f"[warn] Topic assignment failed for doc {doc.get('id')}: {e}")
                errors += 1
        
        return Message(
            content=(
                f"[topics] Processed {len(documents)} documents. "
                f"Assigned: {assigned}, New topics: {created}, Errors: {errors}."
            ),
            role=Role.SYSTEM,
        )

    def timelinereply(self, user_text: str) -> List[Message]:
   
        # pre-process user input into chunks if possible
        input_chunk = self.timechunker(text=user_text, role_hint="user")

        instruction = textwrap.dedent(
            f"""
            You are a concise, independent-minded assistant. Use the memory context to keep continuity.
            Make good use of the structured time references in the user input if any. 
            When not sure about time reference, implicit assumptions, etc., ask for clarification. 
            """
        ).strip()
        
        return self.basereply(instruction=instruction, 
                              user_text=input_chunk)

    def summarize(self, cue: str = "") -> List[Message]:
        context = self.workspace.context_block(n=50)
        instruction = "Summarize the key facts and decisions from the memory context in bullet points. \
            If applicable construct a timeline of events."
        if self.dspy_predictor:
            try:
                out = self.dspy_predictor(instruction=instruction, 
                                          question=[Message(content=cue, role=Role.USER)] if cue else [],
                                          context=context)
                return out.response
            except Exception as e:
                return self._fallback_summary(note=f"(DSPy error: {e})")
        return self._fallback_summary()

    # --- local fallbacks (no network / no DSPy) -----------------------------
    def _fallback_reply(self, user_text: Union[str, List[Message]], note: str = "") -> List[Message]:
        if isinstance(user_text, list):
            parts = [m.content for m in user_text if getattr(m, "content", None)]
            user_text = "\n".join(parts)
        msg = Message(
            content=f"[local] Echo: {user_text} {note}".strip(),
            role=Role.ASSISTANT,
        )
        self.workspace.add(msg)
        return [msg]

    def _fallback_summary(self, note: str = "") -> List[Message]:
        return [Message(
            content=f"[local] Summary not available {note}".strip(), 
            role=Role.ASSISTANT
        )]

    def summarize_rss(self, items: List[dict], keywords: List[str]) -> List[Message]:
        """Summarize RSS items; uses DSPy if available, else produces a simple digest."""
        # Build prompt-ready text
        content_lines = []
        for i, item in enumerate(items, 1):
            title = item.get("title") or "(no title)"
            url = item.get("link") or ""
            feed = item.get("feed") or ""
            summary = item.get("summary", "")
            content_lines.append(f"[{i}] {title}\nfeed: {feed}\nurl: {url}\nsummary: {summary}")
        joined = "\n\n".join(content_lines)
        if self.dspy_predictor:
            try:
                instruction = rss_summary_instruction(keywords=keywords)
                self.last_llm_trace = {
                    "type": "rss_summary",
                    "ts": datetime.now(UTC).isoformat(),
                    "instruction": instruction,
                    "items": items,
                }
                out = self.dspy_predictor(
                    instruction=instruction,
                    question=[],
                    context=[Message(content=joined, role=Role.USER)],
                )
                for m in out.response:
                    # Use chunked ingestion for RSS summaries
                    try:
                        self.workspace.store.add_document_chunked(
                            content=m.content,
                            document_meta={
                                "doc_type": "rss_daily_summary",
                                "title": "RSS daily brief",
                                "source": "rss",
                                "tags": {"keywords": keywords, "count": len(items)},
                            }
                        )
                    except Exception as e:
                        print(f"[warn] Chunked RSS ingestion failed, falling back: {e}")
                        self.workspace.add_with_document(
                            [m],
                            document_meta={
                                "doc_type": "rss_daily_summary",
                                "title": "RSS daily brief",
                                "source": "rss",
                                "tags": {"keywords": keywords, "count": len(items)},
                            },
                        )
                return out.response
            except Exception as e:
                return [Message(content=f"[local] RSS summary error: {e}", role=Role.ASSISTANT)]
        # fallback digest
        bullet_lines = []
        for i, item in enumerate(items, 1):
            title = item.get("title") or "(no title)"
            url = item.get("link") or ""
            feed = item.get("feed") or ""
            bullet_lines.append(f"- [{i}] {title} ({feed}) {url}")
        bullet_lines.append(f"\nKeywords: {', '.join(keywords)}")
        msg = Message(content="\n".join(bullet_lines), role=Role.ASSISTANT)
        # Use chunked ingestion for RSS summaries (fallback case)
        try:
            self.workspace.store.add_document_chunked(
                content=msg.content,
                document_meta={
                    "doc_type": "rss_daily_summary",
                    "title": "RSS daily brief",
                    "source": "rss",
                    "tags": {"keywords": keywords, "count": len(items)},
                }
            )
        except Exception as e:
            print(f"[warn] Chunked RSS ingestion failed, falling back: {e}")
            self.workspace.add_with_document(
                [msg],
                document_meta={
                    "doc_type": "rss_daily_summary",
                    "title": "RSS daily brief",
                    "source": "rss",
                    "tags": {"keywords": keywords, "count": len(items)},
                },
            )
        return [msg]
