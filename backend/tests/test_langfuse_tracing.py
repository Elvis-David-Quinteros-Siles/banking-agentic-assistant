"""Trazas en Langfuse: sin PII hacia el servidor, no-op sin configuración y tolerantes a fallos."""

from types import SimpleNamespace

from app.observability.langfuse_tracing import TurnTrace, get_langfuse, mask_data, turn_trace


def test_mask_data_removes_pii_from_any_structure():
    data = {
        "message": "Mi tarjeta es 4111 1111 1111 1111 y mi correo maria.rojas@correo.com",
        "nested": [{"text": "llamame al 71234567"}, ("cuenta 1000452101",)],
        "n": 3,
        "state": {"customer_id": "CLI-001"},  # el estado del grafo viaja en las entradas de los nodos
    }
    masked = str(mask_data(data=data))
    for secret in ("4111 1111 1111 1111", "maria.rojas@correo.com", "71234567", "1000452101", "CLI-001"):
        assert secret not in masked
    assert "[CLIENTE " in masked  # seudónimo, igual al userId de la traza
    assert mask_data(data=3) == 3


def test_disabled_by_default_is_a_noop():
    get_langfuse.cache_clear()
    assert get_langfuse() is None
    with turn_trace("t1", "CLI-001", "chat") as trace:
        assert trace.callbacks == []
        assert trace.config({"configurable": {"thread_id": "t1"}}) == {"configurable": {"thread_id": "t1"}}
        trace.record(object())  # sin span: no hace nada


def test_record_never_breaks_the_turn():
    class BrokenSpan:
        def update(self, **_):
            raise RuntimeError("Langfuse caído")

    response = SimpleNamespace(answer="ok", status="completed", citations=[], intent="x", intent_confidence=1.0)
    TurnTrace(callbacks=["handler"], span=BrokenSpan()).record(response)  # no lanza


def test_record_sends_result_scores():
    calls = []

    class Span:
        def update(self, **kwargs):
            calls.append(("update", kwargs))

        def score_trace(self, **kwargs):
            calls.append((kwargs["name"], kwargs["value"]))

    response = SimpleNamespace(
        answer="Su saldo es Bs 12.450,30", status="completed", citations=[], intent="consulta_cuenta",
        intent_confidence=1.0, degraded=False, guardrail=SimpleNamespace(blocked=False),
        metrics=SimpleNamespace(latency_ms=42.0),
    )  # fmt: skip
    TurnTrace(callbacks=["handler"], span=Span()).record(response)
    scores = dict(c for c in calls if c[0] != "update")
    assert scores == {"intent": "consulta_cuenta", "status": "completed", "degraded": 0.0,
                      "guardrail_blocked": 0.0, "latency_ms": 42.0}  # fmt: skip


def test_mask_spans_covers_every_text_attribute():
    from langfuse.types import MaskOtelSpansParams, OtelSpanData

    from app.observability.langfuse_tracing import mask_spans

    # Caso real: la interrupción HITL llega como mensaje de estado con el borrador y el customer_id
    span = OtelSpanData(
        trace_id="0" * 32, span_id="1" * 16, parent_span_id=None, name="hitl_review",
        instrumentation_scope_name="langfuse", instrumentation_scope_version=None,
        attributes={
            "langfuse.observation.status_message": "GraphInterrupt({'customer_id': 'CLI-002', 'resumen': '...'})",
            "otro": "sin datos sensibles",
            "lista": ["tarjeta 4111 1111 1111 1111"],
            "n": 3,
        },
        resource_attributes={},
    )  # fmt: skip
    result = mask_spans(params=MaskOtelSpansParams(spans={"s": span}))
    patch = result.span_patches["s"].set_attributes
    assert "CLI-002" not in patch["langfuse.observation.status_message"]
    assert "4111 1111 1111 1111" not in patch["lista"][0]
    assert "otro" not in patch and "n" not in patch  # solo se tocan los que cambian
