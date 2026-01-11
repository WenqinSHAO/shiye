import chunking


def test_fixed_token_chunker_preserves_text_for_cjk():
    """FixedTokenChunker should not insert spaces when slicing CJK text."""
    # Avoid loading large tokenizer; force fallback mode
    chunking._tokenizer = "fallback"
    from chunking import FixedTokenChunker

    text = "十年春秋夏，只识故乡冬。记某场古诗大赛妙句，写外出打工人心境。"
    chunker = FixedTokenChunker(chunk_size=3, overlap_tokens=0, min_chunk_size=1)
    chunks = chunker.chunk(text)
    assert "".join(c.text for c in chunks) == text
