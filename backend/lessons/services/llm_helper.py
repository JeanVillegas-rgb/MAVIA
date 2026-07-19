import logging
from typing import Optional

from .llm_client import extract_json_from_text, get_llm_client

logger = logging.getLogger(__name__)


def _call_llm(prompt: str, max_tokens: int = 1200, timeout: int = 60) -> Optional[dict]:
    logger.debug("LLM helper routed to local Ollama")
    return get_llm_client().generate_text(prompt, max_tokens=max_tokens, timeout=timeout)


def _extract_json_from_text(text: str) -> Optional[dict]:
    return extract_json_from_text(text)
