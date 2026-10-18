"""Integración con el ecosistema LangChain.

- BankPolicyRetriever: cualquier backend de conocimiento del proyecto (BM25, Neo4j, pgvector,
  LlamaIndex) como `BaseRetriever` de LangChain. Se puede usar en cualquier cadena LCEL,
  agente o herramienta de LangChain sin reescribir la recuperación.
- ANSWER_PROMPT + build_rag_chain: la cadena RAG del agente de conocimiento en LCEL
  (retriever | contexto → prompt → modelo → texto). El nodo del grafo usa ANSWER_PROMPT | llm
  para conservar el AIMessage (tokens y costo); build_rag_chain es la versión de punta a punta.
"""

from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from pydantic import ConfigDict

SYSTEM_PROMPT = """Eres el agente de conocimiento de un banco. Responde en español, breve y claro.
Usa ÚNICAMENTE la información del CONTEXTO. Si el contexto no responde la pregunta, di que no tienes esa información.
Cita cada dato con el id del documento entre corchetes, por ejemplo [POL-003]. No inventes montos, tasas ni plazos."""

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", SYSTEM_PROMPT), ("human", "CONTEXTO:\n{context}\n\nPREGUNTA: {question}")]
)


class BankPolicyRetriever(BaseRetriever):
    """Adaptador: un Retriever del proyecto como BaseRetriever de LangChain."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    retriever: Any  # cualquier Retriever del proyecto (Protocol: no lo valida Pydantic)
    k: int = 4
    product_hint: str | None = None

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return [
            Document(
                id=d["id"],
                page_content=d["text"],
                metadata={"id": d["id"], "title": d["title"], "type": d.get("type"), "score": d.get("score")},
            )
            for d in self.retriever.search(query, k=self.k, product_hint=self.product_hint)
        ]


def format_documents(docs: list[Document]) -> str:
    return "\n\n".join(f"[{d.metadata['id']}] {d.metadata['title']}\n{d.page_content}" for d in docs)


def build_rag_chain(retriever: BaseRetriever, llm: BaseChatModel) -> Runnable:
    """pregunta (str) → respuesta (str) con citas [ID], en LCEL."""
    return (
        {"context": retriever | RunnableLambda(format_documents), "question": RunnablePassthrough()}
        | ANSWER_PROMPT
        | llm
        | StrOutputParser()
    )
