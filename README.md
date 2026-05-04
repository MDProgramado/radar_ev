# 🎯 Radar +EV

> Sistema de recomendação pré-jogo para identificação de apostas com valor esperado positivo (+EV) em mercados estatísticos de futebol.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Poetry](https://img.shields.io/badge/poetry-2.3-purple.svg)](https://python-poetry.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 📋 Visão Geral

O Radar +EV analisa mercados estatísticos de futebol (escanteios, cartões) usando:

1. **API-Football** — dados de jogos, odds da Betano, estatísticas históricas
2. **Modelo Poisson** — calcula probabilidade real de eventos (ex: total escanteios > 9.5)
3. **Comparação de odds** — odd justa (1/probabilidade) vs odd oferecida pela Betano
4. **Regras de negócio** — motivação, escalação, derby mode, janela pré-match
5. **Alertas Telegram** — oportunidades aprovadas são enviadas ao usuário

## 🚀 Instalação

```bash
# Clone o repositório
git clone <repo-url>
cd radar_ev

# Instale dependências com Poetry
poetry install

# Copie e configure o .env
cp .env.example .env
# Edite .env com suas credenciais
```

## ⚡ Uso Rápido

```bash
# Modo mock (sem API real, para teste)
poetry run python -m radar_ev.orchestrator --mock

# Modo real (requer chaves válidas no .env)
poetry run python -m radar_ev.orchestrator

# Testar conectividade com a API
poetry run python test_api.py
```

## 🧪 Testes

```bash
# Rodar todos os testes
poetry run pytest

# Com cobertura
poetry run pytest --cov=radar_ev --cov-report=term-missing

# Apenas um módulo
poetry run pytest tests/test_predictor.py -v
```

## 🏗️ Arquitetura

```
[Agendador Cron] → [Orquestrador] → [FootballAPICollector]
                                   → [Predictor (Poisson)]
                                   → [RulesEngine]
                                   → [TelegramSender]
```

### Estrutura de Diretórios

```
radar_ev/
├── .env.example            # Template de variáveis de ambiente
├── pyproject.toml           # Configuração Poetry
├── Dockerfile               # Deploy containerizado
├── test_api.py              # Script de teste de conectividade
├── src/radar_ev/
│   ├── config.py            # Carrega .env via pydantic-settings
│   ├── models.py            # Match, Odds, Prediction, Opportunity
│   ├── http_client.py       # Cliente HTTP com retry e logging
│   ├── ev_calculator.py     # Cálculos de valor esperado
│   ├── rules.py             # Motor de regras RN01–RN05
│   ├── predictor.py         # Modelo Poisson (escanteios + cartões)
│   ├── mock_data.py         # Dados fictícios para testes
│   ├── orchestrator.py      # Pipeline principal
│   ├── logger.py            # Configuração do structlog
│   ├── alert/telegram.py    # Envio via Telegram
│   └── collectors/
│       ├── football_api.py  # Wrapper da API-Football
│       └── odds_collector.py # (depreciado)
└── tests/                   # Testes unitários e de integração
```

## 📊 Modelo Poisson

**Premissa:** O número de escanteios/cartões segue uma distribuição de Poisson.

```
λ_total = média_favor_mandante + média_contra_visitante
P(total > 9.5) = 1 – Σ P(X=k) para k=0..9
Odd justa = 1 / P(total > 9.5)
EV% = (odd_justa - odd_oferecida) / odd_oferecida × 100
```

## 🐳 Docker

```bash
docker build -t radar-ev .
docker run --env-file .env radar-ev
docker run --env-file .env radar-ev python -m radar_ev.orchestrator --mock
```

## 📅 Agendamento (Cron)

```cron
*/30 * * * * cd /opt/radar_ev && docker run --rm --env-file .env radar-ev >> /var/log/radar_ev.log 2>&1
```

## ⚙️ Configuração (.env)

| Variável | Descrição | Default |
|----------|-----------|---------|
| `RAPIDAPI_KEY` | Chave API-Football | *obrigatório* |
| `RAPIDAPI_HOST` | Host da API | `v3.football.api-sports.io` |
| `TELEGRAM_TOKEN` | Token do bot Telegram | *obrigatório* |
| `TELEGRAM_CHAT_ID` | ID do chat/grupo | *obrigatório* |
| `MIN_EV_PERCENT` | EV mínimo (%) | `5.0` |
| `PRE_MATCH_HOURS` | Janela pré-jogo (horas) | `4` |
| `DERBY_TEAMS` | Times derby (CSV) | `Flamengo,Fluminense,...` |

## 📝 Regras de Negócio

- **RN01** — Motivação ≥ 7.0 (jogos decisivos)
- **RN02** — Escalação confirmada (mercados de gols)
- **RN03** — Derby mode (bloqueia gols, permite cartões)
- **RN04** — Bet Builders (placeholder)
- **RN05** — Apenas pré-jogo

---

**Autor:** Maicon Douglas | **Versão:** 1.0.0
