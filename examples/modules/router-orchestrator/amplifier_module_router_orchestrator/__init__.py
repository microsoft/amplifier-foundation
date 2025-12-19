"""Example routing orchestrator module (demo-only).

This lives under examples/ to clearly distinguish sample code from the core
amplifier-foundation runtime. It implements the Orchestrator protocol and
routes between mini vs codex models with a simple heuristic and fallback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from amplifier_core.interfaces import Orchestrator
from amplifier_core.message_models import ChatRequest, Message, TextBlock

logger = logging.getLogger(__name__)


class RoutingOrchestrator:
    """Minimal orchestrator that routes to mini vs codex and records latency."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.mini_model = cfg.get("mini_model", "gpt-5-mini")
        self.codex_model = cfg.get("codex_model", "gpt-5.1-codex")
        self.prefer_mini_first = cfg.get("prefer_mini_first", True)
        self.raw_debug = cfg.get("raw_debug", False)

    def _is_code_prompt(self, prompt: str) -> bool:
        """Detect code-heavy prompts via simple keyword heuristics."""
        keywords = (
            "function",
            "class",
            "refactor",
            "bug",
            "stack trace",
            "traceback",
            "compile",
            "exception",
            "unit test",
            "pytest",
            "api",
            "sdk",
            "script",
        )
        return any(kw in prompt.lower() for kw in keywords)

    def _choose_model(self, prompt: str) -> str:
        """Select model based on prompt characteristics."""
        if self._is_code_prompt(prompt):
            return self.codex_model
        if self.prefer_mini_first and len(prompt) < 600:
            return self.mini_model
        return self.codex_model

    def _provider_choice(self, providers: dict[str, Any]) -> Any:
        """Pick a provider (first available)."""
        if not providers:
            raise RuntimeError("No providers available for routing orchestrator")
        if "openai" in providers:
            return providers["openai"]
        return next(iter(providers.values()))

    def _response_text(self, response: Any) -> str:
        """Extract user-facing text from ChatResponse."""
        if response is None:
            return ""
        if hasattr(response, "text") and response.text:
            return str(response.text)
        if hasattr(response, "content") and response.content:
            texts = []
            for block in response.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif hasattr(block, "text"):
                    texts.append(str(block.text))
            if texts:
                return "\n\n".join(texts)
        return str(response)

    async def execute(  # type: ignore[override]
        self,
        prompt: str,
        context,
        providers,
        tools,
        hooks,
        coordinator=None,  # Accept coordinator for compatibility with AmplifierSession
    ) -> str:
        """Run one turn: choose model, call provider, fallback if needed, record latency."""
        await context.add_message({"role": "user", "content": prompt})

        if await context.should_compact():
            await context.compact()

        raw_messages = await context.get_messages()
        messages = []
        for msg in raw_messages:
            if isinstance(msg, Message):
                messages.append(msg)
            elif isinstance(msg, dict):
                try:
                    messages.append(Message(**msg))
                except Exception as e:
                    logger.debug(f"Skipping message that failed to parse: {e}")

        target_model = self._choose_model(prompt)
        provider = self._provider_choice(providers)
        logger.info(f"[router-orchestrator] model={target_model} provider={getattr(provider, 'name', 'unknown')}")

        request = ChatRequest(messages=messages)
        start = time.perf_counter()
        response = await provider.complete(request, model=target_model)
        latency = time.perf_counter() - start

        reply_text = self._response_text(response)
        used_model = target_model

        if (
            target_model == self.mini_model
            and self._is_code_prompt(prompt)
            and ("```" not in reply_text and "def " not in reply_text and "class " not in reply_text)
        ):
            if self.raw_debug:
                logger.info("[router-orchestrator] escalating to codex due to missing code in mini response")
            else:
                logger.debug("[router-orchestrator] escalating to codex due to missing code in mini response")
            start = time.perf_counter()
            response = await provider.complete(request, model=self.codex_model)
            latency = time.perf_counter() - start
            reply_text = self._response_text(response)
            used_model = self.codex_model

        await context.add_message({"role": "assistant", "content": reply_text})

        if hooks:
            await hooks.emit(
                "orchestrator:turn_complete",
                {"model": used_model, "latency_s": latency, "prompt_chars": len(prompt), "response_chars": len(reply_text)},
            )

        return reply_text


async def mount(coordinator, config: dict[str, Any] | None = None):
    """Module entrypoint: mounts the example orchestrator."""
    orchestrator = RoutingOrchestrator(config=config)
    await coordinator.mount("orchestrator", orchestrator)

    async def cleanup():
        logger.debug("router-orchestrator cleanup")

    return cleanup
