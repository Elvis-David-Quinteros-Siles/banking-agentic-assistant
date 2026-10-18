"""Fine-tuning LoRA / QLoRA de Qwen2.5-0.5B-Instruct para el router (intención + entidades JSON).

Decisiones:
- La loss se calcula SOLO sobre la respuesta JSON (los tokens del prompt se enmascaran con -100):
  el modelo aprende a responder, no a repetir el prompt de sistema.
- LoRA sobre todas las proyecciones de atención y MLP (r=16): ~1% de parámetros entrenables.
- --qlora carga el modelo base en 4 bits (NF4, bitsandbytes). Con 0.5B no hace falta en una GPU
  de 4 GB, pero es el mismo script que se usaría con Llama-3.2-3B.

Uso:
    python finetune/train_lora.py                       # LoRA bf16 (recomendado para 0.5B)
    python finetune/train_lora.py --qlora               # QLoRA 4-bit
    python finetune/train_lora.py --epochs 3 --lr 2e-4
Salida: finetune/adapters/qwen2.5-0.5b-intent-lora/ (+ training_metrics.json)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))  # formato compartido con la inferencia

from app.llm.intent_format import build_messages, serialize_label  # noqa: E402

DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT = ROOT / "adapters" / "qwen2.5-0.5b-intent-lora"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tokenize_example(example: dict, tokenizer, max_length: int) -> dict:
    prompt = tokenizer.apply_chat_template(build_messages(example["text"]), tokenize=False, add_generation_prompt=True)
    target = serialize_label(example["label"]) + tokenizer.eos_token  # <|im_end|> cierra el turno
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    target_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
    input_ids = (prompt_ids + target_ids)[:max_length]
    labels = ([-100] * len(prompt_ids) + target_ids)[:max_length]
    return {"input_ids": input_ids, "attention_mask": [1] * len(input_ids), "labels": labels}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qlora", action="store_true", help="Cargar el modelo base en 4 bits (NF4)")
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    # Batch chico + acumulación (efectivo 16): con 4 GB de VRAM el cuello de botella son los
    # logits del vocabulario de Qwen (152k) por token, no los pesos del modelo
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        print("Aviso: no hay GPU CUDA; el entrenamiento en CPU es posible pero lento (~10x).")
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    tokenizer.padding_side = "right"

    quantization = None
    if args.qlora:
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if use_bf16 else torch.float16,
        )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16 if use_bf16 else torch.float32,
        quantization_config=quantization,
        device_map={"": 0} if torch.cuda.is_available() else None,
    )
    if args.qlora:
        model = prepare_model_for_kbit_training(model)
    else:
        model.enable_input_require_grads()  # necesario para gradient checkpointing con LoRA

    lora_config = LoraConfig(
        r=args.rank,
        lora_alpha=args.rank * 2,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"Parámetros entrenables: {trainable:,} de {total:,} ({100 * trainable / total:.2f}%)")

    def to_dataset(split: str) -> Dataset:
        rows = load_jsonl(DATA_DIR / f"{split}.jsonl")
        return Dataset.from_list(rows).map(
            lambda ex: tokenize_example(ex, tokenizer, args.max_length),
            remove_columns=["text", "label", "template_id"],
        )

    train_ds, val_ds = to_dataset("train"), to_dataset("val")

    training_args = TrainingArguments(
        output_dir=str(ROOT / "outputs"),
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        lr_scheduler_type="cosine",
        warmup_steps=0.05,  # transformers 5: un float < 1 es proporción del total de pasos
        weight_decay=0.0,
        bf16=use_bf16,
        # Recalcula activaciones en el backward: ~30% más lento, mucha menos VRAM
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="no",  # se guarda solo el adaptador final (pocos MB)
        report_to="none",
        seed=args.seed,
        dataloader_num_workers=0,  # Windows: evita problemas de multiprocessing
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100),
    )

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    result = trainer.train()
    elapsed = time.perf_counter() - started
    eval_metrics = trainer.evaluate()

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)

    metrics = {
        "base_model": args.base_model,
        "method": "QLoRA (NF4)" if args.qlora else ("LoRA bf16" if use_bf16 else "LoRA fp32"),
        "rank": args.rank,
        "epochs": args.epochs,
        "learning_rate": args.lr,
        "effective_batch_size": args.batch_size * args.grad_accum,
        "train_examples": len(train_ds),
        "val_examples": len(val_ds),
        "trainable_params": trainable,
        "total_params": total,
        "train_loss": round(result.training_loss, 4),
        "val_loss": round(eval_metrics["eval_loss"], 4),
        "train_seconds": round(elapsed, 1),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "adapter_size_mb": round(sum(f.stat().st_size for f in args.output.glob("*.safetensors")) / 1e6, 1),
    }
    (args.output / "training_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
