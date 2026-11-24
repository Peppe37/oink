"""AI through LiteLLM: any provider (OpenAI, Anthropic, DeepSeek, Gemini, Mistral, Ollama…)
chosen by `OINK_AI_MODEL`. Every feature degrades gracefully when AI is off.

Privacy: with `OINK_AI_SEND_DESCRIPTIONS=false` only aggregated numbers are sent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from oink.core.config import Settings
from oink.services.ledger import CATEGORIES

LANG = {"it": "italiano", "en": "English", "es": "español", "fr": "français", "de": "Deutsch"}


class AIError(RuntimeError):
    pass


class AI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.ai_enabled

    @property
    def language(self) -> str:
        return LANG.get(self.settings.language, self.settings.language)

    async def complete(self, system: str, user: str, *, as_json: bool = False) -> str:
        if not self.enabled:
            raise AIError("IA non configurata (OINK_AI_MODEL).")
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
            raise AIError(f"il provider IA non ha risposto: {type(exc).__name__}") from exc
        return str(response.choices[0].message.content or "")

    async def json(self, system: str, user: str) -> Any:
        text = await self.complete(system, user, as_json=True)
        match = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if not match:
            raise AIError("risposta IA non in JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIError("risposta IA non in JSON") from exc

    async def categorize(self, items: list[tuple[int, str, int]]) -> dict[int, str]:
        """(id, description, cents) → category, for the movements no rule recognised."""
        if not items or not self.settings.ai_send_descriptions:
            return {}
        lines = "\n".join(f"{i}\t{cents / 100:.2f}\t{text[:120]}" for i, text, cents in items)
        data = await self.json(
            "Classifichi movimenti bancari italiani. Rispondi SOLO con JSON "
            '{"categories": {"<id>": "<categoria>"}}. Categorie ammesse: '
            + ", ".join(CATEGORIES)
            + ". Importo negativo = uscita.",
            lines,
        )
        found = data.get("categories", {}) if isinstance(data, dict) else {}
        return {int(k): v for k, v in found.items() if str(k).isdigit() and v in CATEGORIES}

    async def extract_movements(self, text: str) -> list[dict[str, Any]]:
        """Movements from a statement's text (PDF or an unknown CSV layout)."""
        data = await self.json(
            "Estrai i movimenti da un estratto conto. Rispondi SOLO con JSON "
            '{"movements": [{"day": "YYYY-MM-DD", "amount": -12.34, "description": "...", '
            '"counterparty": "..."}], "balance": 1234.56 o null}. Uscite negative, entrate '
            "positive. Ignora saldi iniziali/finali e righe di riepilogo come movimenti.",
            text[:60000],
        )
        if not isinstance(data, dict):
            return []
        rows = data.get("movements", [])
        result = [r for r in rows if isinstance(r, dict)]
        if data.get("balance") is not None:
            result.append({"_balance": data["balance"]})
        return result

    async def monthly_report(self, facts: dict[str, Any]) -> str:
        return await self.complete(
            f"Sei il consulente finanziario personale dell'utente. Scrivi in {self.language} "
            "un resoconto mensile in Markdown: 1) com'è andato il mese in 3 righe, 2) dove sono "
            "andati i soldi e cosa è cambiato rispetto alla media, 3) abbonamenti e spese "
            "ricorrenti da rivedere, 4) tre consigli concreti e misurabili. Usa solo i numeri "
            "che ricevi, non inventare. Tono chiaro, amichevole, niente gergo.",
            json.dumps(facts, ensure_ascii=False, default=str),
        )

    async def answer(self, question: str, facts: dict[str, Any]) -> str:
        return await self.complete(
            f"Rispondi in {self.language} a una domanda sulle finanze dell'utente usando SOLO i "
            "dati forniti. Se i dati non bastano dillo. Breve e concreto. Non dare consigli di "
            "investimento su singoli titoli.",
            f"Dati:\n{json.dumps(facts, ensure_ascii=False, default=str)}\n\nDomanda: {question}",
        )
