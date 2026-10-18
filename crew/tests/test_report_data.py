"""Partes deterministas del informe de reclamos (sin CrewAI ni LLM: corren en CI)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_data import (  # noqa: E402
    compute_stats,
    detect_signals,
    misattributed_policies,
    numbers_in,
    relevant_policy_ids,
    render_kpi_tables,
    strip_code_fence,
    synthetic_complaints,
    unverified_numbers,
)


def test_stats_are_consistent():
    complaints = synthetic_complaints()
    stats = compute_stats(complaints)
    assert stats["total"] == 60
    assert sum(s["reclamos"] for s in stats["por_categoria"].values()) == 60
    assert round(sum(s["porcentaje"] for s in stats["por_categoria"].values())) == 100
    assert "| Cobro indebido |" in render_kpi_tables(stats)


def test_planted_pattern_is_detected_by_code():
    signals = detect_signals(compute_stats(synthetic_complaints()))
    assert any(s.startswith("Transferencia fallida: 10 reclamos en la última semana") for s in signals)


def test_number_verification_handles_formats_and_catches_invented_figures():
    assert numbers_in("Bs 1.500,00 y 1,500.00 y 38,3%") == {"1500", "38.3"}
    data = "23 reclamos, 38.3% del total, monto 23,935.00"
    assert unverified_numbers("Son 23 reclamos (38,3%) por Bs 23.935", data) == []
    assert unverified_numbers("Representan el 78% del total", data) == ["78"]  # suma inventada por el LLM
    assert unverified_numbers("Ver POL-011 y QJ-S0001", data) == []  # los ids no son cifras


def test_policy_checks_and_fence():
    stats = compute_stats(synthetic_complaints())
    allowed = relevant_policy_ids(stats)
    assert "POL-012" in allowed and "POL-009" not in allowed
    assert misattributed_policies("Aplica POL-012; también POL-009", allowed) == ["POL-009"]
    assert strip_code_fence("```markdown\n## Resumen\ntexto\n```") == "## Resumen\ntexto"
    # Caso real (qwen2.5:3b): frase de relleno después del bloque
    assert strip_code_fence("```markdown\n## Resumen\ntexto\n```\n\nEste informe resume...") == "## Resumen\ntexto"
    assert strip_code_fence("## Resumen sin bloque") == "## Resumen sin bloque"


def test_contradicted_trend_is_flagged():
    from report_data import contradicted_trends

    stats = compute_stats(synthetic_complaints())
    # Caso real (qwen2.5:3b): cifras correctas, conclusión opuesta a los datos
    wrong = "En las últimas cuatro semanas hubo una disminución en los reclamos por transferencias rechazadas."
    assert contradicted_trends(wrong, stats) == ["Transferencia fallida"]
    right = "Las transferencias fallidas crecieron a 10 en la última semana. El cobro indebido bajó levemente."
    assert contradicted_trends(right, stats) == []  # cobro indebido no es una categoría en crecimiento


def test_structured_findings_get_numbers_from_code_and_checks_catch_misattribution():
    from report_data import check_findings, load_policies, render_findings

    stats = compute_stats(synthetic_complaints())
    good = [
        {"categoria": "transferencia", "descripcion": "Creció con fuerza en la última semana, sobre todo por la app.",
         "politica": "POL-012", "recomendacion": "Revisar la conciliación de transferencias.", "area_responsable": "Operaciones"},
        {"categoria": "fraude", "descripcion": "Todas las denuncias son de prioridad alta.",
         "politica": "POL-004", "recomendacion": "Reforzar el bloqueo inmediato.", "area_responsable": "Seguridad"},
    ]  # fmt: skip
    assert check_findings("Las transferencias fallidas crecieron.", good, stats) == []
    rendered = render_findings(good, stats, load_policies())
    assert "*14 reclamos (23.3%) · 5 de prioridad alta · últimas 4 semanas: 0 → 2 → 2 → 10*" in rendered

    # Casos reales de qwen2.5:3b que la verificación por conjunto de cifras NO detectaba
    bad = [
        {"categoria": "fraude", "descripcion": "Tendencia creciente desde 2 hasta 10 en las últimas semanas.",
         "politica": "POL-011", "recomendacion": "Atender antes.", "area_responsable": "Atención"},
        {"categoria": "cobro_indebido", "descripcion": "Los reclamos aumentaron.",
         "politica": "POL-012", "recomendacion": "Revisar.", "area_responsable": "Finanzas"},
    ]  # fmt: skip
    problems = check_findings("Hubo 60 reclamos.", bad, stats)
    assert any("Fraude" in p and "cifras" in p for p in problems)
    assert any("Fraude" in p and "crecimiento que los datos no muestran" in p for p in problems)
    assert any("POL-012 no aplica" in p for p in problems)
    assert any(p.startswith("Resumen") for p in problems)
    assert any("Falta un hallazgo" in p and "Transferencia" in p for p in problems)


def test_policy_field_is_normalized_to_id():
    from report_data import normalize_findings

    raw = [{"categoria": "transferencia", "politica": "POL-012 (Transferencias interbancarias)"}]
    assert normalize_findings(raw)[0]["politica"] == "POL-012"


def test_summary_growth_claims_are_checked_per_category():
    from report_data import check_findings

    stats = compute_stats(synthetic_complaints())
    # Caso real: "Cobro indebido con un crecimiento significativo" (5 → 4 → 6 → 8 no es crecimiento)
    real = (
        "Los patrones identificados son Cobro indebido con un crecimiento significativo en reclamos y "
        "Transferencia fallida con un aumento en la cantidad de reclamos."
    )
    problems = check_findings(real, [{"categoria": "transferencia", "descripcion": "Creció.", "politica": "POL-012",
                                      "recomendacion": "Revisar.", "area_responsable": "Ops"}], stats)  # fmt: skip
    assert problems == ["Resumen: afirma un crecimiento de Cobro indebido que los datos no muestran"]
