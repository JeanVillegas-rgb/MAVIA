from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMMetadata:
    provider: str
    model: str
    vision_model: str
    execution: str

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "vision_model": self.vision_model,
            "execution": self.execution,
        }


class LocalLLMError(RuntimeError):
    pass


class LocalLLMClient:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "gemma3:4b")
        self.vision_model = os.getenv("OLLAMA_VISION_MODEL", self.model)
        self.timeout = int(os.getenv("OLLAMA_TIMEOUT", "240"))
        self.vision_timeout = int(os.getenv("OLLAMA_VISION_TIMEOUT", str(self.timeout)))
        self.keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

    @property
    def metadata(self) -> dict:
        return LLMMetadata(
            provider=self.provider,
            model=self.model,
            vision_model=self.vision_model,
            execution="local",
        ).as_dict()

    def _ensure_local_provider(self):
        if self.provider != "ollama":
            raise LocalLLMError("Only local Ollama inference is supported for this project milestone.")

    def generate_json(self, prompt: str, max_tokens: int = 1200, timeout: int | None = None) -> Optional[dict]:
        response = self.generate_text(prompt, max_tokens=max_tokens, timeout=timeout)
        if not response:
            return None
        return extract_json_from_text(response.get("text"))

    def generate_text(self, prompt: str, max_tokens: int = 1200, timeout: int | None = None) -> Optional[dict]:
        self._ensure_local_provider()
        payload = {
            "model": self.model,
            "format": "json",
            "keep_alive": self.keep_alive,
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": max_tokens,
            },
        }
        return self._post_chat(payload, timeout=timeout or self.timeout, model=self.model)

    def describe_image(
        self,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int = 500,
        timeout: int | None = None,
    ) -> Optional[dict]:
        self._ensure_local_provider()
        payload = {
            "model": self.vision_model,
            "format": "json",
            "keep_alive": self.keep_alive,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": max_tokens,
            },
        }
        return self._post_chat(payload, timeout=timeout or self.vision_timeout, model=self.vision_model)

    def _post_chat(self, payload: dict, timeout: int, model: str) -> Optional[dict]:
        try:
            response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.ConnectionError as exc:
            raise LocalLLMError(
                f"Local Ollama is not running at {self.base_url}. Start Ollama and try again."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise LocalLLMError(
                f"Local Ollama timed out while running {model}. Try again or use a smaller local model."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            detail = response.text[:300] if "response" in locals() else ""
            if "not found" in detail.lower() or response.status_code == 404:
                raise LocalLLMError(
                    f"Local Ollama model '{model}' is not available. Run: ollama pull {model}"
                ) from exc
            raise LocalLLMError(f"Local Ollama returned an error: {detail or exc}") from exc
        except Exception as exc:
            logger.exception("Local Ollama call failed: %s", exc)
            raise LocalLLMError(f"Local Ollama call failed: {exc}") from exc

        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return {
                "text": content,
                "llm_metadata": {
                    "provider": self.provider,
                    "model": model,
                    "vision_model": self.vision_model,
                    "execution": "local",
                },
            }
        raise LocalLLMError("Local Ollama returned an empty response.")


def get_llm_client() -> LocalLLMClient:
    return LocalLLMClient()


def extract_json_from_text(text: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
