"""
LLM Router — Claude, Groq, OpenRouter, Mistral
Aplica o template de prompt do NetConfEval + contexto RAG
Temperatura T=0.0 em todos os modelos (reproduz condições do artigo)
"""

import json
import os
import logging
from typing import Any

import anthropic
import httpx

logger = logging.getLogger("llm-router")

NETCONFEVAL_SYSTEM = """You are a network configuration expert.
Your task is to translate natural language network intents into formal JSON network configurations.

Output ONLY valid JSON. No explanations, no markdown, no preamble.

The JSON must follow this schema:
{
  "intent-type": "reachability" | "waypoint" | "load-balancing",
  "source": "<host-identifier>",
  "destination": "<ip-prefix>",
  "action": "allow" | "deny",
  "protocol": "ipv4" | "ipv6",
  "priority": <integer 1-1000>,
  "waypoints": ["<router-id>"],
  "load-balance": {"weights": {"link1": <0-100>, "link2": <0-100>}},
  "constraints": []
}"""

AVAILABLE_MODELS = {
    "anthropic": [
        {"id": "claude-sonnet",  "label": "Claude Sonnet 4.6",  "api_id": "claude-sonnet-4-6"},
        {"id": "claude-opus",    "label": "Claude Opus 4.6",    "api_id": "claude-opus-4-6"},
        {"id": "claude-haiku",   "label": "Claude Haiku 4.5",   "api_id": "claude-haiku-4-5-20251001"},
    ],
    "groq": [
        {"id": "llama-3.3-70b",   "label": "Llama 3.3 70B",       "api_id": "llama-3.3-70b-versatile"},
        {"id": "llama-3.1-8b",    "label": "Llama 3.1 8B",        "api_id": "llama-3.1-8b-instant"},
        {"id": "deepseek-r1",     "label": "DeepSeek R1",          "api_id": "deepseek-r1-distill-llama-70b"},
        {"id": "gemma2-9b",       "label": "Gemma 2 9B",           "api_id": "gemma2-9b-it"},
    ],
    "openrouter": [
        {"id": "gpt-4o",           "label": "GPT-4o",              "api_id": "openai/gpt-4o"},
        {"id": "gemini-2.5-pro",   "label": "Gemini 2.5 Pro",     "api_id": "google/gemini-2.5-pro-preview"},
        {"id": "deepseek-chat",    "label": "DeepSeek Chat V3",    "api_id": "deepseek/deepseek-chat-v3-0324"},
        {"id": "qwen3-235b",       "label": "Qwen3 235B",          "api_id": "qwen/qwen3-235b-a22b"},
        {"id": "llama-4-maverick", "label": "Llama 4 Maverick",   "api_id": "meta-llama/llama-4-maverick"},
    ],
    "mistral": [
        {"id": "mistral-large",  "label": "Mistral Large",        "api_id": "mistral-large-latest"},
        {"id": "mistral-small",  "label": "Mistral Small",        "api_id": "mistral-small-latest"},
        {"id": "codestral",      "label": "Codestral",            "api_id": "codestral-latest"},
    ],
}

# Índice rápido: model_id -> (provider, api_id)
_MODEL_INDEX: dict[str, tuple[str, str]] = {}
for _provider, _models in AVAILABLE_MODELS.items():
    for _m in _models:
        _MODEL_INDEX[_m["id"]] = (_provider, _m["api_id"])


def build_user_prompt(intent: str, rag_context: dict, batch_size: int) -> str:
    examples_block = ""
    if rag_context.get("examples"):
        examples_block = "\n\nRELEVANT EXAMPLES FROM GROUND TRUTH:\n"
        for ex in rag_context["examples"][:3]:
            examples_block += f"Intent: {ex['intent']}\nConfig: {json.dumps(ex['config'], indent=2)}\n\n"

    errors_block = ""
    if rag_context.get("known_errors"):
        errors_block = "\n\nKNOWN FAILURE PATTERNS TO AVOID:\n"
        errors_block += "\n".join(f"- {e}" for e in rag_context["known_errors"])

    if batch_size == 1:
        return f"{examples_block}{errors_block}\n\nTranslate this intent to JSON:\n{intent}"

    intents_block = "\n".join(f"{i+1}. {intent}" for i in range(batch_size))
    return f"{examples_block}{errors_block}\n\nTranslate ALL {batch_size} intents to a JSON array:\n{intents_block}"


class LLMRouter:
    def __init__(self):
        self.anthropic_key  = os.environ.get("ANTHROPIC_API_KEY", "")
        self.groq_key       = os.environ.get("GROQ_API_KEY", "")
        self.openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.mistral_key    = os.environ.get("MISTRAL_API_KEY", "")
        if self.anthropic_key:
            self._anthropic = anthropic.Anthropic(api_key=self.anthropic_key)

    async def generate(self, intent: str, model: str, batch_size: int = 1, rag_context: dict | None = None) -> dict[str, Any]:
        rag_context = rag_context or {}
        prompt = build_user_prompt(intent, rag_context, batch_size)

        if model.startswith("claude"):
            return await self._call_claude(model, prompt)

        provider, api_id = _MODEL_INDEX.get(model, (None, None))
        if not provider:
            raise ValueError(f"Modelo não reconhecido: '{model}'")

        if provider == "groq":
            return await self._call_openai_compat("https://api.groq.com/openai/v1/chat/completions", self.groq_key, api_id, prompt, "GROQ_API_KEY")
        elif provider == "openrouter":
            return await self._call_openai_compat("https://openrouter.ai/api/v1/chat/completions", self.openrouter_key, api_id, prompt, "OPENROUTER_API_KEY",
                extra_headers={"HTTP-Referer": "https://github.com/cscolomboIA/llm-network-validation-framework", "X-Title": "NetValidAI SBRC 2025"})
        elif provider == "mistral":
            return await self._call_openai_compat("https://api.mistral.ai/v1/chat/completions", self.mistral_key, api_id, prompt, "MISTRAL_API_KEY")

    async def _call_claude(self, model_short: str, prompt: str) -> dict:
        if not self.anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY não configurada")
        api_id = next((m["api_id"] for m in AVAILABLE_MODELS["anthropic"] if m["id"] == model_short), "claude-sonnet-4-6")
        resp = self._anthropic.messages.create(
            model=api_id, max_tokens=2048, temperature=0.0,
            system=NETCONFEVAL_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse(resp.content[0].text, api_id)

    async def _call_openai_compat(self, url: str, key: str, api_id: str, prompt: str, key_name: str, extra_headers: dict = None) -> dict:
        if not key:
            raise ValueError(f"{key_name} não configurada no .env")
        headers = {"Authorization": f"Bearer {key}", **(extra_headers or {})}
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, headers=headers, json={
                "model": api_id, "temperature": 0.0, "max_tokens": 2048,
                "messages": [
                    {"role": "system", "content": NETCONFEVAL_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
            })
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        return self._parse(raw, api_id)

    def _parse(self, raw: str, model_id: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            end = -1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[1:end]).strip()
        try:
            config = json.loads(cleaned)
            return {"config": config, "raw": raw, "model": model_id, "syntax_valid": True, "cleaned": cleaned}
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON inválido ({model_id}): {e} | Raw: {raw[:300]}")
