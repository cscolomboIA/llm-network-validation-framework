"""
NetValidAI — Self-Healing Agent
Contribuição nova da tese (trabalho futuro do artigo SBRC 2025).

Loop de autocorreção:
1. Recebe config que falhou + classificação do erro
2. Constrói prompt corretivo direcionado ao tipo de falha
3. Retroalimenta o LLM com contexto do erro
4. Persiste falha+correção no RAG para aprendizado contínuo
5. Repete até MAX_ATTEMPTS ou sucesso
"""

import json
import logging
import os
from typing import Any

import anthropic
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level="INFO")
logger = logging.getLogger("self-healing-agent")

app = FastAPI(title="NetValidAI Self-Healing Agent", version="2.0.0")

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MAX_ATTEMPTS = int(os.environ.get("MAX_HEALING_ATTEMPTS", 3))


class HealRequest(BaseModel):
    run_id: str
    intent: str
    model: str
    failed_config: dict
    verify_result: dict
    mininet_result: dict
    rag_context: dict


class HealResponse(BaseModel):
    success: bool
    attempts: int
    final_config: dict | None
    healing_log: list[dict]
    learned: bool   # True se o erro foi persistido no RAG


@app.post("/heal", response_model=HealResponse)
async def heal(req: HealRequest):
    healing_log = []
    current_intent = req.intent
    http = httpx.AsyncClient(timeout=120.0)

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info(f"Self-healing tentativa {attempt}/{MAX_ATTEMPTS} — run {req.run_id}")

            # 1. Constrói prompt corretivo baseado no tipo de erro
            error_type = req.verify_result.get("error_classification", "unknown")
            corrective_prompt = build_corrective_prompt(
                original_intent=req.intent,
                failed_config=req.failed_config,
                verify_result=req.verify_result,
                mininet_result=req.mininet_result,
                error_type=error_type,
                attempt=attempt,
                rag_context=req.rag_context,
            )

            healing_log.append({
                "attempt": attempt,
                "error_type": error_type,
                "corrective_prompt_preview": corrective_prompt[:300] + "...",
            })

            # 2. Gera nova config com contexto do erro
            try:
                new_config = await generate_corrected(corrective_prompt, req.model)
            except Exception as e:
                healing_log[-1]["error"] = str(e)
                continue

            healing_log[-1]["new_config"] = new_config

            # 3. Executa pipeline completo na nova config
            verify_resp = await http.post(
                "http://verifier:8001/verify",
                json={"config": new_config, "intent": req.intent, "policy_type": "reachability"}
            )
            verify_result = verify_resp.json()

            mininet_resp = await http.post(
                "http://mininet-runner:8004/validate",
                json={"config": new_config, "intent": req.intent}
            )
            mininet_result = mininet_resp.json()
            connectivity = mininet_result.get("connectivity", False)

            healing_log[-1]["verify_pass"] = verify_result.get("all_pass", False)
            healing_log[-1]["connectivity"] = connectivity

            if connectivity:
                # Sucesso! Persiste no RAG como exemplo positivo
                learned = await store_in_rag(
                    http, req.intent, new_config,
                    error_type=error_type,
                    was_corrected=True,
                    attempts=attempt,
                )
                return HealResponse(
                    success=True,
                    attempts=attempt,
                    final_config=new_config,
                    healing_log=healing_log,
                    learned=learned,
                )

            # Atualiza para próxima tentativa com contexto acumulado
            req.failed_config = new_config
            req.verify_result = verify_result
            req.mininet_result = mininet_result

        # Esgotou tentativas — persiste no RAG como falha conhecida
        learned = await store_in_rag(
            http, req.intent, req.failed_config,
            error_type=req.verify_result.get("error_classification", "unknown"),
            was_corrected=False,
            attempts=MAX_ATTEMPTS,
        )
        return HealResponse(
            success=False,
            attempts=MAX_ATTEMPTS,
            final_config=req.failed_config,
            healing_log=healing_log,
            learned=learned,
        )
    finally:
        await http.aclose()


def build_corrective_prompt(
    original_intent: str,
    failed_config: dict,
    verify_result: dict,
    mininet_result: dict,
    error_type: str,
    attempt: int,
    rag_context: dict,
) -> str:
    """
    Constrói prompt de correção direcionado ao tipo de erro detectado.
    Cada tipo de erro tem uma estratégia diferente — isso é o núcleo do self-healing.
    """
    failed_checks = [
        c for c in verify_result.get("checks", []) if not c.get("passed", True)
    ]
    conflicts = verify_result.get("conflicts", [])
    ping_output = mininet_result.get("ping_output", "N/A")

    base = f"""You previously generated this network configuration:
{json.dumps(failed_config, indent=2)}

For this intent: "{original_intent}"

The configuration FAILED validation. Here is what went wrong:
"""

    if error_type == "syntactic":
        strategy = """
PROBLEM TYPE: Syntactic / Format Error
- The JSON structure was malformed or contained markdown artifacts
- Failed checks: """ + "\n  - ".join(c["detail"] for c in failed_checks) + """

CORRECTION STRATEGY:
- Output ONLY valid JSON. No explanations, no ```json fences.
- Ensure all required fields are present: intent-type, source, destination, action, protocol, priority
- Do not include any text before or after the JSON object"""

    elif error_type == "terminological":
        strategy = """
PROBLEM TYPE: Terminological / Naming Error
- Interface identifiers used are incompatible with the Linux/Mininet environment
- Failed checks: """ + "\n  - ".join(c["detail"] for c in failed_checks) + """

CORRECTION STRATEGY:
- Use Linux interface names: eth0, eth1, eth2 (NOT GigabitEthernet, FastEthernet)
- Use lowercase host identifiers matching the topology (e.g., 'lyon', 'paris')
- IP prefixes must use CIDR notation: 192.168.1.0/24"""

    elif error_type == "logical":
        strategy = """
PROBLEM TYPE: Logical / Semantic Error
- The configuration has logical conflicts detected by DETOX analysis
- Conflicts found: """ + "\n  - ".join(conflicts) + """
- Connectivity test result: """ + ping_output + """

CORRECTION STRATEGY:
- Resolve the listed conflicts before generating
- Ensure no rule shadows another with contradictory action
- For waypoint policies: verify the waypoint router is reachable on the path
- For load-balancing: ensure weights sum to exactly 100"""

    else:
        strategy = """
PROBLEM TYPE: Dataplane Connectivity Failure
- Configuration passed syntax and conformance checks but failed in Mininet
- Ping test output: """ + ping_output + """

CORRECTION STRATEGY:
- Verify the next-hop gateway is adjacent to the source host
- Ensure routing table entries are logically consistent
- Check for missing intermediate routes in multi-hop paths"""

    # Adiciona exemplos corretos do RAG
    rag_examples = ""
    if rag_context.get("examples"):
        rag_examples = "\n\nCORRECT EXAMPLES (use as reference):\n"
        for ex in rag_context["examples"][:2]:
            rag_examples += f"Intent: {ex['intent']}\nCorrect config: {json.dumps(ex['config'], indent=2)}\n\n"

    return f"""{base}{strategy}{rag_examples}

Attempt {attempt}/{MAX_ATTEMPTS}. Generate a corrected JSON configuration for:
"{original_intent}"

Output ONLY the corrected JSON:"""


async def generate_corrected(prompt: str, model: str) -> dict:
    """Gera config corrigida via Claude."""
    model_id = "claude-sonnet-4-6"  # usa o melhor modelo para correção

    response = anthropic_client.messages.create(
        model=model_id,
        max_tokens=1024,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    # Remove markdown se presente
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    return json.loads(raw.strip())


async def store_in_rag(
    http: httpx.AsyncClient,
    intent: str,
    config: dict,
    error_type: str,
    was_corrected: bool,
    attempts: int,
) -> bool:
    """
    Persiste o resultado (falha ou sucesso após correção) no RAG.
    Isso implementa o aprendizado contínuo — cada run melhora o sistema.
    """
    try:
        await http.post(
            "http://rag-engine:8002/store",
            json={
                "intent": intent,
                "config": config,
                "error_type": error_type,
                "was_corrected": was_corrected,
                "healing_attempts": attempts,
                "label": "corrected" if was_corrected else "failed",
            }
        )
        return True
    except Exception as e:
        logger.warning(f"Não foi possível persistir no RAG: {e}")
        return False


@app.get("/health")
async def health():
    return {"status": "ok", "service": "self-healing-agent"}
