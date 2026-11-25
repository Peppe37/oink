"""AI through LiteLLM: any provider (OpenAI, Anthropic, DeepSeek, Gemini, Mistral, Ollama…)
chosen by `OINK_AI_MODEL`. Every feature degrades gracefully when AI is off.

The numbers are always computed by Oink; the AI only reads statements, sorts movements
into categories and puts the facts into words, in the user's language.
Privacy: with `OINK_AI_SEND_DESCRIPTIONS=false` only aggregated numbers are sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from oink.core.config import Settings
from oink.services.ledger import CATEGORIES

LANG = {"en": "English", "it": "Italian", "de": "German", "fr": "French", "es": "Spanish"}


class AIError(RuntimeError):
    pass


#: Providers offered in the app (LiteLLM prefixes). Any other LiteLLM model id works too.
PROVIDERS = {
    "openai": {"example": "openai/gpt-5.4-mini", "key": True},
    "anthropic": {"example": "anthropic/claude-sonnet-5-5", "key": True},
    "gemini": {"example": "gemini/gemini-2.5-flash", "key": True},
    "mistral": {"example": "mistral/mistral-medium-latest", "key": True},
    "deepseek": {"example": "deepseek/deepseek-chat", "key": True},
    "groq": {"example": "groq/llama-3.3-70b-versatile", "key": True},
    "openrouter": {"example": "openrouter/openai/gpt-5.4-mini", "key": True},
    "ollama": {"example": "ollama/llama3.1", "key": False},
}
AI_KEYS = ("ai.model", "ai.base_url", "ai.api_key", "ai.send_descriptions")


@dataclass
class AIConfig:
    model: str
    base_url: str
    api_key: str  # "" = the provider's usual environment variable
    send_descriptions: bool
    source: str  # "app" | "env"


class AI:
    """`store` (optional) returns the admin's settings from the app, which win over .env."""

    def __init__(self, settings: Settings, store: Any = None) -> None:
        self.settings = settings
        self.store = store

    def config(self) -> AIConfig:
        if self.store is not None:
            found: AIConfig | None = self.store()
            if found is not None and found.model:
                return found
        s = self.settings
        return AIConfig(s.ai_model, s.ai_base_url, "", s.ai_send_descriptions, "env")

    @property
    def enabled(self) -> bool:
        return bool(self.config().model)

    @property
    def can_read_text(self) -> bool:
        """Statements and descriptions may be sent to the provider."""
        cfg = self.config()
        return bool(cfg.model) and cfg.send_descriptions

    async def complete(
        self,
        system: str,
        user: str | list[dict[str, Any]],
        *,
        as_json: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        cfg = self.config()
        if not cfg.model:
            raise AIError("ai_off")
        import litellm  # heavy import: only when AI is used

        kwargs: dict[str, Any] = {}
        if cfg.base_url:
            kwargs["api_base"] = cfg.base_url
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        if as_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await litellm.acompletion(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    *(history or []),
                    {"role": "user", "content": user},
                ],
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

    async def advise(self, picture: dict[str, Any], language: str, focus: str = "") -> str:
        """Personal money plan from the whole picture (computed by Oink)."""
        return await self.complete(
            f"You are a careful, independent personal finance coach. Write in "
            f"{LANG.get(language, 'English')}, in Markdown, for this person only, using ONLY the "
            "facts given (numbers in their main currency). Cover: 1) where they stand (net worth, "
            "liquidity, emergency fund in months, savings rate, fixed costs vs income); 2) how to "
            "split income (needs / wants / saving & investing) given how they actually live; "
            "3) investing: whether their plans (PAC) and holdings fit their risk profile and "
            "horizon, diversification, costs, what to do next with concrete monthly amounts; "
            "4) risks to fix first (debts, no emergency fund, concentration, crypto share); "
            "5) three to five concrete, measurable steps. If the risk questionnaire is missing, "
            "say so and keep advice general. Never promise returns; mention that simulations "
            "are not forecasts. No single-stock picks. Be warm, direct, no jargon."
            + (f" The user especially asks about: {focus}" if focus else ""),
            json.dumps(picture, ensure_ascii=False, default=str),
        )

    async def answer(self, question: str, facts: dict[str, Any], language: str) -> str:
        return await self.complete(
            f"Answer in {LANG.get(language, 'English')} a question about the user's finances "
            "using ONLY the data provided. If the data are not enough, say so. Short and "
            "concrete. No investment advice on single securities.",
            f"Data:\n{json.dumps(facts, ensure_ascii=False, default=str)}\n\nQuestion: {question}",
        )

    async def read_image(self, mime: str, b64: str) -> str:
        """The text of a photo or scan (a receipt, a payslip), for models that see images."""
        return await self.complete(
            "Transcribe all the text of this document image faithfully, keeping numbers, "
            "dates and the layout of tables as plain text lines. Reply with the text only.",
            [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}],
        )

    async def describe_document(self, text: str, filename: str) -> dict[str, Any]:
        """{"kind", "title", "date", "issuer", "summary", "facts"} for a financial document."""
        data = await self.json(
            "You file personal financial documents (any language). Reply ONLY with JSON "
            '{"kind": one of statement|payslip|contract|invoice|receipt|tax|insurance|'
            'investment|other, "title": short title in the document\'s language, "date": '
            '"YYYY-MM-DD" (the date the document refers to) or null, "issuer": who issued it, '
            '"summary": two sentences, "facts": {key: value} with the key numbers (e.g. for a '
            "payslip gross_pay, net_pay, period; for a contract parties, start, end, monthly_cost,"
            " notice; for an invoice total, due_date), decimal numbers as strings}.",
            f"File name: {filename}\n\n{text[:24000]}",
        )
        return data if isinstance(data, dict) else {}

    async def answer_from_documents(
        self,
        question: str,
        passages: list[dict[str, Any]],
        language: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        sources = "\n\n".join(
            f"[{p['n']}] {p['title']} ({p['date'] or 'no date'}):\n{p['text']}" for p in passages
        )
        return await self.complete(
            f"Answer in {LANG.get(language, 'English')} using ONLY the numbered passages from "
            "the user's own financial documents. Cite the passages you use as [1], [2]. If the "
            "passages do not contain the answer, say so plainly and suggest which document "
            "would. Be short and precise with numbers and dates.",
            f"Passages:\n{sources}\n\nQuestion: {question}",
            history=history,
        )

    async def review_document(
        self, kind: str, issues: list[dict[str, Any]], rows: list[list[str]], text: str,
        language: str,
    ) -> dict[str, Any]:  # fmt: skip
        """Where a document's numbers don't add up and what to check. It never changes
        anything: Oink shows the note, the person decides."""
        data = await self.json(
            f"You check personal financial documents. Write in {LANG.get(language, 'English')}. "
            "Oink read the numbers by rules and a check failed or could not be done. Compare "
            "the checks, the movements Oink read and the document text, and say what is most "
            "likely wrong (a missed or doubled line, a wrong sign, a misread amount, a balance "
            'not found). Reply ONLY with JSON {"explanation": "two or three sentences", '
            '"suggestions": ["concrete things to check or fix, with dates and amounts"], '
            '"confidence": "low|medium|high"}. Never invent numbers that are not in the text.',
            json.dumps({"kind": kind, "checks": issues, "movements": rows}, ensure_ascii=False)
            + "\n\nDocument text:\n"
            + text,
        )
        return data if isinstance(data, dict) else {}
