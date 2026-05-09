"""
Stage 1 — Syntactic Verification
---------------------------------
Sends the user intent to the selected LLM and verifies that the response
contains a well-formed JSON object, free of Markdown wrappers and verbose
text that would invalidate parsing.

Key design decision: temperature=0.0 for deterministic output across runs,
matching the NetConfEval benchmark protocol.
"""

import json
import re
import os
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ── Prompt template (mirrors NetConfEval Task 1 format) ──────────────────────
SYSTEM_PROMPT = """You are a network configuration expert.
Given a network intent expressed in natural language, output ONLY a valid JSON
object representing the network policy. Do NOT include any explanation,
markdown code fences, or extra text — only the raw JSON object.

Expected JSON fields for a reachability policy:
{
  "source": "<host or subnet>",
  "destination": "<subnet in CIDR notation>",
  "action": "allow" | "deny",
  "protocol": "any" | "tcp" | "udp" | "icmp",
  "priority": <integer>
}

For waypoint policies, add:
  "waypoints": ["<intermediate_host>", ...]

For load-balancing policies, add:
  "load_balancing": { "algorithm": "round-robin" | "weighted", "paths": [...] }
"""

def _build_prompt(intent: str, policy_type: str, batch_size: int, rag_context: dict) -> str:
    """Construct the user prompt, concatenating multiple intents for batch tests."""
    examples = ""
    if rag_context and rag_context.get("examples"):
        ex = rag_context["examples"][0]
        examples = f"\n\nReference example:\nIntent: {ex.get('intent','')}\nConfig: {json.dumps(ex.get('config',{}))}\n"

    if batch_size == 1:
        return f"Policy type: {policy_type}{examples}\n\nIntent: {intent}"

    # For batch_size > 1, repeat the intent to stress the context window
    intents_block = "\n".join([f"{i+1}. {intent}" for i in range(batch_size)])
    return (
        f"Policy type: {policy_type}{examples}\n\n"
        f"Generate one JSON configuration for EACH of the following intents "
        f"(respond with a JSON array):\n{intents_block}"
    )


def _extract_json(raw: str) -> Any:
    """
    Remove Markdown fences and extract the first valid JSON object or array.
    Raises ValueError if no valid JSON is found.
    """
    # Strip ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find first {...} or [...]
    for pattern in (r"\{[\s\S]+\}", r"\[[\s\S]+\]"):
        match = re.search(pattern, cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    raise ValueError("No valid JSON found in LLM response")


async def _call_llm(model: str, prompt: str) -> str:
    """
    Dispatch to the correct LLM provider based on model prefix.
    Returns the raw string response.
    """
    if model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            temperature=0.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    elif model.startswith("gpt"):
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = await client.chat.completions.create(
            model="gpt-4-turbo" if "turbo" in model else "gpt-4",
            temperature=0.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        )
        return resp.choices[0].message.content

    else:
        # Groq: llama-3.3-70b, mistral-large, deepseek-chat, qwen-3, gemini-*
        from groq import AsyncGroq
        MODEL_MAP = {
            "llama-3.3-70b":  "llama-3.3-70b-versatile",
            "llama-3.1-8b":   "llama-3.1-8b-instant",
            "mistral-large":  "mistral-large-latest",
            "deepseek-chat":  "deepseek-r1-distill-llama-70b",
            "qwen-3":         "qwen-qwq-32b",
            "gemini-2.0-pro": "gemini2-flash-preview",
            "gemini-2.5-pro": "gemini2-flash-preview",
        }
        groq_model = MODEL_MAP.get(model, "llama-3.3-70b-versatile")
        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        resp = await client.chat.completions.create(
            model=groq_model,
            temperature=0.0,
            top_p=1.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        )
        return resp.choices[0].message.content


async def run_syntactic_check(
    intent: str,
    model: str,
    policy_type: str,
    batch_size: int,
    rag_context: dict,
) -> Dict:
    """
    Execute Stage 1: call the LLM and verify syntactic correctness.

    Returns a dict with:
      status: "pass" | "fail"
      config: parsed JSON object (if pass)
      raw:    raw LLM response string
      model:  model identifier
      error:  error message (if fail)
    """
    prompt = _build_prompt(intent, policy_type, batch_size, rag_context)
    logger.debug(f"Stage 1 prompt ({len(prompt)} chars)")

    try:
        raw = await _call_llm(model, prompt)
        logger.debug(f"LLM response ({len(raw)} chars)")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {"status": "fail", "error": f"LLM unavailable: {e}", "model": model}

    try:
        config = _extract_json(raw)
        # For batch runs, take only the first config
        if isinstance(config, list):
            config = config[0] if config else {}
        return {"status": "pass", "config": config, "raw": raw, "model": model}
    except ValueError as e:
        return {"status": "fail", "error": str(e), "raw": raw, "model": model}
