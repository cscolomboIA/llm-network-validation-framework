"""
Self-Healing Agent
-------------------
When a pipeline stage fails, this agent reformulates the prompt incorporating
the specific error feedback and retries the LLM call (up to max_attempts times).
Inspired by retry-based deployment [Hachimi et al. 2025], but operating
PREVENTIVELY — before any production deployment.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SelfHealingAgent:
    """
    Autonomous correction agent that iteratively refines LLM-generated
    configurations using stage-specific error feedback as context.
    """

    def __init__(self, model: str, max_attempts: int = 3):
        self.model       = model
        self.max_attempts = max_attempts

    async def heal(
        self,
        intent: str,
        failed_config: dict,
        stage_error: dict,
        rag_context: dict,
        policy_type: str,
    ) -> Optional[dict]:
        """
        Attempt to correct the failed configuration.

        Args:
            intent:        Original user intent
            failed_config: The JSON that failed validation
            stage_error:   Stage result dict containing error details
            rag_context:   RAG examples for additional context
            policy_type:   Policy classification

        Returns:
            Corrected config dict, or None if all attempts fail.
        """
        import json
        from verifier.syntactic import _call_llm, _extract_json

        # Build error summary from failed checks
        checks    = stage_error.get("detail", {}).get("checks", [])
        conflicts = stage_error.get("detail", {}).get("conflicts", [])
        failed_checks = [c for c in checks if not c.get("passed", True)]

        error_summary = "\n".join(
            f"- {c['name']}: {c['detail']}" for c in failed_checks
        ) or stage_error.get("error", "Unknown error")

        # Ground truth hint from RAG
        gt_hint = ""
        examples = rag_context.get("examples", []) if rag_context else []
        if examples:
            gt = examples[0].get("config", {})
            gt_hint = f"\n\nReference (correct) configuration:\n{json.dumps(gt, indent=2)}"

        for attempt in range(1, self.max_attempts + 1):
            logger.info(f"Self-healing attempt {attempt}/{self.max_attempts}")

            healing_prompt = f"""The following network configuration failed validation.

Original intent: {intent}
Policy type: {policy_type}

Your previous (incorrect) configuration:
{json.dumps(failed_config, indent=2)}

Validation errors detected:
{error_summary}
{gt_hint}

Please fix the configuration and respond with ONLY the corrected JSON object.
Do not include any explanation or markdown fences."""

            try:
                raw     = await _call_llm(self.model, healing_prompt)
                healed  = _extract_json(raw)
                if isinstance(healed, list):
                    healed = healed[0] if healed else {}
                logger.info(f"Self-healing attempt {attempt} produced a valid JSON")
                return healed
            except Exception as e:
                logger.warning(f"Self-healing attempt {attempt} failed: {e}")

        logger.error("Self-healing exhausted all attempts")
        return None
