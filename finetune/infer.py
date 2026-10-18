"""Inferencia local con el router LoRA.

Uso:
    python finetune/infer.py "me cobraron Bs 350 dos veces en la tarjeta"
    python finetune/infer.py --no-adapter "..."      # modelo base sin fine-tuning (comparación)
    python finetune/infer.py --device cpu "..."      # medir latencia en CPU
    python finetune/infer.py                          # modo interactivo
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.llm.lora_router import LoraIntentClassifier  # noqa: E402

DEFAULT_ADAPTER = ROOT / "adapters" / "qwen2.5-0.5b-intent-lora"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--no-adapter", action="store_true")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    adapter = None if args.no_adapter else args.adapter
    if adapter is not None and not adapter.exists():
        sys.exit(f"No existe el adaptador {adapter}. Ejecuta primero: python finetune/train_lora.py")
    classifier = LoraIntentClassifier(args.base_model, adapter, args.device)

    def run(text: str) -> None:
        try:
            pred = classifier.classify(text)
            print(json.dumps({**pred.output, "confidence": pred.confidence, "latency_ms": pred.latency_ms},
                             ensure_ascii=False))
        except ValueError as error:
            print(f"Salida inválida: {error}")

    if args.text:
        run(" ".join(args.text))
        return
    print("Modo interactivo (Ctrl+C para salir)")
    while True:
        try:
            run(input("> "))
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    main()
