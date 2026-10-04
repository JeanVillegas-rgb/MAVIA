"""Small Groq adapter for the existing Ollama-backed generation steps."""

import base64
import json
import threading
import time

import requests
from django.conf import settings


_STRICT_SCHEMA_MODELS = {
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
}

# All Groq-backed steps in one backend process share this queue. Rate-limit
# cooldowns belong to individual keys, so a rejected key is not retried until
# its wait has passed while another configured key may still be available.
_REQUEST_LOCK = threading.Lock()
_ACTIVE_KEYS = ()
_CURRENT_KEY_INDEX = 0
_KEY_COOLDOWNS = {}
_KEY_REJECTIONS = {}


class GroqRateLimitError(ValueError):
    """The request could not fit within the configured rate-limit wait."""


def _rate_limit_delay(response, rejection_count):
    """Prefer Groq's retry hint; back off when a short hint proves insufficient."""
    try:
        retry_after = float(response.headers.get("retry-after", "0"))
    except (TypeError, ValueError):
        retry_after = 0.0
    return max(retry_after, min(2 ** rejection_count, 30.0)) + 0.25


def _configured_keys():
    """Keep the original key working; optional extras are supplied by .env."""
    primary = (settings.GROQ_API_KEY or "").strip()
    extras = getattr(settings, "GROQ_ADDITIONAL_API_KEYS", ())
    if isinstance(extras, str):
        extras = extras.split(",")
    extras = tuple(key.strip() for key in extras)
    return tuple(dict.fromkeys(key for key in (primary, *extras) if key))


def _strict_schema(value):
    """Close every object for Groq's constrained JSON decoding."""
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _strict_schema(item)
        for key, item in value.items()
        if key not in {"minItems", "maxItems"}
    }
    object_type = result.get("type")
    if object_type == "object" or (
        isinstance(object_type, list) and "object" in object_type
    ):
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def generate(
    prompt: str,
    *,
    model: str,
    timeout: int,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    schema: dict | None = None,
    image_bytes: bytes | None = None,
    on_rate_limit_wait=None,
) -> tuple[str, dict]:
    """Return text and usage for one Groq chat-completion request."""
    keys = _configured_keys()
    if not keys:
        raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq.")

    strict_schema = schema is not None and model in _STRICT_SCHEMA_MODELS
    content = prompt
    if schema is not None and not strict_schema:
        content += (
            "\n\nReturn exactly one JSON object matching this schema. "
            "Do not include markdown or other text:\n"
            + json.dumps(schema, ensure_ascii=False)
        )
    if image_bytes is not None:
        content = [
            {"type": "text", "text": content},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(image_bytes).decode("ascii")
                },
            },
        ]

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "stream": False,
    }
    if model.startswith("openai/gpt-oss-"):
        payload["reasoning_effort"] = "low"
    if schema is not None:
        payload["response_format"] = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "mavia_response",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }
            if strict_schema else {"type": "json_object"}
        )

    started = time.monotonic()
    wait_budget = max(0.0, settings.GROQ_MAX_RATE_LIMIT_WAIT)
    global _ACTIVE_KEYS, _CURRENT_KEY_INDEX
    with _REQUEST_LOCK:
        if keys != _ACTIVE_KEYS:
            _ACTIVE_KEYS = keys
            _CURRENT_KEY_INDEX = 0
            _KEY_COOLDOWNS.clear()
            _KEY_REJECTIONS.clear()
        # Queue time should not consume this request's retry allowance.
        deadline = time.monotonic() + wait_budget
        validation_retries = 0
        rate_rejections = 0
        while True:
            now = time.monotonic()
            available = None
            for offset in range(len(keys)):
                index = (_CURRENT_KEY_INDEX + offset) % len(keys)
                if _KEY_COOLDOWNS.get(keys[index], 0) <= now:
                    available = index
                    break
            if available is None:
                delay = min(_KEY_COOLDOWNS[key] for key in keys) - now
                if now + delay > deadline:
                    raise GroqRateLimitError(
                        "Groq HTTP 429 (rate_limit_exceeded): all configured "
                        f"keys need more than the {wait_budget:g}s wait limit."
                    )
                if on_rate_limit_wait:
                    on_rate_limit_wait(delay, rate_rejections)
                time.sleep(delay)
                continue
            key = keys[available]
            response = requests.post(
                f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=timeout,
            )
            if response.ok:
                _KEY_REJECTIONS[key] = 0
                _KEY_COOLDOWNS.pop(key, None)
                break
            try:
                error_code = ((response.json() or {}).get("error") or {}).get("code")
            except ValueError:
                error_code = None
            if response.status_code == 400 and error_code == "json_validate_failed":
                if validation_retries >= 3:
                    break
                validation_retries += 1
                continue
            if response.status_code != 429:
                break

            rate_rejections += 1
            _KEY_REJECTIONS[key] = _KEY_REJECTIONS.get(key, 0) + 1
            delay = _rate_limit_delay(response, _KEY_REJECTIONS[key])
            _KEY_COOLDOWNS[key] = time.monotonic() + delay
            _CURRENT_KEY_INDEX = (available + 1) % len(keys)
    if not response.ok:
        try:
            error = (response.json() or {}).get("error") or {}
        except ValueError:
            error = {}
        code = str(error.get("code") or error.get("type") or "request_failed")
        message = " ".join(str(error.get("message") or "").split())[:240]
        raise ValueError(f"Groq HTTP {response.status_code} ({code}): {message}")
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") or []
    text = (choices[0].get("message") or {}).get("content") if choices else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Groq returned an empty response.")
    usage = body.get("usage") or {}
    metrics = {
        "model": body.get("model") or model,
        "total_ms": (time.monotonic() - started) * 1000,
        "load_ms": 0.0,
        "prompt_eval_ms": None,
        "eval_ms": None,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "tokens_per_second": None,
    }
    return text, metrics
