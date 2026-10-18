"""Fusión de rankings para la recuperación híbrida (compartida por Neo4j y pgvector)."""

import re
from collections import defaultdict


def rrf(rankings: dict[str, list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(d) = Σ_listas 1 / (k + posición). Robusto a escalas distintas."""
    scores: dict[str, float] = defaultdict(float)
    for ranked_ids in rankings.values():
        # Cada documento cuenta UNA vez por lista (su mejor posición). Sin esto, una política
        # compartida por dos productos aparecía dos veces en la lista "entidad" y duplicaba su
        # score (bug detectado por los Evals, caso P01).
        unique_ids = list(dict.fromkeys(ranked_ids))
        for position, doc_id in enumerate(unique_ids, start=1):
            scores[doc_id] += 1.0 / (k + position)
    return dict(scores)


def lucene_query(text: str) -> str:
    """Convierte la pregunta en una query Lucene segura (sin operadores ni caracteres especiales)."""
    words = re.findall(r"\w{3,}", text.lower())
    return " ".join(words)
