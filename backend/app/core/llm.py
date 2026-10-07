"""
Single LLM access point for every reasoning agent in the pipeline.

REAL OPENAI ONLY. No mock mode. If OPENAI_API_KEY is missing or the API
call fails for any reason (auth, rate limit, timeout, network), this
raises immediately with a message identifying which agent's call failed
and why -- it does not fall back to canned output. This is a deliberate
choice: silently degrading to fake data during a live demo is worse than
a clear, fast failure you can explain to judges on the spot.

Agents call `llm_client.complete(...)` -- never the OpenAI SDK directly --
so provider/model changes stay a one-file change.
"""
from __future__ import annotations

from app.core.config import settings


class LLMCallError(RuntimeError):
    """Raised when a real OpenAI call fails. Carries which agent failed and why."""

    def __init__(self, agent_hint: str, cause: Exception):
        self.agent_hint = agent_hint
        self.cause = cause
        super().__init__(
            f"LLM call failed for agent '{agent_hint or 'unknown'}': {type(cause).__name__}: {cause}"
        )


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str):
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Every reasoning agent in this pipeline "
                "requires a real OpenAI API call -- there is no mock fallback. "
                "Set OPENAI_API_KEY in .env before starting the server."
            )
        # Imported lazily so import errors surface as a clear message rather
        # than crashing module import if the package isn't installed yet.
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
    ) -> str:
        agent_hint = _extract_agent_hint(system_prompt)
        # Agent hint tags (e.g. "[AGENT: intent_parser]") are for error
        # messages only -- OpenAI never sees them.
        clean_system_prompt = _strip_agent_hint(system_prompt)

        kwargs = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": clean_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1500,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMCallError(agent_hint, exc) from exc

        content = response.choices[0].message.content
        if content is None:
            raise LLMCallError(agent_hint, RuntimeError("OpenAI returned an empty message"))

        return content


def _extract_agent_hint(system_prompt: str) -> str:
    """Agents prefix their system prompt with '[AGENT: agent_name]' purely
    so a failed call's error message says which agent broke."""
    if system_prompt.startswith("[AGENT:"):
        return system_prompt.split("]")[0].replace("[AGENT:", "").strip()
    return ""


def _strip_agent_hint(system_prompt: str) -> str:
    if system_prompt.startswith("[AGENT:"):
        return system_prompt.split("]", 1)[1].lstrip()
    return system_prompt


def get_llm_client() -> LLMClient:
    return LLMClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )


llm_client = get_llm_client()
