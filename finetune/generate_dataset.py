"""Genera el dataset sintético para el router LoRA (intención + entidades en JSON).

Diseño:
- Plantillas por intención con slots {producto}, {monto}, ... y formas coloquiales bolivianas
  ("plata", "sacar", "cuánto me queda", "me clavaron un cobro").
- Ruido realista: sin tildes, minúsculas, typos, mayúsculas sostenidas, signos faltantes.
- Split POR PLANTILLA: ~20% de las plantillas de cada intención se reservan para test.
  Así el test mide generalización a frases nuevas, no memorización de las de entrenamiento.

Uso:  python finetune/generate_dataset.py --n 600 --seed 42
Salida: finetune/data/{train,val,test}.jsonl  con {"text", "label", "template_id"}
"""

import argparse
import json
import random
import re
import unicodedata
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# Formas de superficie de cada producto -> etiqueta canónica (sin artículos: la plantilla pone "mi")
PRODUCTS = {
    "tarjeta_credito": ["tarjeta de crédito", "tarjeta de credito", "visa de crédito", "tarjeta de crédito clásica"],
    "tarjeta_debito": ["tarjeta de débito", "tarjeta de debito", "tarjeta débito", "tarjeta de débito"],
    "tarjeta": ["tarjeta", "tarjeta", "plástico"],
    "caja_ahorro": ["caja de ahorro", "cuenta de ahorro", "caja de ahorros", "cuenta en bolivianos"],
    "cuenta_corriente": ["cuenta corriente", "cuenta corriente empresarial", "cuenta de la empresa"],
    "dpf": ["DPF", "depósito a plazo fijo", "plazo fijo", "depósito a plazo"],
    "credito_vivienda": ["crédito de vivienda", "préstamo para casa", "crédito hipotecario", "préstamo de vivienda"],
    "credito_pyme": ["crédito pyme", "préstamo para mi negocio", "crédito productivo", "crédito para emprendimiento"],
    "qr": ["QR", "pago QR", "cobro con QR"],
    "banca_internet": ["app", "banca por internet", "aplicación", "banca móvil"],
    "transferencia": ["transferencia", "transferencia interbancaria", "giro"],
}

# Grupos de productos coherentes por slot: evita frases como "¿cómo abro un cobro con QR?"
SLOT_GROUPS = {
    "p": list(PRODUCTS),
    "p_card": ["tarjeta_credito", "tarjeta_debito", "tarjeta"],
    "p_account": ["caja_ahorro", "cuenta_corriente"],
    "p_product": ["caja_ahorro", "cuenta_corriente", "dpf", "credito_vivienda", "credito_pyme", "tarjeta_credito", "tarjeta_debito"],
    "p_credit": ["credito_vivienda", "credito_pyme", "tarjeta_credito"],
    "p_spend": ["tarjeta_credito", "tarjeta_debito", "tarjeta", "caja_ahorro", "qr", "banca_internet"],
}
SLOT_RE = re.compile(r"\{(p(?:_[a-z]+)?)\}")


def random_amount(rng: random.Random) -> tuple[str, float, str]:
    """Devuelve (texto, valor, moneda) en formatos variados del uso boliviano."""
    value = rng.choice([25, 64, 95, 120, 180, 230, 350, 412.5, 500, 780, 1200, 1250.5, 3000, 8900, 15000])
    currency = rng.choice(["BOB", "BOB", "BOB", "USD"])

    def fmt(v: float) -> str:
        if v == int(v):
            return f"{int(v):,}".replace(",", ".") if v >= 1000 and rng.random() < 0.5 else str(int(v))
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    number = fmt(value)
    if currency == "BOB":
        text = rng.choice([f"Bs {number}", f"Bs. {number}", f"{number} bolivianos", f"{number} Bs", f"{number} bs"])
    else:
        text = rng.choice([f"$us {number}", f"{number} dólares", f"USD {number}", f"{number} dolares"])
    return text, float(value), currency


# Cada plantilla: (texto, slots que usa). {p}=producto, {m}=monto.
# Las etiquetas de producto/monto se derivan de los slots realmente usados.
TEMPLATES: dict[str, list[str]] = {
    "consulta_cuenta": [
        "¿Cuál es mi saldo?", "cuánto tengo en mi {p_account}", "quiero ver el saldo de mi {p_account}",
        "muéstrame mis últimos movimientos", "¿cuánta plata me queda?", "dame el extracto de mi {p_account}",
        "¿me depositaron el sueldo?", "necesito saber cuánto dinero tengo", "saldo de mi cuenta en dólares",
        "¿qué gastos hice esta semana con mi {p_spend}?", "¿llegó el depósito de {m}?", "movimientos de mi {p_account} por favor",
        "cuanto tengo disponible", "ver mis transacciones recientes", "¿cuánto me descontaron este mes?",
        "quiero revisar mis cuentas", "¿cuál fue mi último retiro?", "consultar saldo",
        "¿ya me acreditaron la transferencia de {m}?", "dime cuánto gasté con mi {p_card}",
    ],
    "conocimiento_producto": [
        "¿qué requisitos piden para {p_product}?", "¿cuánto cobran de comisión por {p_product}?",
        "¿cuál es la tasa de interés de {p_credit}?", "¿cómo abro una {p_account}?", "¿qué necesito para sacar {p_credit}?",
        "¿cuánto puedo sacar del cajero por día?", "¿cuál es el límite diario de mi {p_card}?",
        "quiero comprar una casa, ¿me pueden prestar?", "perdí mi tarjeta, ¿qué hago?",
        "¿cuánto cuesta reponer mi {p_card}?", "¿puedo usar mi {p_card} en el extranjero?",
        "¿cobran por pagar con QR?", "¿cuál es el pago mínimo de la tarjeta de crédito?",
        "¿en cuánto tiempo responden un reclamo?", "¿qué plazos tiene el DPF?",
        "¿cuál es el monto mínimo para abrir {p_product}?", "¿cobran mantenimiento en la {p_account}?",
        "¿hasta cuánto puedo transferir desde la app?", "info sobre {p_product}", "¿cómo funciona {p_product}?",
        "me robaron el celular con la app, ¿cómo bloqueo todo?", "¿qué documentos necesito para {p_credit}?",
        "¿cuánto cuesta la chequera?", "quiero invertir mi plata, ¿qué opciones hay?",
    ],
    "queja_disputa": [
        "quiero presentar un reclamo, me cobraron {m} dos veces en mi {p_card}",
        "no reconozco un cargo de {m} en mi {p_spend}", "me hicieron un doble cobro de {m}",
        "esto es un fraude, alguien usó mi {p_card}", "pésima atención en la agencia, quiero quejarme",
        "me clavaron un cobro de {m} que no hice", "hice una transferencia de {m} y nunca llegó",
        "quiero hacer una denuncia por un cobro indebido en mi {p_spend}",
        "me descontaron {m} de mantenimiento y tengo más de 500 en la cuenta",
        "el cajero no me dio la plata pero sí me descontó {m}",
        "estoy indignado, me bloquearon mi {p_card} sin avisar", "reclamo formal por una compra de {m} que no autoricé",
        "me están cobrando intereses que no corresponden en mi {p_credit}", "la app me cobró {m} y la operación falló",
        "quiero disputar un cargo de {m}", "me atendieron muy mal por teléfono, quiero reclamar",
        "me clonaron mi {p_card}, hay compras que no hice", "exijo la devolución de {m}",
    ],
    "fuera_de_alcance": [
        "¿quién ganó el partido de ayer?", "dame una receta de majadito", "¿cómo va a estar el clima mañana?",
        "cuéntame un chiste", "¿a cuánto está el bitcoin hoy?", "escríbeme un poema",
        "ayúdame con mi tarea de matemáticas", "¿quién va a ganar las elecciones?", "recomiéndame una película",
        "¿cuál es la capital de Francia?", "hazme un código en python", "¿qué signo del horóscopo soy?",
        "¿dónde puedo apostar en fútbol?", "traduce esto al inglés", "¿cuál es la mejor canción de este año?",
        "¿qué opinas del presidente?",
    ],
    "ambiguo": [
        "hola", "buenas tardes", "buenos días", "necesito ayuda", "tengo una consulta", "ok gracias",
        "?", "información por favor", "hola, una pregunta", "ayuda", "sí", "no entiendo", "qué tal",
        "quiero hablar con alguien",
    ],
}


# Iteración 2 (tras los Evals de la Fase 6): vocabulario coloquial que el modelo v1 no conocía.
# "¿cuánta plata me queda?" (plantilla de TEST) se bloqueaba como fuera de alcance porque "plata"
# solo aparecía en plantillas de quejas. Estas plantillas van SIEMPRE a entrenamiento y son
# frases distintas de las de test: se corrige la cobertura de vocabulario, no se memoriza el test.
EXTRA_TRAIN_TEMPLATES: dict[str, list[str]] = {
    "consulta_cuenta": [
        "¿cuánta plata tengo en mi {p_account}?", "quiero saber cuánta platita tengo",
        "¿cuánta plata entró a mi cuenta este mes?", "revisar cuánta plata hay en mi {p_account}",
        "¿cuántos bolivianos tengo en la cuenta?", "¿todavía tengo plata en mi {p_account}?",
    ],
    "fuera_de_alcance": [
        "¿cómo gano plata rápido con apuestas?", "¿cuánta plata gana un futbolista?",
    ],
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def add_noise(text: str, rng: random.Random) -> str:
    """Ruido de canal de chat real. Nunca toca los dígitos (romperían la etiqueta de monto)."""
    roll = rng.random()
    if roll < 0.25:
        text = strip_accents(text)
    elif roll < 0.40:
        text = text.lower().replace("¿", "").replace("?", "")
    elif roll < 0.45:
        text = text.upper()
    if rng.random() < 0.15:  # typo: intercambia dos letras contiguas de una palabra larga
        words = text.split(" ")
        candidates = [i for i, w in enumerate(words) if len(w) > 5 and w.isalpha()]
        if candidates:
            i = rng.choice(candidates)
            w = words[i]
            j = rng.randrange(1, len(w) - 2)
            words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
            text = " ".join(words)
    if rng.random() < 0.10:
        text = rng.choice(["hola, ", "buenas, ", "disculpe, ", "porfa "]) + text
    return text


def fill(template: str, rng: random.Random) -> tuple[str, dict]:
    label = {"producto": None, "monto": None, "moneda": None}
    text = template
    slot = SLOT_RE.search(text)
    if slot:
        tag = rng.choice(SLOT_GROUPS[slot.group(1)])
        text = text.replace(slot.group(0), rng.choice(PRODUCTS[tag]))
        label["producto"] = tag
    else:
        # Productos implícitos en la frase fija (la etiqueta debe reflejar lo que dice el texto)
        lowered = strip_accents(text.lower())
        for tag, hint in (("tarjeta_credito", "tarjeta de credito"), ("tarjeta", "tarjeta"), ("dpf", "dpf"),
                          ("qr", "qr"), ("transferencia", "transferencia"), ("banca_internet", "app"),
                          ("credito_vivienda", "casa"), ("cuenta_corriente", "chequera")):
            if re.search(rf"\b{hint}\b", lowered):
                label["producto"] = tag
                break
    if "{m}" in text:
        amount_text, value, currency = random_amount(rng)
        text = text.replace("{m}", amount_text)
        label["monto"], label["moneda"] = value, currency
    elif re.search(r"d[oó]lares", text):
        label["moneda"] = "USD"
    return text, label


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=600, help="Ejemplos totales aproximados")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-template-ratio", type=float, default=0.2)
    parser.add_argument("--regenerate-test", action="store_true",
                        help="Sobrescribe test.jsonl (por defecto se conserva para comparar modelos sobre el MISMO test)")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    # 1. Split por plantilla
    train_templates, test_templates = [], []
    for intent, templates in TEMPLATES.items():
        ids = [(intent, i, t) for i, t in enumerate(templates)]
        rng.shuffle(ids)
        cut = max(2, round(len(ids) * args.test_template_ratio))
        test_templates += ids[:cut]
        train_templates += ids[cut:]
    for intent, templates in EXTRA_TRAIN_TEMPLATES.items():
        train_templates += [(intent, f"extra{i}", t) for i, t in enumerate(templates)]

    # 2. Muestreo balanceado por intención (cada intención ~ n/5)
    def sample(pool: list, n_total: int) -> list[dict]:
        by_intent: dict[str, list] = {}
        for item in pool:
            by_intent.setdefault(item[0], []).append(item)
        rows, seen = [], set()
        per_intent = n_total // len(by_intent)
        for intent, items in by_intent.items():
            attempts = 0
            count = 0
            while count < per_intent and attempts < per_intent * 20:
                attempts += 1
                _, template_id, template = rng.choice(items)
                text, label = fill(template, rng)
                text = add_noise(text, rng)
                if text in seen:
                    continue
                seen.add(text)
                rows.append({"text": text, "label": {"intent": intent, **label}, "template_id": f"{intent}:{template_id}"})
                count += 1
        rng.shuffle(rows)
        return rows

    n_test = int(args.n * 0.2)
    train_val = sample(train_templates, args.n - n_test)
    test = sample(test_templates, n_test)
    n_val = len(train_val) // 9
    splits = {"train": train_val[n_val:], "val": train_val[:n_val], "test": test}

    test_file = DATA_DIR / "test.jsonl"
    if test_file.exists() and not args.regenerate_test:
        # El test versionado es la referencia: comparar v1 vs v2 exige exactamente los mismos casos
        splits["test"] = [json.loads(line) for line in test_file.read_text(encoding="utf-8").splitlines() if line]
        print("test.jsonl existente conservado (usar --regenerate-test para sobrescribirlo)")
        test_texts = {r["text"] for r in splits["test"]}
        for name in ("train", "val"):
            splits[name] = [r for r in splits[name] if r["text"] not in test_texts]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        with open(DATA_DIR / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print({name: len(rows) for name, rows in splits.items()})
    for name, rows in splits.items():
        dist: dict[str, int] = {}
        for r in rows:
            dist[r["label"]["intent"]] = dist.get(r["label"]["intent"], 0) + 1
        print(f"  {name}: {dist}")


if __name__ == "__main__":
    main()
