"""
Validador de conformidade YANG (Bjorklund 2016)
Verifica se a config gerada pelo LLM respeita o schema NetConf.
"""

from pathlib import Path


REQUIRED_FIELDS = {"intent-type", "source", "destination", "action", "protocol", "priority"}
VALID_INTENT_TYPES = {"reachability", "waypoint", "load-balancing"}
VALID_ACTIONS = {"allow", "deny", "permit", "drop"}
VALID_PROTOCOLS = {"ipv4", "ipv6", "any"}


class YANGValidator:
    def __init__(self, schema_dir: Path):
        self.schema_dir = schema_dir

    def validate(self, config: dict) -> tuple[bool, str]:
        configs = config if isinstance(config, list) else [config]
        all_errors = []

        for i, cfg in enumerate(configs):
            prefix = f"[{i}] " if len(configs) > 1 else ""
            errors = self._validate_single(cfg, prefix)
            all_errors.extend(errors)

        if all_errors:
            return False, "; ".join(all_errors[:3])  # mostra os 3 primeiros
        return True, f"Schema válido ({len(configs)} regra(s))"

    def _validate_single(self, cfg: dict, prefix: str) -> list[str]:
        errors = []

        # Campos obrigatórios
        missing = REQUIRED_FIELDS - set(cfg.keys())
        if missing:
            errors.append(f"{prefix}Campos ausentes: {missing}")
            return errors  # sem campo obrigatório, não adianta continuar

        # intent-type
        if cfg["intent-type"] not in VALID_INTENT_TYPES:
            errors.append(f"{prefix}intent-type inválido: '{cfg['intent-type']}'")

        # action
        if cfg["action"] not in VALID_ACTIONS:
            errors.append(f"{prefix}action inválida: '{cfg['action']}'")

        # protocol
        if cfg["protocol"] not in VALID_PROTOCOLS:
            errors.append(f"{prefix}protocol inválido: '{cfg['protocol']}'")

        # priority (deve ser inteiro 1-1000)
        try:
            p = int(cfg["priority"])
            if not (1 <= p <= 1000):
                errors.append(f"{prefix}priority fora do range [1,1000]: {p}")
        except (TypeError, ValueError):
            errors.append(f"{prefix}priority deve ser inteiro: '{cfg['priority']}'")

        # waypoints só para policy waypoint
        if cfg["intent-type"] == "waypoint" and not cfg.get("waypoints"):
            errors.append(f"{prefix}Política waypoint sem waypoints definidos")

        # load-balance só para policy load-balancing
        if cfg["intent-type"] == "load-balancing" and not cfg.get("load-balance"):
            errors.append(f"{prefix}Política load-balancing sem configuração de pesos")

        return errors
