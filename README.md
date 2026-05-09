# NetValidAI — LLM Network Validation Framework

[![SBRC 2026](https://img.shields.io/badge/SBRC-2026-blue)](https://sbrc.sbc.org.br)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-brightgreen)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue)](docker-compose.yml)

> **Artigo aceito no SBRC 2026 — Trilha Principal**  
> *Framework para Verificação e Validação Experimental de Configurações de Rede Geradas por LLMs*  
> Cristiano da Silveira Colombo (UFES/IFES), Magnos Martinello (UFES)

---

## Visão Geral

Este framework implementa um **pipeline preventivo multi-estágio** para verificar e validar configurações de rede geradas por LLMs antes de sua implantação em produção. A motivação central é demonstrar que **validade sintática não é condição suficiente** para garantir conectividade operacional.

```
Intenção (linguagem natural)
        ↓
  [Etapa 1] Verificação Sintática  →  JSON bem-formado?
        ↓
  [Etapa 2] Conformidade YANG      →  Schema e endereçamento IP válidos?
        ↓
  [Etapa 3] Verificação Semântica  →  Consistência lógica via DETOX + RAG?
        ↓
  [Etapa 4] Validação Mininet      →  Ping com 0% de perda no dataplane?
        ↓
  ✓ Configuração aprovada para implantação
```

### Principais achados do artigo

| Observação | Evidência |
|---|---|
| Ponto de inflexão crítico em **batch size = 20** | Degradação estatisticamente significativa (p < 0,05) |
| Claude 3.5 Sonnet: 100% sintático, **80% operacional** | Falso positivo da verificação sintática isolada |
| Similaridade textual ≠ corretude funcional | ECDF: Llama-3.3-70b com sim ~0,6 mas 50% de sucesso no Mininet |
| DeepSeek/Qwen-3: 0% na Política 3 | Proficiência generalista não garante corretude em infra |

---

## Estrutura do Repositório

```
llm-network-validation-framework/
├── api-gateway/          # FastAPI — ponto de entrada do pipeline
│   ├── main.py
│   ├── routers/
│   └── requirements.txt
├── verifier/             # Etapas 1–3: sintaxe, conformidade, semântica
│   ├── syntactic.py
│   ├── conformance.py
│   ├── semantic.py
│   └── detox.py
├── rag-engine/           # RAG sobre o dataset NetConfEval
│   ├── engine.py
│   └── data/
├── self-healing-agent/   # Autocorreção (até 3 tentativas)
│   └── agent.py
├── mininet-runner/       # Etapa 4: emulação e teste de conectividade
│   ├── orchestrator.py
│   └── runner.py
├── frontend/             # Interface web (index.html)
│   └── index.html
├── scripts/              # Scripts de experimento e reprodução
│   ├── run_benchmark.py
│   └── batch_stress_test.py
├── docs/                 # Documentação complementar
│   ├── ARCHITECTURE.md
│   └── RESULTS.md
├── docker-compose.yml
└── README.md
```

---

## Pré-requisitos

| Componente | Versão mínima | Observação |
|---|---|---|
| Python | 3.10 | Todos os módulos |
| Mininet | 2.3 | Apenas para Etapa 4 (Linux) |
| Ubuntu | 22.04 | Recomendado para Mininet |
| Docker + Compose | 24.x | Alternativa sem instalar dependências |
| Chave de API | — | OpenAI, Groq ou Anthropic |

---

## Instalação e Execução

### Opção A — Docker Compose (recomendado para avaliadores)

A forma mais rápida de subir o ambiente completo (sem Mininet real):

```bash
git clone https://github.com/cscolomboIA/llm-network-validation-framework.git
cd llm-network-validation-framework

# Configure as chaves de API
cp .env.example .env
# Edite .env com suas chaves (OpenAI, Groq, Anthropic)

docker-compose up --build
```

O frontend estará disponível em `http://localhost:8080` e a API em `http://localhost:8000`.

> **Nota**: sem Mininet instalado, a Etapa 4 roda em modo simulado. Para validação real, use a Opção B.

---

### Opção B — Instalação local (com Mininet real)

```bash
git clone https://github.com/cscolomboIA/llm-network-validation-framework.git
cd llm-network-validation-framework

# Instalar dependências Python
pip install -r api-gateway/requirements.txt

# Configurar ambiente
cp .env.example .env
# Edite .env com suas chaves de API

# Instalar Mininet (Ubuntu 22.04)
sudo apt-get install mininet -y

# Subir a API
cd api-gateway
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Em outro terminal: servir o frontend
cd frontend
python -m http.server 8080
```

---

## Configuração (.env)

```env
# Chaves de API dos modelos (ao menos uma obrigatória)
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
ANTHROPIC_API_KEY=sk-ant-...

# Modo de execução da Etapa 4
# "real"      → Mininet instalado localmente (Linux)
# "simulated" → retorna resultado simulado (qualquer OS)
MININET_MODE=simulated

# Caminho do dataset NetConfEval (baixado automaticamente se vazio)
NETCONFEVAL_PATH=./rag-engine/data/netconfeval.json
```

---

## Uso via Interface Web

1. Abra `http://localhost:8080` no navegador  
2. Digite uma intenção de rede no campo **Network Intent**, por exemplo:
   ```
   Traffic originating from lyon can reach the subnet 100.0.8.0/24.
   ```
3. Selecione o **modelo LLM**, o **tipo de política** e o **batch size**
4. Clique em **▶ Run Pipeline**
5. Acompanhe as 4 etapas em tempo real nos painéis de resultado

Você também pode usar o botão **"pick from NetConfEval dataset"** para selecionar amostras reais do benchmark com 1.665 intents catalogados.

---

## Uso via API (curl)

```bash
# Verificar saúde da API
curl http://localhost:8000/health

# Executar pipeline completo
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "intent": "Traffic originating from lyon can reach the subnet 100.0.8.0/24.",
    "model": "claude-3-5-sonnet",
    "policy_type": "reachability",
    "batch_size": 1
  }'

# Listar modelos disponíveis
curl http://localhost:8000/api/models

# Buscar amostras do dataset
curl "http://localhost:8000/api/dataset/samples?policy_type=reachability&limit=10"
```

### Resposta de exemplo

```json
{
  "all_pass": true,
  "stages": {
    "1_syntactic": {
      "status": "pass",
      "model": "claude-3-5-sonnet",
      "config": { "source": "lyon", "destination": "100.0.8.0/24", ... }
    },
    "2_conformance": {
      "status": "pass",
      "detail": {
        "similarity_score": 0.97,
        "conflicts": [],
        "checks": [
          { "name": "YANG Schema", "passed": true, "detail": "Valid structure" },
          { "name": "IPv4 Addressing", "passed": true, "detail": "No conflicts" },
          { "name": "Host Identifiers", "passed": true, "detail": "Normalized" },
          { "name": "DETOX — Logical conflicts", "passed": true, "detail": "No anomalies" }
        ]
      }
    },
    "4_mininet": {
      "status": "pass",
      "detail": {
        "ping_output": "3 packets transmitted, 3 received, 0% loss",
        "packet_loss": 0
      }
    }
  },
  "rag_context": {
    "ground_truth_count": 3,
    "examples": [...]
  }
}
```

---

## Reproduzindo os Experimentos do Artigo

### Benchmark completo (Task 1 do NetConfEval — 1.665 amostras)

```bash
python scripts/run_benchmark.py \
  --models claude-3-5-sonnet gpt-4-azure llama-3.3-70b mistral-large \
  --policy_types reachability waypoint load-balancing \
  --batch_sizes 1 3 5 9 20 33 50 100 \
  --output results/benchmark_$(date +%Y%m%d).json
```

> Tempo estimado: 4–8 horas dependendo dos modelos e limites de rate.

### Teste de estresse de batch size

```bash
python scripts/batch_stress_test.py \
  --model claude-3-5-sonnet \
  --policy reachability \
  --max_batch 100 \
  --step 5 \
  --output results/batch_stress.json
```

### Plotar resultados (reproduz as Figuras 2, 3 e 4 do artigo)

```bash
python scripts/plot_results.py \
  --input results/benchmark_*.json \
  --output figs/
```

---

## Módulos do Pipeline

### `verifier/syntactic.py` — Etapa 1

Isola o objeto JSON da resposta do LLM, removendo explicações textuais e delimitadores Markdown. Retorna `status: pass` apenas para JSON parseável e sem verbosidade.

### `verifier/conformance.py` + `verifier/detox.py` — Etapa 2

Valida o JSON contra o schema YANG do NetConfEval. O módulo DETOX detecta conflitos lógicos como shadowing de regras, rotas recursivas e inconsistências de endereçamento.

### `rag-engine/engine.py` — Etapa 3 (RAG)

Implementa Retrieval-Augmented Generation sobre o dataset NetConfEval. Recupera as `k` amostras mais similares (embedding por TF-IDF + cosine similarity) e calcula a `similarity_score` entre a configuração gerada e o ground truth.

### `self-healing-agent/agent.py` — Autocorreção

Quando qualquer etapa falha, o agente reformula o prompt incorporando o feedback de erro e reenvia ao LLM (até 3 tentativas). Inspirado na abordagem de [Hachimi et al. 2025], mas de forma preventiva.

### `mininet-runner/orchestrator.py` — Etapa 4

Traduz o JSON verificado em comandos Mininet. Instancia a topologia, configura as tabelas de roteamento e executa `ping -c 3` entre os hosts definidos na intenção. O indicador de sucesso é 0% de perda de pacotes.

---

## Limitações Conhecidas

- Avaliação restrita à Task 1 do NetConfEval (Reachability, Waypoint, Load-Balancing)
- Execução única por configuração (sem análise de variância estatística completa)
- Etapa 4 requer Linux com Mininet; em outros SOs funciona em modo simulado
- Custo computacional e escalabilidade não foram avaliados formalmente

---

## Trabalhos Futuros

- Mecanismo de self-healing com retroalimentação dos logs de erro do Mininet
- Integração com verificadores formais (Batfish)
- Suporte a RAG com embeddings densos (sentence-transformers)
- Extensão para Tasks 2 e 3 do NetConfEval
- Integração do pipeline a arquiteturas baseadas em agentes de IA

---

## Referências Principais

- **NetConfEval**: Dahlmann et al., ACM SIGCOMM CCR 2024  
- **DETOX**: Jesus et al., WTF/SBRC 2016  
- **Mininet**: Lantz et al., HotNets 2010  
- **Lost in the Middle**: Liu et al., TACL 2024  

---

## Citação

```bibtex
@inproceedings{colombo2026netvalidai,
  title     = {Framework para Verificação e Validação Experimental de
               Configurações de Rede Geradas por LLMs},
  author    = {Colombo, Cristiano da Silveira and Martinello, Magnos},
  booktitle = {Anais do XLIV Simpósio Brasileiro de Redes de Computadores
               e Sistemas Distribuídos (SBRC)},
  year      = {2026},
  publisher = {SBC}
}
```

---

## Licença

MIT License — veja [LICENSE](LICENSE).

## Contato

- Cristiano Colombo — [cristianos@ifes.edu.br](mailto:cristianos@ifes.edu.br)  
- Magnos Martinello — [magnos@inf.ufes.br](mailto:magnos@inf.ufes.br)
