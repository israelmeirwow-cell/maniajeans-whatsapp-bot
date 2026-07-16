"""Pluggable text embeddings for semantic retrieval.

Backends, tried in order (first available wins):
  1. Voyage AI  — if VOYAGE_API_KEY set and `voyageai` installed (Anthropic-recommended, multilingual)
  2. OpenAI     — if OPENAI_API_KEY set and `openai` installed
  3. Local      — a multilingual `sentence-transformers` model (no API key, runs on CPU)

If none is available, `Embedder.available` is False and callers fall back to keyword search.
All vectors are returned L2-normalized, so cosine similarity == dot product.
"""
import os
from typing import List
import numpy as np

# multilingual (Hebrew-capable), CPU-friendly; override via env
LOCAL_MODEL = os.environ.get("EMBEDDINGS_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


class Embedder:
    def __init__(self):
        self.kind = None
        self._voyage = self._openai = self._st = None
        self._init_backend()

    def _init_backend(self) -> None:
        if os.environ.get("VOYAGE_API_KEY"):
            try:
                import voyageai
                self._voyage = voyageai.Client()
                self.kind = "voyage"
                return
            except Exception:
                pass
        if os.environ.get("OPENAI_API_KEY"):
            try:
                from openai import OpenAI
                self._openai = OpenAI()
                self.kind = "openai"
                return
            except Exception:
                pass
        try:
            from sentence_transformers import SentenceTransformer
            self._st = SentenceTransformer(LOCAL_MODEL)
            self.kind = "local"
            return
        except Exception:
            self.kind = None

    @property
    def available(self) -> bool:
        return self.kind is not None

    def embed(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype="float32")
        if self.kind == "voyage":
            r = self._voyage.embed(texts, model="voyage-3",
                                   input_type="query" if is_query else "document")
            v = np.array(r.embeddings, dtype="float32")
        elif self.kind == "openai":
            r = self._openai.embeddings.create(model="text-embedding-3-small", input=texts)
            v = np.array([d.embedding for d in r.data], dtype="float32")
        elif self.kind == "local":
            # e5-family models expect "query:"/"passage:" prefixes; MiniLM does not.
            if "e5" in LOCAL_MODEL.lower():
                prefix = "query: " if is_query else "passage: "
                texts = [prefix + t for t in texts]
            v = self._st.encode(texts, convert_to_numpy=True, normalize_embeddings=False).astype("float32")
        else:
            raise RuntimeError("no embedder available")
        norm = np.linalg.norm(v, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return v / norm


_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
