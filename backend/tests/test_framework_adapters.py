"""LangChain (BaseRetriever + cadena LCEL) y LlamaIndex (cuarto backend de conocimiento)."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.rag.langchain_adapter import BankPolicyRetriever, build_rag_chain
from app.tools.knowledge_base import get_keyword_retriever


def test_project_retriever_works_as_langchain_retriever():
    retriever = BankPolicyRetriever(retriever=get_keyword_retriever(), k=3, product_hint="tarjeta_debito")
    docs = retriever.invoke("¿cuánto cuesta reponer la tarjeta de débito?")
    assert docs and docs[0].metadata["id"] == "POL-004"
    assert docs[0].page_content


def test_lcel_rag_chain_puts_retrieved_context_in_the_prompt():
    seen = {}

    class SpyModel(FakeListChatModel):
        def _call(self, messages, *args, **kwargs):
            seen["prompt"] = "\n".join(str(m.content) for m in messages)
            return super()._call(messages, *args, **kwargs)

    chain = build_rag_chain(
        BankPolicyRetriever(retriever=get_keyword_retriever()), SpyModel(responses=["Bs 30 [POL-004]"])
    )
    assert chain.invoke("¿cuánto cuesta reponer la tarjeta de débito?") == "Bs 30 [POL-004]"
    assert "[POL-004]" in seen["prompt"] and "PREGUNTA: ¿cuánto cuesta reponer" in seen["prompt"]


def test_llamaindex_retriever_hybrid_and_abstains():
    pytest.importorskip("llama_index.core")
    from app.config import get_settings
    from app.rag.embeddings import HashEmbedder
    from app.rag.llamaindex_retriever import LlamaIndexHybridRetriever
    from app.tools.knowledge_base import load_documents

    docs = load_documents(get_settings().seed_dir / "knowledge.json")
    # HashEmbedder: sin descargar modelos en CI; el canal BM25 de LlamaIndex lleva el peso
    retriever = LlamaIndexHybridRetriever(docs, HashEmbedder(), min_similarity=0.5)
    found = retriever.search("límite de retiro en cajero de la tarjeta de débito", k=4, product_hint="tarjeta_debito")
    assert "POL-003" in [d["id"] for d in found]
    assert {"id", "title", "text", "score"} <= set(found[0])
    strict = LlamaIndexHybridRetriever(docs, HashEmbedder(), min_similarity=0.99)
    assert strict.search("¿quién ganó el partido de ayer?") == []
