"""
DETOX — Detecção de inconsistências na política de rede
Baseado em: Jesus, Martinello & Zambon (2016)
"DETOX: Detecção de inconsistências na política de segurança implementada em firewall real"

Detecta: shadowing, redundância, correlação, generalização, sobreposição
"""

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("detox")


@dataclass
class PolicyRule:
    """Representação normalizada de uma regra de política."""
    source: str
    destination: str
    action: str
    priority: int
    protocol: str = "ipv4"
    waypoints: list[str] = None
    raw: dict = None

    def __post_init__(self):
        self.waypoints = self.waypoints or []


class DetoxDetector:
    """
    Implementação dos algoritmos de detecção de conflitos do DETOX.
    
    Tipos de anomalia detectados:
    - SHADOW: regra nunca executada por ser coberta por outra de maior prioridade
    - REDUNDANCY: regras com mesma ação e alcançabilidade equivalente
    - CORRELATION: regras que se contradizem para alguns fluxos
    - GENERALIZATION: uma regra é subconjunto lógico de outra com ação diferente
    - RECURSIVE_ROUTE: destino que referencia a si mesmo criando loop
    """

    def analyze(self, config: dict, policy_type: str) -> list[str]:
        """
        Analisa uma configuração gerada pelo LLM e retorna lista de conflitos.
        Retorna lista vazia se a configuração é consistente.
        """
        conflicts = []

        try:
            rules = self._extract_rules(config, policy_type)
        except Exception as e:
            logger.warning(f"Não foi possível extrair regras para análise DETOX: {e}")
            return []

        if len(rules) < 2:
            return []   # Precisa de ≥2 regras para detectar conflitos

        # Aplica cada algoritmo de detecção
        conflicts.extend(self._detect_shadowing(rules))
        conflicts.extend(self._detect_redundancy(rules))
        conflicts.extend(self._detect_correlation(rules))
        conflicts.extend(self._detect_recursive_routes(rules))

        if policy_type == "waypoint":
            conflicts.extend(self._detect_waypoint_conflicts(rules))

        if policy_type == "load-balancing":
            conflicts.extend(self._detect_lb_weight_errors(config))

        return conflicts

    def _extract_rules(self, config: dict, policy_type: str) -> list[PolicyRule]:
        """Normaliza a config do LLM em PolicyRules comparáveis."""
        rules = []

        # Config pode ser objeto único ou array (batch)
        configs = config if isinstance(config, list) else [config]

        for i, cfg in enumerate(configs):
            rules.append(PolicyRule(
                source=cfg.get("source", "any"),
                destination=cfg.get("destination", "0.0.0.0/0"),
                action=cfg.get("action", "allow"),
                priority=cfg.get("priority", 100 - i),  # prioridade decrescente no batch
                protocol=cfg.get("protocol", "ipv4"),
                waypoints=cfg.get("waypoints", []),
                raw=cfg,
            ))

        return sorted(rules, key=lambda r: r.priority, reverse=True)

    def _detect_shadowing(self, rules: list[PolicyRule]) -> list[str]:
        """
        SHADOW: Regra R2 é sombreada por R1 se:
        - R1 tem maior prioridade que R2
        - R1 abrange todo o espaço de endereços de R2
        - R1 e R2 têm ações diferentes (shadowamento relevante) ou iguais (redundância)
        """
        conflicts = []
        for i, r1 in enumerate(rules):
            for r2 in rules[i+1:]:
                if self._covers(r1.destination, r2.destination):
                    if r1.action != r2.action:
                        conflicts.append(
                            f"SHADOW: regra '{r2.source}→{r2.destination}' (action={r2.action}) "
                            f"sombreada por '{r1.source}→{r1.destination}' (action={r1.action}, "
                            f"prioridade {r1.priority})"
                        )
        return conflicts

    def _detect_redundancy(self, rules: list[PolicyRule]) -> list[str]:
        """
        REDUNDANCY: Duas regras com mesma ação e mesmo espaço de endereços.
        Indica batch dropout — o LLM duplicou uma regra.
        """
        conflicts = []
        for i, r1 in enumerate(rules):
            for r2 in rules[i+1:]:
                if (r1.source == r2.source and
                        r1.destination == r2.destination and
                        r1.action == r2.action and
                        r1.waypoints == r2.waypoints):
                    conflicts.append(
                        f"REDUNDANCY: regras duplicadas para '{r1.source}→{r1.destination}' "
                        f"(prioridades {r1.priority} e {r2.priority}) — possível batch dropout"
                    )
        return conflicts

    def _detect_correlation(self, rules: list[PolicyRule]) -> list[str]:
        """
        CORRELATION: Regras que se contradizem para fluxos na interseção.
        Diferente de SHADOW: nenhuma cobre completamente a outra.
        """
        conflicts = []
        for i, r1 in enumerate(rules):
            for r2 in rules[i+1:]:
                if (r1.action != r2.action and
                        self._overlaps(r1.destination, r2.destination) and
                        not self._covers(r1.destination, r2.destination) and
                        not self._covers(r2.destination, r1.destination)):
                    conflicts.append(
                        f"CORRELATION: regras '{r1.destination}' (allow) e "
                        f"'{r2.destination}' (deny) têm interseção não resolvida"
                    )
        return conflicts

    def _detect_recursive_routes(self, rules: list[PolicyRule]) -> list[str]:
        """
        RECURSIVE_ROUTE: Destino que inclui o próprio source — cria loop de roteamento.
        """
        conflicts = []
        for r in rules:
            try:
                dest_net = ipaddress.ip_network(r.destination, strict=False)
                src_addr = ipaddress.ip_address(r.source) if not r.source.endswith("/") else None
                if src_addr and src_addr in dest_net:
                    conflicts.append(
                        f"RECURSIVE_ROUTE: source '{r.source}' está contido em "
                        f"destination '{r.destination}' — rota recursiva impossível"
                    )
            except ValueError:
                pass  # source não é IP válido (nome de host) — ok
        return conflicts

    def _detect_waypoint_conflicts(self, rules: list[PolicyRule]) -> list[str]:
        """Detecta waypoints que contradizem a política de alcançabilidade."""
        conflicts = []
        for r in rules:
            if r.waypoints and r.action == "deny":
                conflicts.append(
                    f"WAYPOINT_DENY: regra deny com waypoints definidos — "
                    f"waypoints são irrelevantes para tráfego negado: {r.waypoints}"
                )
        return conflicts

    def _detect_lb_weight_errors(self, config: dict) -> list[str]:
        """Detecta pesos de load-balancing que não somam 100%."""
        conflicts = []
        lb = config.get("load-balance", {})
        weights = lb.get("weights", {})
        if weights:
            total = sum(weights.values())
            if abs(total - 100) > 1:   # tolerância de 1%
                conflicts.append(
                    f"LB_WEIGHT_ERROR: pesos de load-balancing somam {total}% "
                    f"(esperado: 100%) — {weights}"
                )
        return conflicts

    # =========================================================================
    # Helpers de comparação de prefixos IP
    # =========================================================================

    def _covers(self, net_a: str, net_b: str) -> bool:
        """Retorna True se net_a contém completamente net_b."""
        try:
            a = ipaddress.ip_network(net_a, strict=False)
            b = ipaddress.ip_network(net_b, strict=False)
            return b.subnet_of(a)
        except (ValueError, TypeError):
            return net_a == net_b

    def _overlaps(self, net_a: str, net_b: str) -> bool:
        """Retorna True se net_a e net_b têm interseção."""
        try:
            a = ipaddress.ip_network(net_a, strict=False)
            b = ipaddress.ip_network(net_b, strict=False)
            return a.overlaps(b)
        except (ValueError, TypeError):
            return False
