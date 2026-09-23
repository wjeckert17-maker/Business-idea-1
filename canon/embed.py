"""Text embedders. Two backends behind one interface so the matcher does not care:
  HashingEmbedder            no dependencies; hashed word+char n-gram TF-IDF, random-projected to 512-d
  SentenceTransformerEmbedder  all-MiniLM-L6-v2 (384-d) when sentence-transformers is installed
Both return L2-normalized float32 rows, so cosine == dot.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Dict, List, Optional, Sequence

import numpy as np

from .text import norm_title


class HashingEmbedder:
    name = "hashing-tfidf-srp512"

    def __init__(self, n_features: int = 1 << 18, dim: int = 512, seed: int = 7):
        self.n_features, self.dim, self.seed = n_features, dim, seed
        self.idf: Optional[np.ndarray] = None
        rng = np.random.default_rng(seed)
        self._proj = rng.standard_normal((n_features, dim), dtype=np.float32) / math.sqrt(dim)

    def _features(self, text: str) -> Dict[int, float]:
        toks = norm_title(text).split()
        feats: Dict[int, float] = {}
        def add(s, w=1.0):
            h = int(hashlib.blake2b(s.encode(), digest_size=4).hexdigest(), 16) % self.n_features
            feats[h] = feats.get(h, 0.0) + w
        for t in toks:
            add("w:" + t)
            padded = f" {t} "
            for n in (3, 4):
                for i in range(len(padded) - n + 1):
                    add("c:" + padded[i:i + n], 0.5)
        for a, b in zip(toks, toks[1:]):
            add("b:" + a + "_" + b, 1.5)
        return feats

    def fit(self, texts: Sequence[str]) -> "HashingEmbedder":
        df = np.zeros(self.n_features, dtype=np.float32)
        for t in texts:
            for h in self._features(t):
                df[h] += 1
        n = max(1, len(texts))
        self.idf = np.log((n + 1) / (df + 1)) + 1.0
        return self

    def sparse(self, text: str) -> Dict[int, float]:
        idf = self.idf if self.idf is not None else None
        f = self._features(text)
        vec = {h: (1 + math.log(c)) * (idf[h] if idf is not None else 1.0) for h, c in f.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {h: v / norm for h, v in vec.items()}

    @staticmethod
    def sparse_cos(a: Dict[int, float], b: Dict[int, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return float(sum(v * b.get(h, 0.0) for h, v in a.items()))

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            sp = self.sparse(t)
            if not sp:
                continue
            idx = np.fromiter(sp.keys(), dtype=np.int64, count=len(sp))
            val = np.fromiter(sp.values(), dtype=np.float32, count=len(sp))
            v = val @ self._proj[idx]
            n = np.linalg.norm(v)
            out[i] = v / n if n > 0 else v
        return out


class SentenceTransformerEmbedder:
    name = "all-MiniLM-L6-v2"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", batch_size: int = 256):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.batch_size = batch_size

    def fit(self, texts):
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.model.get_sentence_embedding_dimension()), dtype=np.float32)
        e = self.model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(e, dtype=np.float32)


def get_embedder(name: str):
    if name in ("hashing", HashingEmbedder.name):
        return HashingEmbedder()
    if name in ("minilm", "all-MiniLM-L6-v2"):
        return SentenceTransformerEmbedder()
    raise KeyError(name)


class EmbeddingCache:
    """Disk cache keyed by (embedder name, text) so retrains do not re-encode 200k titles."""

    def __init__(self, path: str, embedder):
        self.path, self.embedder = path, embedder
        self._mem: Dict[str, np.ndarray] = {}
        if os.path.exists(path):
            z = np.load(path, allow_pickle=False)
            keys = z["keys"].tolist()
            self._mem = dict(zip(keys, z["vecs"]))

    @staticmethod
    def key(embedder_name: str, text: str) -> str:
        return hashlib.sha1(f"{embedder_name}\x1f{text}".encode()).hexdigest()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        keys = [self.key(self.embedder.name, t) for t in texts]
        missing = sorted({k for k in keys if k not in self._mem})
        if missing:
            by_key = {self.key(self.embedder.name, t): t for t in texts}
            vecs = self.embedder.encode([by_key[k] for k in missing])
            for k, v in zip(missing, vecs):
                self._mem[k] = v
            self.save()
        return np.stack([self._mem[k] for k in keys]) if keys else np.zeros((0, 1), dtype=np.float32)

    def save(self) -> None:
        if not self._mem:
            return
        keys = np.array(list(self._mem.keys()))
        vecs = np.stack(list(self._mem.values()))
        np.savez(self.path, keys=keys, vecs=vecs)
