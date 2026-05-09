"""
Stage 1 — Syntactic Verification
---------------------------------
Sends the user intent to the selected LLM and verifies that the response
contains a well-formed JSON object, free of Markdown wrappers and verbose
text that would invalidate parsing.

Key design decision: temperature=0.0 for deterministic output across runs,
matching the NetConfEval benchmark protocol.

Batch size behavior:
- batch_size=1: sends the single user intent
- batch_size>1: sends N *different* intents from the NetConfEval dataset
  (the user intent + N-1 additional samples), simulating real context
  window stress as described in the paper.
"""

import json
import re
import os
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Prompt template (mirrors NetConfEval Task 1 format) ──────────────────────
SYSTEM_PROMPT = """You are a network configuration expert.
Given one or more network intents expressed in natural language, output ONLY
a valid JSON array where each element is a configuration object for the
corresponding intent. If only one intent is given, output a JSON array with
one element. Do NOT include any explanation, markdown code fences, or extra
text — only the raw JSON array.

Each configuration object should contain:
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


def _build_prompt(
    intent: str,
    policy_type: str,
    batch_size: int,
    rag_context: dict,
    extra_intents: Optional[List[str]] = None,
) -> str:
    """
    Construct the user prompt.

    For batch_size=1: sends the single intent.
    For batch_size>1: sends the user intent + (batch_size-1) different intents
    from the NetConfEval dataset, making the batch realistic.
    """
    examples = ""
    if rag_context and rag_context.get("examples"):
        ex = rag_context["examples"][0]
        examples = (
            f"\n\nReference example:\n"
            f"Intent: {ex.get('intent','')}\n"
            f"Config: {json.dumps(ex.get('config', {}))}\n"
        )

    if batch_size == 1:
        return f"Policy type: {policy_type}{examples}\n\nIntents:\n1. {intent}"

    # Build list: user intent first, then extra intents from dataset
    all_intents = [intent]
    if extra_intents:
        all_intents += extra_intents[: batch_size - 1]
    # If not enough extras, pad by repeating the last one
    while len(all_intents) < batch_size:
        all_intents.append(all_intents[-1])

    intents_block = "\n".join(
        [f"{i + 1}. {intent_i}" for i, intent_i in enumerate(all_intents)]
    )

    return (
        f"Policy type: {policy_type}{examples}\n\n"
        f"Generate one JSON configuration for EACH of the following {batch_size} intents "
        f"(respond with a JSON array of {batch_size} objects, one per intent, in order):\n"
        f"{intents_block}"
    )


def _extract_json(raw: str) -> Any:
    """
    Remove Markdown fences and extract the first valid JSON object or array.
    Raises ValueError if no valid JSON is found.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    for pattern in (r"\[[\s\S]+\]", r"\{[\s\S]+\}"):
        match = re.search(pattern, cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    raise ValueError("No valid JSON found in LLM response")


async def _call_llm(model: str, prompt: str) -> str:
    """Dispatch to the correct LLM provider based on model prefix."""
    if model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
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
    extra_intents: Optional[List[str]] = None,
) -> Dict:
    """
    Execute Stage 1: call the LLM and verify syntactic correctness.

    When batch_size > 1, sends N different intents to the LLM simultaneously,
    faithfully reproducing the context window stress test of the paper.
    The result returned corresponds to the FIRST intent (the user's intent).

    Returns a dict with:
      status:        "pass" | "fail"
      config:        parsed JSON object for intent #1 (if pass)
      all_configs:   list of all N parsed configs (if pass, batch_size > 1)
      batch_intents: list of all N intents sent to the LLM
      raw:           raw LLM response string
      model:         model identifier
      error:         error message (if fail)
    """
    # Build the full list of intents sent to the LLM
    all_intents = [intent]
    if extra_intents and batch_size > 1:
        all_intents += extra_intents[: batch_size - 1]
    while len(all_intents) < batch_size:
        all_intents.append(all_intents[-1])
    all_intents = all_intents[:batch_size]

    prompt = _build_prompt(intent, policy_type, batch_size, rag_context, extra_intents)
    logger.debug(f"Stage 1 | model={model} batch={batch_size} prompt_len={len(prompt)}")

    try:
        raw = await _call_llm(model, prompt)
        logger.debug(f"LLM response ({len(raw)} chars)")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {
            "status": "fail",
            "error": f"LLM unavailable: {e}",
            "model": model,
            "batch_intents": all_intents,
        }

    try:
        parsed = _extract_json(raw)

        # Normalize to list
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            parsed = [parsed]

        # Primary config = first item (corresponds to user's intent)
        config = parsed[0] if parsed else {}

        return {
            "status": "pass",
            "config": config,
            "all_configs": parsed,
            "batch_intents": all_intents,
            "raw": raw,
            "model": model,
        }
    except ValueError as e:
        return {
            "status": "fail",
            "error": str(e),
            "raw": raw,
            "model": model,
            "batch_intents": all_intents,
        }
