"""Token-aware chunking with structure preservation for enhanced retrieval.

This module provides pluggable chunkers for different document types:
- Fixed-token chunking for general text
- Header-aware chunking for markdown notes and structured content
- Sentence-window chunking for academic papers
- Message-based chunking for chat conversations

All chunkers return chunks with provenance metadata (char offsets, sequence, headings, etc.)
to support precise citations and neighbor expansion during context assembly.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple
from pathlib import Path

# Lazy import transformers tokenizer to avoid startup overhead
_tokenizer = None


def _get_tokenizer():
    """Get or initialize the tokenizer (lazy loading)."""
    global _tokenizer
    if _tokenizer is None:
        try:
            from transformers import AutoTokenizer
            from config import MODEL_NAME
            # Use the same model as embeddings for consistency
            _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        except Exception as e:
            print(f"[warn] Failed to load tokenizer, using fallback: {e}")
            # Simple fallback: approximate 1 token ~= 4 chars
            _tokenizer = "fallback"
    return _tokenizer


def count_tokens(text: str) -> int:
    """Count tokens in text using the embedding model's tokenizer."""
    tokenizer = _get_tokenizer()
    if tokenizer == "fallback":
        # Rough approximation: 1 token ~= 4 characters for English
        return len(text) // 4
    try:
        return len(tokenizer.encode(text, add_special_tokens=False))
    except Exception:
        return len(text) // 4


@dataclass
class Chunk:
    """A chunk of text with provenance metadata."""
    text: str
    char_start: int  # Start position in original document
    char_end: int    # End position in original document
    seq: int         # Sequence number in document
    
    # Optional metadata for navigation and context
    heading_path: Optional[str] = None  # e.g., "Introduction > Background"
    page_number: Optional[int] = None   # For papers/PDFs
    token_count: Optional[int] = None   # Actual measured token count
    chunk_window: Optional[str] = None  # Not used during chunking; filled at context assembly time
    
    def __post_init__(self):
        """Calculate token count if not provided."""
        if self.token_count is None:
            self.token_count = count_tokens(self.text)


class Chunker(Protocol):
    """Protocol for text chunkers."""
    
    def chunk(self, text: str, metadata: Optional[dict] = None) -> List[Chunk]:
        """Split text into chunks with provenance metadata.
        
        Args:
            text: The full document text to chunk
            metadata: Optional document metadata (url, title, etc.)
            
        Returns:
            List of Chunk objects with char offsets and sequence numbers
        """
        ...


class FixedTokenChunker:
    """Token-aware chunker with fixed size and optional overlap.
    
    Uses the embedding model's tokenizer to measure chunk size.
    Default: 240-300 tokens per chunk, minimal overlap (~10-30 tokens).
    """
    
    def __init__(
        self,
        chunk_size: int = 256,
        overlap_tokens: int = 20,
        min_chunk_size: int = 50
    ):
        """Initialize fixed-token chunker.
        
        Args:
            chunk_size: Target tokens per chunk (default 256 for MiniLM)
            overlap_tokens: Overlap between chunks in tokens (default 20)
            min_chunk_size: Minimum chunk size in tokens (default 50)
        """
        self.chunk_size = chunk_size
        self.overlap_tokens = overlap_tokens
        self.min_chunk_size = min_chunk_size
    
    def chunk(self, text: str, metadata: Optional[dict] = None) -> List[Chunk]:
        """Split text into token-sized chunks with overlap."""
        if not text or not text.strip():
            return []
        
        tokenizer = _get_tokenizer()
        
        # For fallback mode, use character-based approximation
        if tokenizer == "fallback":
            return self._chunk_by_chars(text)
        
        # Encode full text with offsets to preserve original text (avoids CJK spacing issues)
        try:
            encoded = tokenizer(
                text,
                add_special_tokens=False,
                return_offsets_mapping=True,
            )
            tokens = encoded["input_ids"]
            offsets = encoded["offset_mapping"]
        except Exception:
            return self._chunk_by_chars(text)
        
        if not tokens or not offsets:
            return self._chunk_by_chars(text)
        
        chunks = []
        seq = 0
        start_token_idx = 0
        
        while start_token_idx < len(tokens):
            # Calculate end token index
            end_token_idx = min(start_token_idx + self.chunk_size, len(tokens))
            
            # Extract chunk tokens
            chunk_tokens = tokens[start_token_idx:end_token_idx]
            
            # Skip if chunk is too small (except for last chunk)
            if len(chunk_tokens) < self.min_chunk_size and end_token_idx < len(tokens):
                start_token_idx = end_token_idx - self.overlap_tokens
                continue
            
            # Use offsets to slice original text (no decode artifacts)
            start_char = offsets[start_token_idx][0]
            end_char = offsets[end_token_idx - 1][1]
            chunk_text = text[start_char:end_char]
            
            chunks.append(Chunk(
                text=chunk_text,
                char_start=start_char,
                char_end=end_char,
                seq=seq,
                token_count=len(chunk_tokens)
            ))
            
            seq += 1
            
            # Move to next chunk with overlap
            if end_token_idx >= len(tokens):
                break
            start_token_idx = end_token_idx - self.overlap_tokens
        
        return chunks
    
    def _chunk_by_chars(self, text: str) -> List[Chunk]:
        """Fallback character-based chunking when tokenizer unavailable."""
        # Approximate: 256 tokens ~= 1024 chars, 20 tokens ~= 80 chars
        chunk_chars = self.chunk_size * 4
        overlap_chars = self.overlap_tokens * 4
        
        chunks = []
        seq = 0
        start = 0
        
        while start < len(text):
            end = min(start + chunk_chars, len(text))
            chunk_text = text[start:end]
            
            chunks.append(Chunk(
                text=chunk_text,
                char_start=start,
                char_end=end,
                seq=seq
            ))
            
            seq += 1
            if end >= len(text):
                break
            start = end - overlap_chars
        
        return chunks


class SentenceSplitter:
    """Helper to split text into sentences."""
    
    # Sentence boundary detection (ASCII + multilingual punctuation + paragraph breaks)
    SENTENCE_BOUNDARY = re.compile(
        r"(?:"
        # ASCII punctuation requiring whitespace/end
        r"(?:[.!?]+(?:[\"'\)\]\}]+)?(?:\s+|$))"
        # CJK punctuation that does NOT require whitespace
        r"|(?:[。！？；]+|[｡。．]+)(?:[\"'\)\]\}]+)?"
        # Paragraph breaks
        r"|[\n]{2,}"
        r")",
        re.UNICODE,
    )
    
    @staticmethod
    def split(text: str) -> List[str]:
        """Split text into sentences."""
        if not text:
            return []

        return [text[start:end] for start, end in SentenceSplitter.split_with_spans(text)]

    @staticmethod
    def split_with_spans(text: str) -> List[Tuple[int, int]]:
        """Split text into sentence spans (start, end) preserving separators."""
        if not text:
            return []

        spans: List[Tuple[int, int]] = []
        start = 0

        for match in SentenceSplitter.SENTENCE_BOUNDARY.finditer(text):
            end = match.end()
            if end > start:
                spans.append((start, end))
                start = end

        if start < len(text):
            spans.append((start, len(text)))

        return spans


class SentenceWindowChunker:
    """Chunk text by grouping sentences to target token size.
    
    Ideal for academic papers where sentence boundaries are meaningful.
    Targets ~260 tokens per chunk by grouping complete sentences.
    """
    
    def __init__(self, target_tokens: int = 260, min_tokens: int = 100):
        """Initialize sentence window chunker.
        
        Args:
            target_tokens: Target tokens per chunk (default 260)
            min_tokens: Minimum tokens to start a new chunk (default 100)
        """
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.splitter = SentenceSplitter()
    
    def chunk(self, text: str, metadata: Optional[dict] = None) -> List[Chunk]:
        """Split text into sentence-grouped chunks."""
        if not text or not text.strip():
            return []
        
        sentences = self.splitter.split(text)
        if not sentences:
            return []
        
        chunks = []
        current_sentences = []
        current_tokens = 0
        char_start = 0
        seq = 0
        
        for sentence in sentences:
            sentence_tokens = count_tokens(sentence)
            
            # Check if adding this sentence would exceed target
            if current_tokens > 0 and current_tokens + sentence_tokens > self.target_tokens:
                # Finalize current chunk if it meets minimum
                if current_tokens >= self.min_tokens:
                    chunk_text = ''.join(current_sentences)
                    chunks.append(Chunk(
                        text=chunk_text,
                        char_start=char_start,
                        char_end=char_start + len(chunk_text),
                        seq=seq,
                        token_count=current_tokens,
                        page_number=metadata.get('page_number') if metadata else None
                    ))
                    seq += 1
                    char_start += len(chunk_text)
                    current_sentences = []
                    current_tokens = 0
            
            # Add sentence to current chunk
            current_sentences.append(sentence)
            current_tokens += sentence_tokens
        
        # Add final chunk
        if current_sentences:
            chunk_text = ''.join(current_sentences)
            chunks.append(Chunk(
                text=chunk_text,
                char_start=char_start,
                char_end=char_start + len(chunk_text),
                seq=seq,
                token_count=current_tokens,
                page_number=metadata.get('page_number') if metadata else None
            ))
        
        return chunks


class HeaderAwareChunker:
    """Chunk markdown/HTML by headers, then apply token limits within sections.
    
    Preserves document structure by respecting heading boundaries.
    Stores heading path for navigation and context.
    """
    
    # Markdown headers: # Header, ## Subheader, etc.
    MD_HEADER = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    
    def __init__(self, max_tokens: int = 300, min_section_tokens: int = 50):
        """Initialize header-aware chunker.
        
        Args:
            max_tokens: Maximum tokens per chunk (will split long sections)
            min_section_tokens: Minimum tokens for a section to be its own chunk
        """
        self.max_tokens = max_tokens
        self.min_section_tokens = min_section_tokens
        self.fixed_chunker = FixedTokenChunker(chunk_size=max_tokens, overlap_tokens=0)
        self.sentence_splitter = SentenceSplitter()
    
    def chunk(self, text: str, metadata: Optional[dict] = None) -> List[Chunk]:
        """Split text by headers, then chunk long sections."""
        if not text or not text.strip():
            return []
        
        # Find all headers and their positions
        sections = self._split_by_headers(text)
        
        if not sections:
            # No headers found, use fixed chunker
            return self.fixed_chunker.chunk(text, metadata)
        
        # Process each section
        chunks = []
        seq = 0
        
        for section in sections:
            section_text = section['text']
            section_tokens = count_tokens(section_text)
            
            # If section fits in one chunk, keep it whole
            if section_tokens <= self.max_tokens:
                chunks.append(Chunk(
                    text=section_text,
                    char_start=section['char_start'],
                    char_end=section['char_end'],
                    seq=seq,
                    heading_path=section['heading_path'],
                    token_count=section_tokens
                ))
                seq += 1
            else:
                # Split long section using sentence-aware packing
                section_chunks = self._chunk_section_sentences(section, metadata)
                for chunk in section_chunks:
                    chunks.append(Chunk(
                        text=chunk.text,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        seq=seq,
                        heading_path=section['heading_path'],
                        token_count=chunk.token_count
                    ))
                    seq += 1
        
        return chunks
    
    def _split_by_headers(self, text: str) -> List[dict]:
        """Split text into sections based on markdown headers."""
        matches = list(self.MD_HEADER.finditer(text))
        
        if not matches:
            return []
        
        sections = []
        heading_stack = []  # Stack to track heading hierarchy

        # Capture any preamble text before the first heading so we don't drop content
        first_start = matches[0].start()
        if first_start > 0:
            preamble = text[:first_start]
            if preamble.strip():
                sections.append({
                    'text': preamble,
                    'char_start': 0,
                    'char_end': first_start,
                    'heading_path': None,
                    'level': 0
                })
        
        for i, match in enumerate(matches):
            level = len(match.group(1))  # Number of # symbols
            title = match.group(2).strip()
            start = match.start()
            
            # Update heading stack for this level
            # Remove deeper levels
            heading_stack = [h for h in heading_stack if h['level'] < level]
            # Add current heading
            heading_stack.append({'level': level, 'title': title})
            
            # Build heading path
            heading_path = ' > '.join(h['title'] for h in heading_stack)
            
            # Determine section text
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(text)
            
            # Preserve whitespace to keep char offsets aligned with original text
            section_text = text[start:end]
            
            if section_text.strip():
                sections.append({
                    'text': section_text,
                    'char_start': start,
                    'char_end': end,
                    'heading_path': heading_path,
                    'level': level
                })
        
        return sections

    def _chunk_section_sentences(self, section: dict, metadata: Optional[dict] = None) -> List[Chunk]:
        """Split a long section using sentence-aware packing."""
        section_text = section['text']
        section_start = section['char_start']

        spans = self.sentence_splitter.split_with_spans(section_text)
        if not spans:
            return []

        chunks: List[Chunk] = []
        current_start = None
        current_end = None
        current_tokens = 0

        for start, end in spans:
            sentence_text = section_text[start:end]
            sentence_tokens = count_tokens(sentence_text)

            if sentence_tokens > self.max_tokens:
                if current_tokens > 0 and current_start is not None and current_end is not None:
                    chunk_text = section_text[current_start:current_end]
                    chunks.append(Chunk(
                        text=chunk_text,
                        char_start=section_start + current_start,
                        char_end=section_start + current_end,
                        seq=0,
                        token_count=current_tokens
                    ))
                    current_start = None
                    current_end = None
                    current_tokens = 0

                sentence_chunks = self.fixed_chunker.chunk(sentence_text, metadata)
                for chunk in sentence_chunks:
                    chunks.append(Chunk(
                        text=chunk.text,
                        char_start=section_start + start + chunk.char_start,
                        char_end=section_start + start + chunk.char_end,
                        seq=0,
                        token_count=chunk.token_count
                    ))
                continue

            if current_tokens > 0 and current_tokens + sentence_tokens > self.max_tokens:
                if current_start is not None and current_end is not None:
                    chunk_text = section_text[current_start:current_end]
                    chunks.append(Chunk(
                        text=chunk_text,
                        char_start=section_start + current_start,
                        char_end=section_start + current_end,
                        seq=0,
                        token_count=current_tokens
                    ))
                current_start = None
                current_end = None
                current_tokens = 0

            if current_tokens == 0:
                current_start = start
                current_end = end
                current_tokens = sentence_tokens
            else:
                current_end = end
                current_tokens += sentence_tokens

        if current_tokens > 0 and current_start is not None and current_end is not None:
            chunk_text = section_text[current_start:current_end]
            chunks.append(Chunk(
                text=chunk_text,
                char_start=section_start + current_start,
                char_end=section_start + current_end,
                seq=0,
                token_count=current_tokens
            ))

        return chunks


class MessageChunker:
    """Chunk chat messages, keeping each message as a chunk.
    
    Optionally creates turn windows (3-7 messages) for dense search.
    Individual messages are always preserved for exact retrieval.
    """
    
    def __init__(self, create_windows: bool = False, window_size: int = 5):
        """Initialize message chunker.
        
        Args:
            create_windows: Whether to create turn windows in addition to individual messages
            window_size: Number of messages per window (default 5)
        """
        self.create_windows = create_windows
        self.window_size = window_size
    
    def chunk(self, messages: List[str], metadata: Optional[dict] = None) -> List[Chunk]:
        """Chunk a list of messages.
        
        Args:
            messages: List of message texts
            metadata: Optional metadata (not used for now)
            
        Returns:
            List of chunks, one per message (plus optional windows)
        """
        if not messages:
            return []
        
        chunks = []
        
        # Individual message chunks
        char_offset = 0
        for seq, msg in enumerate(messages):
            chunks.append(Chunk(
                text=msg,
                char_start=char_offset,
                char_end=char_offset + len(msg),
                seq=seq
            ))
            char_offset += len(msg) + 1  # +1 for implicit separator
        
        # Optional: create sliding windows for context
        if self.create_windows and len(messages) >= 3:
            for i in range(len(messages) - self.window_size + 1):
                window_messages = messages[i:i + self.window_size]
                window_text = '\n'.join(window_messages)
                
                # Calculate char positions
                start_chunk = chunks[i]
                end_chunk = chunks[i + self.window_size - 1]
                
                chunks.append(Chunk(
                    text=window_text,
                    char_start=start_chunk.char_start,
                    char_end=end_chunk.char_end,
                    seq=len(chunks),  # Sequential after individual messages
                    heading_path=f"Turn window {i}-{i + self.window_size}"
                ))
        
        return chunks


def get_chunker_for_doctype(doc_type: str, **kwargs) -> Chunker:
    """Factory function to get appropriate chunker for document type.
    
    Args:
        doc_type: Document type (note, web_page, paper, chat, rss_daily_summary)
        **kwargs: Additional arguments passed to chunker constructor
        
    Returns:
        Appropriate Chunker instance
    """
    if doc_type == 'note':
        return HeaderAwareChunker(**kwargs)
    elif doc_type == 'web_page':
        return HeaderAwareChunker(**kwargs)
    elif doc_type == 'paper':
        return SentenceWindowChunker(**kwargs)
    elif doc_type == 'chat':
        # Return MessageChunker for chat conversations
        return MessageChunker(**kwargs)
    elif doc_type == 'rss_daily_summary':
        # RSS summaries are typically single chunks
        return FixedTokenChunker(chunk_size=2000, overlap_tokens=0, **kwargs)
    else:
        # Default to fixed token chunker
        return FixedTokenChunker(**kwargs)
