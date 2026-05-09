# Guia de Avaliação de Artefato — SBRC 2026

Este documento auxilia o **Comitê Técnico de Artefatos (CTA)** na avaliação do artefato submetido ao SBRC 2026, fornecendo instruções detalhadas para reprodução dos resultados do artigo.

---

## Informações do Artefato

| Campo | Valor |
|---|---|
| **Título do artigo** | Framework para Verificação e Validação Experimental de Configurações de Rede Geradas por LLMs |
| **Evento** | SBRC 2026 — Trilha Principal |
| **Repositório** | https://github.com/cscolomboIA/llm-network-validation-framework |
| **Licença** | MIT |
| **DOI do artigo** | *(a ser preenchido após publicação)* |

---

## Requisitos para Avaliação

### Mínimos (modo simulado — qualquer OS)
- Docker + Docker Compose 24.x, **ou** Python 3.10+
- Chave de API: **ao menos uma** entre OpenAI, Groq (gratuito), ou Anthropic
- 4 GB RAM, 2 GB disco

### Completos (validação Mininet real — Etapa 4 nativa)
- Tudo acima +
- Ubuntu 22.04 (ou 20.04)
- Mininet 2.3+ (`sudo apt install mininet`)
- Privilégios `sudo`

---

## Reprodução Rápida (≈ 10 minutos)

### Passo 1 — Clonar e configurar

```bash
git clone https://github.com/cscolomboIA/llm-network-validation-framework.git
cd llm-network-validation-framework
cp .env.example .env
```

Edite `.env` e adicione **ao menos uma** chave de API:

```env
GROQ_API_KEY=gsk_...   # Conta gratuita disponível em console.groq.com
```

### Passo 2 — Subir com Docker Compose

```bash
docker-compose up --build
```

### Passo 3 — Interagir via interface web

Abra `http://localhost:8080` e execute o pipeline com um dos exemplos abaixo:

**Exemplo 1 — Reachability (esperado: PASS)**
```
Traffic originating from lyon can reach the subnet 100.0.8.0/24.
```
- Modelo: Llama 3.3 70B
- Policy: Reachability
- Batch size: 1

**Exemplo 2 — Waypoint (maior chance de falha semântica)**
```
Traffic from amsterdam to 10.0.5.0/24 must pass through firewall1.
```
- Modelo: Llama 3.3 70B
- Policy: Waypoint
- Batch size: 1

**Exemplo 3 — Teste do limiar crítico de batch size**
- Use o mesmo intent do Exemplo 1
- Mova o slider de Batch Size para 25 (observe o aviso ⚠)
- Observe a degradação na qualidade da configuração gerada

### Passo 4 — Verificar via API (opcional)

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "intent": "Traffic originating from lyon can reach the subnet 100.0.8.0/24.",
    "model": "llama-3.3-70b",
    "policy_type": "reachability",
    "batch_size": 1
  }' | python3 -m json.tool
```

---

## Correspondência com os Resultados do Artigo

| Figura/Tabela no artigo | Como reproduzir | Arquivo de saída |
|---|---|---|
| **Fig. 2** — Acurácia Sintática vs Batch Size | `python scripts/run_benchmark.py --models llama-3.3-70b --batch_sizes 1 3 5 9 20 33 50 100` | `results/benchmark.json` |
| **Fig. 3** — Mapa de calor de executabilidade | `python scripts/run_benchmark.py` (todos os modelos/políticas) | `results/benchmark.json` |
| **Tab. 2** — Exemplo completo do pipeline | Interface web com o intent do Exemplo 1 | Resultado visual na UI |
| **Tab. 3** — Modelos destaque por política | Comparar `operational_acc` no JSON de resultado | `results/benchmark.json` |

> **Nota sobre reprodutibilidade**: os modelos proprietários (GPT-4, Claude, Gemini) podem produzir resultados ligeiramente diferentes dos do artigo devido a atualizações de versão e diferenças de temperatura. O protocolo `temperature=0.0` minimiza, mas não elimina, essa variação.

---

## Checklist dos Selos SBRC

| Critério | Status | Evidência |
|---|---|---|
| **Disponível** — artefato acessível publicamente | ✅ | GitHub público + MIT License |
| **Funcional** — executa sem erros | ✅ | Docker Compose + instruções verificadas |
| **Reproduzível** — resultados consistentes com o artigo | ✅ | `scripts/run_benchmark.py` + modo simulado calibrado |
| **Reutilizável** — bem documentado e modular | ✅ | README + ARCHITECTURE.md + código comentado |

---

## Contato para Dúvidas

- **Cristiano Colombo** — [cristianos@ifes.edu.br](mailto:cristianos@ifes.edu.br)
- **Issues no GitHub** — https://github.com/cscolomboIA/llm-network-validation-framework/issues
