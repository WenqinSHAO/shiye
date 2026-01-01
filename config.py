import os
from pathlib import Path

DATA_DIR = Path(os.getenv("SHIYE_DATA_DIR", Path.home() / ".shiye"))
DB_PATH = DATA_DIR / "shiye.db"
INDEX_PATH = DATA_DIR / "shiye.faiss"
MODEL_NAME = os.getenv("SHIYE_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MODEL_CACHE = Path(os.getenv("SHIYE_MODEL_CACHE", DATA_DIR / "models"))
VECTOR_DIM_ENV = os.getenv("SHIYE_VECTOR_DIM")  # allow override when swapping models

# Retrieval settings (v0.7)
SHIYE_RERANKER = os.getenv("SHIYE_RERANKER", "flashrank")  # 'flashrank', 'bge', 'none'
SHIYE_RERANK_TOP_K = int(os.getenv("SHIYE_RERANK_TOP_K", "50"))
SHIYE_SEARCH_TOP_K = int(os.getenv("SHIYE_SEARCH_TOP_K", "20"))
SHIYE_RRF_K = int(os.getenv("SHIYE_RRF_K", "60"))  # RRF constant
SHIYE_RECENCY_DECAY_DAYS = int(os.getenv("SHIYE_RECENCY_DECAY_DAYS", "30"))
