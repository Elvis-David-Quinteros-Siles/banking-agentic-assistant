"""Datos y verificación del informe de reclamos (Python puro: sin CrewAI, testeable en CI).

Las cifras del informe las calcula este módulo; los agentes de CrewAI solo las interpretan y
redactan. Al final se verifica que toda cifra del texto generado exista en los datos.
"""

import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KNOWLEDGE_FILE = REPO / "data" / "seed" / "knowledge.json"

CATEGORY_LABELS = {
    "cobro_indebido": "Cobro indebido",
    "fraude": "Fraude / compra no reconocida",
    "transferencia": "Transferencia fallida",
    "atencion": "Atención",
    "producto": "Condiciones del producto",
    "otro": "Otro",
}
# Políticas que aplican a cada categoría (las consulta el especialista en políticas)
CATEGORY_POLICIES = {
    "cobro_indebido": ["POL-011", "POL-002", "POL-003"],
    "fraude": ["POL-004", "POL-005", "POL-011"],
    "transferencia": ["POL-012", "POL-011"],
    "atencion": ["POL-011"],
    "producto": ["POL-006", "POL-011"],
    "otro": ["POL-011"],
}

# Datos sintéticos (FICTICIOS) con un patrón plantado para que el análisis tenga algo que encontrar:
# las transferencias fallidas por banca por internet crecen en la última semana.
_SUMMARIES = {
    "cobro_indebido": ["Cliente reporta doble cobro en compra con {p}", "Descuento de mantenimiento no esperado en {p}",
                       "Cobro de comisión que el cliente no reconoce en {p}"],
    "fraude": ["Compras no reconocidas en {p} tras extravío", "Cliente indica clonación de {p}"],
    "transferencia": ["Transferencia debitada que no llegó al destino desde {p}",
                      "Transferencia interbancaria rechazada pero debitada en {p}"],
    "atencion": ["Demora en la atención en agencia", "Cliente insatisfecho con la atención telefónica"],
    "producto": ["Cliente no estaba informado del pago mínimo de {p}"],
    "otro": ["Consulta derivada como reclamo sin categoría clara"],
}  # fmt: skip
_PRODUCTS = {
    "cobro_indebido": ["tarjeta_debito", "caja_ahorro", "tarjeta_credito"],
    "fraude": ["tarjeta_debito", "tarjeta_credito"],
    "transferencia": ["banca_internet", "caja_ahorro"],
    "atencion": [None],
    "producto": ["tarjeta_credito"],
    "otro": [None],
}


def synthetic_complaints(n: int = 60, seed: int = 7, today: datetime | None = None) -> list[dict]:
    rng = random.Random(seed)  # noqa: S311 - datos sintéticos reproducibles
    today = today or datetime(2026, 10, 18)
    weights = {"cobro_indebido": 38, "fraude": 14, "transferencia": 22, "atencion": 12, "producto": 9, "otro": 5}
    rows = []
    for i in range(n):
        category = rng.choices(list(weights), weights=list(weights.values()))[0]
        days_ago = rng.randint(0, 27)
        product = rng.choice(_PRODUCTS[category])
        if category == "transferencia" and rng.random() < 0.6:
            days_ago, product = rng.randint(0, 6), "banca_internet"  # patrón plantado
        amount = None if category in ("atencion", "otro") else round(rng.choice([45, 120, 350, 780, 1500, 3200]), 2)
        rows.append(
            {
                "complaint_id": f"QJ-S{i:04d}",
                "category": category,
                "product": product,
                "amount": amount,
                "currency": "BOB",
                "priority": "alta" if category == "fraude" or (amount or 0) >= 1500 else rng.choice(["media", "baja"]),
                "status": "rechazada" if rng.random() < 0.1 else "registrada",
                "summary": rng.choice(_SUMMARIES[category]).format(p=(product or "").replace("_", " ")),
                "created_at": (today - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            }
        )
    return rows


def load_complaints_sqlite(db_path: Path) -> list[dict]:
    """Reclamos registrados por el sistema (sin customer_id: el informe no necesita identidad)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT complaint_id, category, product, amount, currency, priority, status, summary, "
            "substr(created_at, 1, 10) AS created_at FROM complaints"
        ).fetchall()
    return [dict(r) for r in rows]


def compute_stats(complaints: list[dict], today: datetime | None = None) -> dict:
    today = today or datetime(2026, 10, 18)
    total = len(complaints)
    by_category: dict[str, dict] = {}
    for category in CATEGORY_LABELS:
        group = [c for c in complaints if c["category"] == category]
        if not group:
            continue
        amounts = [float(c["amount"]) for c in group if c.get("amount") is not None and c.get("currency") == "BOB"]
        by_category[category] = {
            "reclamos": len(group),
            "porcentaje": round(100 * len(group) / total, 1),
            "monto_bob": round(sum(amounts), 2),
            "alta_prioridad": sum(c["priority"] == "alta" for c in group),
        }
    weekly: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # semanas: hace 3, 2, 1, actual
    for c in complaints:
        days = (today - datetime.strptime(c["created_at"], "%Y-%m-%d")).days
        if 0 <= days < 28:
            weekly[c["category"]][3 - days // 7] += 1
    products = Counter(c["product"] for c in complaints if c.get("product"))
    return {
        "total": total,
        "alta_prioridad": sum(c["priority"] == "alta" for c in complaints),
        "rechazados": sum(c["status"] == "rechazada" for c in complaints),
        "por_categoria": by_category,
        "tendencia_semanal": dict(weekly),
        "productos_top": products.most_common(5),
    }


def render_kpi_tables(stats: dict) -> str:
    lines = [
        f"**{stats['total']} reclamos** · {stats['alta_prioridad']} de prioridad alta · {stats['rechazados']} rechazados por el operador",
        "",
        "| Categoría | Reclamos | % | Monto reclamado (Bs) | Prioridad alta | Últimas 4 semanas |",
        "|---|---|---|---|---|---|",
    ]
    for category, s in sorted(stats["por_categoria"].items(), key=lambda kv: -kv[1]["reclamos"]):
        trend = " → ".join(str(n) for n in stats["tendencia_semanal"].get(category, [0, 0, 0, 0]))
        lines.append(
            f"| {CATEGORY_LABELS[category]} | {s['reclamos']} | {s['porcentaje']}% | {s['monto_bob']:,.2f} | "
            f"{s['alta_prioridad']} | {trend} |"
        )
    lines += ["", "Productos con más reclamos: " + ", ".join(f"{p} ({n})" for p, n in stats["productos_top"])]
    return "\n".join(lines)


def growing_categories(stats: dict) -> list[str]:
    """Categorías cuya última semana duplica (o más) el promedio de las 3 anteriores."""
    return [
        category
        for category, weeks in stats["tendencia_semanal"].items()
        if weeks[3] >= 4 and weeks[3] >= 2 * max(sum(weeks[:3]) / 3, 1)
    ]


# Palabra que identifica cada categoría en el texto y verbos de baja (para detectar contradicciones)
CATEGORY_KEYWORDS = {
    "cobro_indebido": "cobro",
    "fraude": "fraude",
    "transferencia": "transferencia",
    "atencion": "atención",
    "producto": "condiciones",
    "otro": "otro",
}
DECREASE_RE = re.compile(r"\b(disminu\w*|reduj\w*|reduc\w*|baj[óo]|cay[óo]|descen\w*|menos reclamos)\b", re.IGNORECASE)


def contradicted_trends(narrative: str, stats: dict) -> list[str]:
    """Categorías que CRECIERON según los datos pero que el texto describe como en baja (misma oración).

    La verificación de cifras no detecta esto: el texto puede usar solo cifras correctas y aun así
    afirmar lo contrario de lo que muestran (caso real con qwen2.5:3b).
    """
    sentences = re.split(r"(?<=[.!?])\s+|\n", narrative)
    flagged = []
    for category in growing_categories(stats):
        keyword = CATEGORY_KEYWORDS[category]
        if any(keyword in s.lower() and DECREASE_RE.search(s) for s in sentences):
            flagged.append(CATEGORY_LABELS[category])
    return flagged


def detect_signals(stats: dict) -> list[str]:
    """Señales calculadas por código (el LLM las explica, no las descubre): crecimiento en la última
    semana frente al promedio de las 3 anteriores, y categorías con mayoría de prioridad alta."""
    signals = []
    for category, weeks in stats["tendencia_semanal"].items():
        if category in growing_categories(stats):
            signals.append(
                f"{CATEGORY_LABELS[category]}: {weeks[3]} reclamos en la última semana frente a "
                f"{' / '.join(str(w) for w in weeks[:3])} en las 3 anteriores"
            )
    for category, s in stats["por_categoria"].items():
        if s["reclamos"] >= 5 and s["alta_prioridad"] / s["reclamos"] >= 0.5:
            signals.append(f"{CATEGORY_LABELS[category]}: {s['alta_prioridad']} de {s['reclamos']} con prioridad alta")
    return signals


def policy_map_text(stats: dict, policies: dict[str, dict]) -> str:
    """Qué políticas aplican a cada categoría presente (mapeo fijo, no lo decide el LLM)."""
    lines = []
    for category in stats["por_categoria"]:
        ids = CATEGORY_POLICIES[category]
        lines.append(f"- {CATEGORY_LABELS[category]}: " + ", ".join(f"{i} ({policies[i]['titulo']})" for i in ids))
    return "\n".join(lines)


def strip_code_fence(text: str) -> str:
    """Los modelos chicos suelen envolver el Markdown en ```markdown ... ``` y agregar frases de relleno
    antes o después: si hay un bloque con el informe, se usa solo su contenido."""
    match = re.search(r"```(?:markdown|md)?[ \t]*\n(.*?)\n[ \t]*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match and "## " in match.group(1) else text.strip()


def misattributed_policies(narrative: str, allowed: list[str]) -> list[str]:
    """Ids de política citados que no corresponden a ninguna categoría del informe."""
    cited = dict.fromkeys(re.findall(r"POL-\d{3}", narrative))
    return [p for p in cited if p not in allowed]


def load_policies() -> dict[str, dict]:
    data = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    return {p["politica_id"]: {"titulo": p["titulo"], "texto": p["texto"]} for p in data["policies"]}


def relevant_policy_ids(stats: dict) -> list[str]:
    ids: list[str] = []
    for category in stats["por_categoria"]:
        ids += [p for p in CATEGORY_POLICIES[category] if p not in ids]
    return ids


# ---------------------------------------------------------------------------
# Verificación de cifras (misma idea que el output_guardrail del chat)
# ---------------------------------------------------------------------------
NUMBER_RE = re.compile(r"\d[\d.,]*")
POLICY_ID_RE = re.compile(r"(?:POL|PRD|QJ)-[A-Z0-9-]+")


def _canonical(raw: str) -> str | None:
    """ "1.500,00", "1,500.00", "1500" y "1500.0" -> "1500"; "38,3" y "38.3" -> "38.3"."""
    raw = raw.rstrip(".,")
    if not raw:
        return None
    if "," in raw and "." in raw:  # el último separador es el decimal
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif raw.count(",") + raw.count(".") > 1 or re.fullmatch(r"\d{1,3}[.,]\d{3}", raw):
        raw = raw.replace(",", "").replace(".", "")  # solo separadores de miles
    else:
        raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return f"{value:.2f}".rstrip("0").rstrip(".")


def numbers_in(text: str) -> set[str]:
    cleaned = POLICY_ID_RE.sub(" ", text)
    return {c for c in (_canonical(m) for m in NUMBER_RE.findall(cleaned)) if c is not None}


def unverified_numbers(narrative: str, *sources: str) -> list[str]:
    """Cifras del texto generado que no aparecen en ninguna fuente (datos o políticas)."""
    allowed = set().union(*(numbers_in(s) for s in sources)) | {str(n) for n in range(0, 11)}
    return sorted(numbers_in(narrative) - allowed, key=lambda x: float(x))


# ---------------------------------------------------------------------------
# Informe a partir de hallazgos ESTRUCTURADOS (salida del redactor con output_pydantic)
# ---------------------------------------------------------------------------
GROWTH_RE = re.compile(r"\b(crec\w*|aument\w*|increment\w*|subi\w*|al alza)\b", re.IGNORECASE)
DIGIT_RE = re.compile(r"\d")


def render_findings(findings: list[dict], stats: dict, policies: dict[str, dict]) -> str:
    """Hallazgos con las cifras insertadas por código desde las estadísticas de SU categoría:
    el modelo describe con palabras; una cifra nunca puede quedar asociada a la categoría equivocada."""
    lines = []
    for f in findings:
        s = stats["por_categoria"].get(f["categoria"])
        if s is None:
            continue
        trend = " → ".join(str(n) for n in stats["tendencia_semanal"].get(f["categoria"], [0, 0, 0, 0]))
        policy = policies.get(f["politica"], {}).get("titulo", "política no encontrada")
        lines += [
            f"### {CATEGORY_LABELS[f['categoria']]}",
            f"*{s['reclamos']} reclamos ({s['porcentaje']}%) · {s['alta_prioridad']} de prioridad alta · "
            f"últimas 4 semanas: {trend}*",
            "",
            f["descripcion"],
            "",
            f"- Política: **{f['politica']}** ({policy})",
            f"- Recomendación: {f['recomendacion']} — *Responsable: {f['area_responsable']}*",
            "",
        ]
    return "\n".join(lines).strip()


def check_findings(summary: str, findings: list[dict], stats: dict) -> list[str]:
    """Problemas de los hallazgos estructurados (lista vacía = todo verificado)."""
    problems = []
    growing = set(growing_categories(stats))
    for f in findings:
        label = CATEGORY_LABELS.get(f["categoria"], f["categoria"])
        if f["categoria"] not in stats["por_categoria"]:
            problems.append(f"{label}: categoría sin reclamos en el período")
            continue
        if f["politica"] not in CATEGORY_POLICIES[f["categoria"]]:
            problems.append(f"{label}: la política {f['politica']} no aplica a esta categoría")
        text = f"{f['descripcion']} {f['recomendacion']}"
        if DIGIT_RE.search(text):
            problems.append(
                f"{label}: el texto del modelo incluye cifras (no verificadas; las cifras van en la cabecera)"
            )
        if f["categoria"] in growing and DECREASE_RE.search(text):
            problems.append(f"{label}: el texto la describe en baja, pero CRECIÓ")
        if f["categoria"] not in growing and GROWTH_RE.search(text):
            problems.append(f"{label}: el texto afirma un crecimiento que los datos no muestran")
    # En el resumen se mezclan categorías: se revisa oración por oración
    for sentence in re.split(r"(?<=[.!?])\s+|\n", summary):
        for category, keyword in CATEGORY_KEYWORDS.items():
            if keyword not in sentence.lower() or category not in stats["por_categoria"]:
                continue
            if category in growing and DECREASE_RE.search(sentence):
                problems.append(f"Resumen: describe en baja {CATEGORY_LABELS[category]}, que CRECIÓ")
            # "Cobro indebido con un crecimiento…" — solo si la palabra de crecimiento está cerca de la categoría
            near = re.search(rf"{keyword}\w*\W+(?:\w+\W+){{0,6}}?{GROWTH_RE.pattern}", sentence, re.IGNORECASE)
            if category not in growing and near:
                problems.append(
                    f"Resumen: afirma un crecimiento de {CATEGORY_LABELS[category]} que los datos no muestran"
                )
    if DIGIT_RE.search(summary):
        problems.append("Resumen: incluye cifras escritas por el modelo (no verificadas)")
    missing = [CATEGORY_LABELS[c] for c in growing if c not in {f["categoria"] for f in findings}]
    if missing:
        problems.append(f"Falta un hallazgo para categorías en crecimiento: {', '.join(missing)}")
    return problems


def normalize_findings(findings: list[dict]) -> list[dict]:
    """El modelo suele escribir "POL-011 (Plazo de atención…)" en lugar del id: se conserva solo el id."""
    normalized = []
    for f in findings:
        match = re.search(r"POL-\d{3}", f.get("politica", ""))
        normalized.append({**f, "politica": match.group(0) if match else f.get("politica", "")})
    return normalized
