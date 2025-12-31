import os
from pathlib import Path

DATA_DIR = Path(os.getenv("SHIYE_DATA_DIR", Path.home() / ".shiye"))
DB_PATH = DATA_DIR / "shiye.db"
INDEX_PATH = DATA_DIR / "shiye.faiss"
MODEL_NAME = os.getenv("SHIYE_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MODEL_CACHE = Path(os.getenv("SHIYE_MODEL_CACHE", DATA_DIR / "models"))
VECTOR_DIM_ENV = os.getenv("SHIYE_VECTOR_DIM")  # allow override when swapping models
