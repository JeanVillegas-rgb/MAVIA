"""The one call to the configured LLM provider (Groq, or a dormant local Ollama) for writing and checking questions."""

import json
import logging
import threading
import time

import requests
from django.conf import settings
from config.groq_client import generate as groq_generate

logger = logging.getLogger(__name__)

_warm_lock = threading.Lock()
_warm_model = ""
_warm_until = 0.0


def on_groq():
    return settings.LLM_PROVIDER == "groq"


def _mark_warm(model):
    global _warm_model, _warm_until
    with _warm_lock:
        _warm_model = model
        _warm_until = time.monotonic() + 25 * 60


def _ollama_metrics(data, model):
    """Ollama's nanosecond timings in trace-friendly units."""
    def milliseconds(name):
        value = data.get(name)
        return round(value / 1_000_000, 1) if isinstance(value, (int, float)) else None

    eval_count = data.get("eval_count") or 0
    eval_duration = data.get("eval_duration") or 0
    tokens_per_second = (
        round(eval_count / (eval_duration / 1_000_000_000), 2)
        if eval_count and eval_duration else None
    )
    return {
        "load_ms": milliseconds("load_duration"),
        "prompt_eval_ms": milliseconds("prompt_eval_duration"),
        "eval_ms": milliseconds("eval_duration"),
        "total_ms": milliseconds("total_duration"),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "tokens_per_second": tokens_per_second,
        "model": model,
    }


def generate(prompt, *, model, temperature, max_tokens, timeout, schema=None,
             on_rate_limit_wait=None, use_failed_generation=False):
    """One call to the configured provider; returns (reply text, metrics)."""
    if on_groq():
        return groq_generate(
            prompt,
            model=model,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            on_rate_limit_wait=on_rate_limit_wait,
            use_failed_generation=use_failed_generation,
        )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": settings.QUESTION_LLM_KEEP_ALIVE,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if schema is not None:
        payload["format"] = schema
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    parts = []
    data = None
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        chunk = json.loads(raw_line)
        if chunk.get("error"):
            raise RuntimeError(str(chunk["error"]))
        parts.append(chunk.get("response") or "")
        data = chunk
    if data is None:
        raise ValueError("Ollama returned an empty streaming response.")
    _mark_warm(model)
    return "".join(parts), _ollama_metrics(data, model)


def _warm_hit():
    return {
        "load_ms": 0.0,
        "prompt_eval_ms": 0.0,
        "eval_ms": 0.0,
        "total_ms": 0.0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "tokens_per_second": None,
        "model": settings.QUESTION_LLM_MODEL,
        "warm_cache_hit": True,
    }


def warm_question_model(on_metrics=None):
    """Load the question model before the first real call."""
    if on_groq():
        metrics = _warm_hit()
        if on_metrics:
            on_metrics(metrics)
        return metrics
    with _warm_lock:
        already_warm = (
            _warm_model == settings.QUESTION_LLM_MODEL
            and time.monotonic() < _warm_until
        )
    if already_warm:
        metrics = _warm_hit()
        if on_metrics:
            on_metrics(metrics)
        return metrics

    payload = {
        "model": settings.QUESTION_LLM_MODEL,
        "prompt": "",
        "stream": False,
        "keep_alive": settings.QUESTION_LLM_KEEP_ALIVE,
    }
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=settings.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        metrics = _ollama_metrics(response.json(), settings.QUESTION_LLM_MODEL)
        metrics["warm_cache_hit"] = False
        _mark_warm(settings.QUESTION_LLM_MODEL)
        if on_metrics:
            on_metrics(metrics)
        return metrics
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Question model warm-up failed: %s", exc)
        return None
