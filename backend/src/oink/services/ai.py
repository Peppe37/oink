"""AI through LiteLLM: any provider (OpenAI, Anthropic, DeepSeek, Gemini, Mistral, Ollama…)
chosen by `OINK_AI_MODEL`. Every feature degrades gracefully when AI is off.

The numbers are always computed by Oink; the AI only reads statements, sorts movements
into categories and puts the facts into words, in the user's language.
Privacy: with `OINK_AI_SEND_DESCRIPTIONS=false` only aggregated numbers are sent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from oink.core.config import Settings
from oink.services.ledger import CATEGORIES

LANG = {"en": "English", "it": "Italian", "de": "German", "fr": "French", "es": "Spanish"}


class AIError(RuntimeError):
    pass


class AI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.ai_enabled

    @property
    def can_read_text(self) -> bool:
        """Statements and descriptions may be sent to the provider."""
        return self.enabled and self.settings.ai_send_descriptions

    async def complete(self, system: str, user: str, *, as_json: bool = False) -> str:
        if not self.enabled:
            raise AIError("ai_off")
        import litellm  # heavy import: only when AI is used

        kwargs: dict[str, Any] = {}
        if self.settings.ai_base_url:
            kwargs["api_base"] = self.settings.ai_base_url
        if as_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await litellm.acompletion(
                model=self.settings.ai_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                timeout=120,
                **kwargs,
            )
        except Exception as exc:  # provider errors come in many shapes
            raise AIError(f"provider_failed:{type(exc).__name__}") from exc
        return str(response.choices[0].message.content or "")

    async def json(self, system: str, user: str) -> Any:
        text = await self.complete(system, user, as_json=True)
        match = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if not match:
            raise AIError("not_json")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIError("not_json") from exc

    async def categorize(self, items: list[tuple[int, str, str]]) -> dict[int, str]:
        """(id, description, signed amount) → category id, for movements no rule knew."""
        if not items or not self.can_read_text:
            return {}
        lines = "\n".join(f"{i}\t{amount}\t{text[:120]}" for i, text, amount in items)
        data = await self.json(
            "You sort bank and card movements (any language) into categories. Reply ONLY "
            'with JSON {"categories": {"<id>": "<category>"}}. Allowed categories: '
            + ", ".join(CATEGORIES)
            + ". A negative amount is money going out. Moving money between one's own "
            "accounts is «transfers».",
            lines,
        )
        found = data.get("categories", {}) if isinstance(data, dict) else {}
        return {int(k): v for k, v in found.items() if str(k).isdigit() and v in CATEGORIES}

    async def extract_movements(self, text: str) -> dict[str, Any]:
        """{"movements": [{day, amount, description, counterparty}], "balance": x|null}"""
        data = await self.json(
            "Extract the movements from a bank, card or broker statement (any language). "
            'Reply ONLY with JSON {"movements": [{"day": "YYYY-MM-DD", "amount": -12.34, '
            '"description": "...", "counterparty": "..."}], "balance": 1234.56 or null}. '
            "Money out is negative, money in positive. Opening/closing balances and summary "
            "lines are not movements; put the closing balance in «balance».",
            text[:60000],
        )
        return data if isinstance(data, dict) else {}

    async def monthly_report(self, facts: dict[str, Any], language: str) -> str:
        return await self.complete(
            f"You are the user's personal finance coach. Write in {LANG.get(language, 'English')}"
            " a monthly report in Markdown: 1) how the month went in 3 lines, 2) where the money"
            " went and what changed against the usual, 3) subscriptions and recurring costs to"
            " review, 4) three concrete, measurable tips. Use only the numbers you receive, never"
            " invent any. Amounts are in the user's base currency. Clear, friendly, no jargon.",
            json.dumps(facts, ensure_ascii=False, default=str),
        )

    async def answer(self, question: str, facts: dict[str, Any], language: str) -> str:
        return await self.complete(
            f"Answer in {LANG.get(language, 'English')} a question about the user's finances "
            "using ONLY the data provided. If the data are not enough, say so. Short and "
            "concrete. No investment advice on single securities.",
            f"Data:\n{json.dumps(facts, ensure_ascii=False, default=str)}\n\nQuestion: {question}",
        )
