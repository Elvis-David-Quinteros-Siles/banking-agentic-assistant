"""Clasificador de intención con Qwen2.5-0.5B + adaptador LoRA (inferencia local).

- Carga diferida: torch/transformers solo se importan si ROUTER_BACKEND=lora, de modo que el
  backend (y el CI) no necesitan esas dependencias pesadas para funcionar.
- Decodificación greedy, determinista, con tope de tokens: la salida esperada mide ~25 tokens.
- Confianza: probabilidad conjunta de los tokens que forman el valor de "intent", tomada de los
  logits de la propia generación (sin pasadas extra). Alimenta el umbral de aclaración del router.
"""

import logging
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.llm.intent_format import build_messages, parse_output

logger = logging.getLogger("lora_router")


@dataclass
class LoraPrediction:
    output: dict[str, Any]  # {"intent", "producto", "monto", "moneda"}
    confidence: float
    latency_ms: float
    raw: str
    tokens_in: int
    tokens_out: int


class LoraIntentClassifier:
    def __init__(self, base_model: str, adapter_path: Path | None, device: str = "auto", max_new_tokens: int = 48):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        dtype = torch.bfloat16 if self.device == "cuda" and torch.cuda.is_bf16_supported() else torch.float32

        self.tokenizer = AutoTokenizer.from_pretrained(base_model)
        model = AutoModelForCausalLM.from_pretrained(base_model, dtype=dtype)
        if adapter_path is not None:
            from peft import PeftModel

            # merge_and_unload: fusiona LoRA en los pesos base → misma latencia que el modelo base
            model = PeftModel.from_pretrained(model, str(adapter_path)).merge_and_unload()
        self.model = model.to(self.device).eval()  # type: ignore[arg-type, unused-ignore]  # stubs de transformers (sin torch instalado, como en CI, no aplica)
        self.max_new_tokens = max_new_tokens
        self.adapter_path = adapter_path
        logger.info("Router LoRA cargado (%s, adapter=%s, device=%s)", base_model, adapter_path, self.device)

    def _intent_confidence(self, token_ids: list[int], probs: list[float]) -> float:
        """Probabilidad conjunta de los tokens que cubren el valor del campo intent."""
        marker = '"intent":"'
        text, spans = "", []
        for token_id in token_ids:
            start = len(text)
            text += str(self.tokenizer.decode([token_id]))
            spans.append((start, len(text)))
        value_start = text.find(marker)
        if value_start == -1:
            return 0.0
        value_start += len(marker)
        value_end = text.find('"', value_start)
        logprob = sum(
            math.log(max(p, 1e-9))
            for (start, end), p in zip(spans, probs, strict=True)
            if start < value_end and end > value_start
        )
        return round(math.exp(logprob), 3)

    def classify(self, text: str) -> LoraPrediction:
        torch = self.torch
        started = time.perf_counter()
        prompt = self.tokenizer.apply_chat_template(build_messages(text), tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)

        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                output_scores=True,
                return_dict_in_generate=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new_ids = generated.sequences[0, inputs["input_ids"].shape[1] :].tolist()
        probs = [
            torch.softmax(score[0].float(), dim=-1)[token_id].item()
            for score, token_id in zip(generated.scores, new_ids, strict=True)
        ]
        raw = str(self.tokenizer.decode(new_ids, skip_special_tokens=True))
        output = parse_output(raw)  # lanza ValueError si el JSON no es válido → el router cae a reglas
        return LoraPrediction(
            output=output,
            confidence=self._intent_confidence(new_ids, probs),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            raw=raw,
            tokens_in=int(inputs["input_ids"].shape[1]),
            tokens_out=len(new_ids),
        )


@lru_cache
def get_lora_classifier() -> LoraIntentClassifier:
    from app.config import get_settings

    settings = get_settings()
    return LoraIntentClassifier(settings.lora_base_model, settings.lora_adapter_path, settings.lora_device)
