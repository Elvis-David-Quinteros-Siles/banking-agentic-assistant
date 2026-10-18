"""Informe ejecutivo semanal de reclamos con CrewAI (batch, fuera del camino del cliente).

Tres agentes en proceso secuencial, con un LLM on-premise (Ollama):
  1. Analista de calidad   → patrones y causas probables a partir de las cifras y los resúmenes
  2. Especialista en normativa interna → contrasta cada patrón con la política aplicable
     (herramienta consultar_politica)
  3. Redactor ejecutivo    → hallazgos ESTRUCTURADOS (output_pydantic): categoría, descripción
     con palabras, política, recomendación y responsable

Las cifras las calcula report_data.py y el código las inserta junto a cada hallazgo según su
categoría: el modelo no escribe cifras, así que no puede atribuir una cifra a la categoría
equivocada. Al final se verifican políticas, tendencias y cifras escritas por el modelo.

Uso (entorno aparte: crew/requirements.txt):
    python crew/complaints_crew.py                       # datos sintéticos (60 reclamos ficticios)
    python crew/complaints_crew.py --source sqlite       # reclamos registrados por el sistema
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_data import (  # noqa: E402
    REPO,
    check_findings,
    compute_stats,
    contradicted_trends,
    detect_signals,
    load_complaints_sqlite,
    load_policies,
    misattributed_policies,
    normalize_findings,
    policy_map_text,
    relevant_policy_ids,
    render_findings,
    render_kpi_tables,
    strip_code_fence,
    synthetic_complaints,
    unverified_numbers,
)

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
POLICIES = load_policies()


class PolicyInput(BaseModel):
    policy_id: str = Field(description="Id de la política, por ejemplo POL-011")


class PolicyLookupTool(BaseTool):
    name: str = "consultar_politica"
    description: str = "Devuelve el texto de una política interna del banco a partir de su id (POL-001 ... POL-014)."
    args_schema: type[BaseModel] = PolicyInput

    def _run(self, policy_id: str) -> str:
        policy = POLICIES.get(policy_id.strip().upper())
        return (
            f"{policy_id}: {policy['titulo']}. {policy['texto']}" if policy else f"No existe la política {policy_id}."
        )


class Finding(BaseModel):
    categoria: Literal["cobro_indebido", "fraude", "transferencia", "atencion", "producto", "otro"]
    descripcion: str = Field(description="Qué pasa y causa probable, con palabras y SIN cifras")
    politica: str = Field(description="Id de la política aplicable, por ejemplo POL-012")
    recomendacion: str = Field(description="Acción concreta, sin cifras")
    area_responsable: str


class ExecutiveReport(BaseModel):
    resumen: str = Field(description="3 a 4 frases para gerencia, sin cifras")
    hallazgos: list[Finding] = Field(description="Un hallazgo por patrón (máximo 3)")


def build_crew(llm: LLM, kpis: str, samples: str, signals: list[str], policy_map: str) -> Crew:
    limits = {"llm": llm, "allow_delegation": False, "verbose": False, "max_iter": 4, "max_execution_time": 180}
    analyst = Agent(
        role="Analista de calidad de reclamos",
        goal="Encontrar los patrones que más afectan a los clientes y su causa probable",
        backstory="Analista del área de experiencia del cliente de un banco boliviano. Se basa solo en los datos.",
        **limits,
    )
    specialist = Agent(
        role="Especialista en normativa interna",
        goal="Relacionar cada patrón con la política interna que aplica y señalar brechas",
        backstory="Conoce las políticas del banco y las cita por su id.",
        tools=[PolicyLookupTool()],
        **limits,
    )
    writer = Agent(
        role="Redactor ejecutivo",
        goal="Escribir hallazgos claros y accionables para la gerencia",
        backstory="Redacta informes breves para el comité de gerencia. Las cifras las pone el sistema, no él.",
        **limits,
    )

    analysis = Task(
        description=(
            "Estas son las cifras de reclamos de las últimas 4 semanas (calculadas por el sistema, son exactas):\n"
            f"{kpis}\n\nSeñales detectadas por el sistema (inclúyelas):\n"
            + "\n".join(f"- {s}" for s in signals)
            + f"\n\nEjemplos de resúmenes de reclamos:\n{samples}\n\n"
            "Identifica los 3 patrones más relevantes (volumen, crecimiento reciente o prioridad alta) "
            "y la causa probable de cada uno. Copia las cifras tal como aparecen arriba; no sumes ni calcules."
        ),
        expected_output="Lista de 3 patrones, cada uno con: qué pasa, cifras que lo muestran y causa probable.",
        agent=analyst,
    )
    policy_check = Task(
        description=(
            "Para cada patrón del análisis, consulta con la herramienta consultar_politica la política que aplica "
            f"según esta tabla (usa SOLO estas):\n{policy_map}\nIndica qué establece y si hay una brecha "
            "(por ejemplo, plazos que no se cumplen o información que el cliente no recibió). Cita el id."
        ),
        expected_output="Para cada patrón: política aplicable con su id, qué establece y la brecha detectada.",
        agent=specialist,
        context=[analysis],
    )
    summary = Task(
        description=(
            "Redacta el informe para gerencia en español como hallazgos estructurados: un resumen de 3-4 frases "
            "y un hallazgo por patrón (máximo 3) con su categoría, descripción, política (id), recomendación y "
            "área responsable. NO escribas cifras: el sistema agrega las cifras de cada categoría junto al "
            'hallazgo. Describe con palabras (por ejemplo, "creció con fuerza en la última semana").'
        ),
        expected_output="Resumen y hallazgos estructurados según el esquema.",
        agent=writer,
        context=[analysis, policy_check],
        output_pydantic=ExecutiveReport,
    )
    return Crew(
        agents=[analyst, specialist, writer], tasks=[analysis, policy_check, summary], process=Process.sequential
    )


def free_text_checks(narrative: str, stats: dict, kpis: str, policy_ids: list[str]) -> list[str]:
    """Respaldo si el modelo no devolvió el esquema: verificaciones sobre texto libre (más débiles)."""
    policy_texts = " ".join(f"{p['titulo']} {p['texto']}" for p in POLICIES.values())
    problems = [
        f"cifra no presente en los datos: {n}"
        for n in unverified_numbers(narrative, kpis, json.dumps(stats), policy_texts)
    ]
    problems += [
        f"la política {p} no aplica a ninguna categoría" for p in misattributed_policies(narrative, policy_ids)
    ]
    problems += [f"{c}: el texto la describe en baja, pero CRECIÓ" for c in contradicted_trends(narrative, stats)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=["synthetic", "sqlite"], default="synthetic")
    parser.add_argument("--db", default=str(REPO / "backend" / "data" / "bank.db"))
    parser.add_argument("--model", default=os.environ.get("CREW_MODEL", "ollama/qwen2.5:3b"))
    parser.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--out", default=str(REPORTS_DIR / "complaints_report.md"))
    args = parser.parse_args()

    complaints = synthetic_complaints() if args.source == "synthetic" else load_complaints_sqlite(Path(args.db))
    if not complaints:
        print("No hay reclamos para analizar.")
        return 1
    stats = compute_stats(complaints)
    kpis = render_kpi_tables(stats)
    samples = "\n".join(f"- [{c['category']}] {c['summary']}" for c in complaints[:12])
    policy_ids = relevant_policy_ids(stats)
    signals = detect_signals(stats)

    # max_tokens: un modelo chico puede entrar en una generación sin fin (visto: 11.800 tokens en una respuesta)
    llm = LLM(model=args.model, base_url=args.base_url, temperature=0.2, max_tokens=900)
    started = time.perf_counter()
    result = build_crew(llm, kpis, samples, signals, policy_map_text(stats, POLICIES)).kickoff()
    seconds = time.perf_counter() - started
    usage = getattr(result, "token_usage", None)

    structured = result.pydantic if isinstance(result.pydantic, ExecutiveReport) else None
    if structured is not None:
        findings = normalize_findings([f.model_dump() for f in structured.hallazgos])
        body = f"## Resumen ejecutivo\n\n{structured.resumen}\n\n## Hallazgos\n\n{render_findings(findings, stats, POLICIES)}"
        problems = check_findings(structured.resumen, findings, stats)
        mode = "hallazgos estructurados"
    else:
        body = strip_code_fence(str(result.raw))
        problems = free_text_checks(body, stats, kpis, policy_ids)
        mode = "texto libre (el modelo no devolvió el esquema)"

    verification = "\n".join(f"- ⚠️ {p}" for p in problems) or "- ✅ Políticas, tendencias y cifras verificadas."
    report = "\n".join(
        [
            "# Informe semanal de reclamos",
            "",
            f"Generado el {datetime.now():%Y-%m-%d %H:%M} · fuente: {args.source} · modelo: `{args.model}` · "
            f"{seconds:.0f} s" + (f" · {usage.total_tokens} tokens" if usage else "") + f" · {mode}",
            "",
            "## Cifras (calculadas por el sistema)",
            "",
            kpis,
            "",
            "Señales: " + ("; ".join(signals) if signals else "ninguna"),
            "",
            body,
            "",
            "## Verificación automática (revisión humana antes de enviar)",
            "",
            verification,
            "",
        ]
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nInforme guardado en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
