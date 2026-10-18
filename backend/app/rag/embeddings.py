"""Embeddings para el índice vectorial de Neo4j.

- fastembed: paraphrase-multilingual-MiniLM-L12-v2 (384 dims) vía ONNX en CPU. Sin torch,
  sin GPU, sin API key: apto para on-premise. ~220 MB que se descargan una sola vez.
- hash: vector determinista por hashing de tokens (solo para tests/CI sin descarga).
  NO captura semántica; sirve para probar el cableado, no la calidad.
"""

import hashlib
import math
from functools import lru_cache
from typing import Protocol

from app.config import get_settings
from app.tools.knowledge_base import tokenize

EMBEDDING_DIM = 384


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # import diferido: carga ONNX solo si se usa

        self.model = TextEmbedding(model_name=model_name)
        self.dim = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]


class HashEmbedder:
    dim = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in tokenize(text):
                index = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16) % self.dim
                vector[index] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embeddings_backend == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder(settings.embeddings_model)
