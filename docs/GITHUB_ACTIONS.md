# Radar +EV — GitHub Actions

Este documento descreve o workflow `.github/workflows/pipeline.yml`, que roda o
pipeline `src/radar_ev/orchestrator.py` de forma automática 3x ao dia e de forma
manual sob demanda.

---

## 1. Visão geral e lógica de agendamento

O workflow tem **duas lógicas em horários distintos**:

| Horário (UTC) | Horário (BRT, UTC-3) | Tarefa | Comando |
|---|---|---|---|
| 12:00 e 18:00 | 09:00 e 15:00 | **Buscar + prever** (matches → odds → Poisson → EV → Telegram) | `python .../orchestrator.py` (sem flag) |
| 23:00 | 20:00 | **Resolver resultados** (PENDING → GREEN/RED → extrato no Telegram) | `python .../orchestrator.py --resolve` |

> **Por quê?** O modo single-shot **não resolve** por padrão (só o `--daemon`
> resolve em ciclo). Separar em dois crons garante que a resolução aconteça
> diariamente sem misturar as lógicas — o fetch de 12h/18h nunca resolve e o
> job das 23h nunca busca jogos.

| Item | Valor |
|---|---|
| Workflow | Radar +EV pipeline |
| Schedule | `0 12,18 * * *` (fetch) e `0 23 * * *` (resolve), sempre em **UTC** |
| Runner | `ubuntu-latest` |
| Python | 3.12 (igual ao `pyproject.toml`: `^3.12`) |
| Timeout | 10 minutos por job |
| Execução manual | `workflow_dispatch` com input `TASK` (`fetch` ou `resolve`) |

**A escolha da tarefa no cron** é feita por `github.event.schedule`: o YAML
compara o cron que disparou a run (`'0 23 * * *'` → `--resolve`; caso contrário
→ sem flag). Execuções via `workflow_dispatch` usam o input `TASK`.

> ⚠️ **Fuso**: o cron do GitHub Actions é sempre **UTC**.

---

## 2. Secrets obrigatórios

Crie todos em **Settings → Secrets and variables → Actions → New repository
secret** (ou em **Organization/Repository** se usar Actions de organização).

| Secret | Obrigatório | Onde gerar | Observação |
|---|---|---|---|
| `RAPIDAPI_KEY` | Sim | [api-sports.io](https://dashboard.api-football.com/) → **Account** → **Sport Plan** → copie a chave | Chave da API-Football (namespace `v3.football.api-sports.io`) |
| `TELEGRAM_TOKEN` | Sim | [@BotFather](https://t.me/BotFather) → `/newbot` → o token é exibido na resposta | Token do bot usado para enviar as oportunidades |
| `TELEGRAM_CHAT_ID` | Sim | Envie `/start` para o seu bot e converse com [@userinfobot](https://t.me/userinfobot) → o ID numérico aparece na resposta | Chat/grupo que receberá as recomendações |
| `TURSO_DATABASE_URL` | Sim | Terminal: `turso db create radar` → depois `turso db show radar` | URL tipo `libsql://radar-<org>.turso.io` (o código aceita `https://` ou `libsql://`) |
| `TURSO_AUTH_TOKEN` | Sim | Terminal: `turso db tokens create radar` | Token de acesso à base remota do Turso |
| `ODDS_API_KEY` | Não | Legado (The Odds API), não usado mais | Mantido por compatibilidade; pode usar qualquer valor |

### Instalação do Turso CLI (para gerar os 2 secrets Turso)

```bash
curl -sSf https://get.turso.tech/install.sh | bash
turso auth login
turso db create radar
turso db show radar          # → copie a URL para TURSO_DATABASE_URL
turso db tokens create radar # → copie o token para TURSO_AUTH_TOKEN
```

### RAPIDAPI_HOST (opcional — leia antes)

O código tem default correto (`v3.football.api-sports.io`), então o workflow
**não** injeta esse secret. Se precisar customizar o host, adicione a linha
`RAPIDAPI_HOST: ${{ secrets.RAPIDAPI_HOST }}` ao passo de execução do YAML e
crie o respectivo secret.

> ⚠️ **Armadilha do secret vazio**: no GitHub Actions um secret que **não
> existe** resolve para *string vazia*, e uma env var vazia sobrescreve o
> default do pydantic. Por isso `RAPIDAPI_HOST` fica de fora — injetado como
> vazio ele quebraria a chamada à API. Os secrets acima são seguros: se
> `RAPIDAPI_KEY`/`TELEGRAM_*` faltarem, o job falha no import (aviso claro);
> se `TURSO_*` vierem vazios, o código cai para SQLite local automaticamente.

---

## 3. Como rodar manualmente (workflow_dispatch)

1. Abra a aba **Actions** do repositório.
2. No menu lateral esquerdo, clique em **Radar +EV pipeline**.
3. Botão **Run workflow** (canto superior direito).
4. Em **TASK**, escolha:
   - **fetch** — roda a busca + previsão (mesma lógica das 12h/18h).
   - **resolve** — roda só a resolução de resultados (mesma lógica das 23h).
5. Confirme a branch (`main`) e clique em **Run workflow**.

> **Não existe botão com "--mock" no Actions**: o workflow roda **modo real**
> (APIs + Telegram + Turso). Para um smoke test offline, rode localmente (ver
> seção 5).

---

## 4. Como ver os logs

1. Abra a aba **Actions** → clique na execução (mais recente).
2. No job `run-pipeline`, expanda cada step. O step executado depende do
   horário:
   - **Resolve resultados (23h UTC ou manual 'resolve')** — saída do `--resolve`.
   - **Buscar e prever (12h/18h UTC ou manual 'fetch')** — saída do pipeline.
     (O step não executado aparece como `Skipped` no log.)
3. No log, procure por:
   - Linhas em vermelho / `ERROR` (structlog) — ex.: `vig_fallback_alert`.
   - `💥 ERRO CRÍTICO:` — exceção não tratada no pipeline.
   - `📋 RESUMO: N oportunidade(s)` (fetch) ou `✅ Resolvidas:` (resolve) —
     execução concluída com sucesso.

Para baixar o log completo: botão **…** (no topo da página do run) →
**Download log archive**.

---

## 5. Como debugar quando falhar

### Diagnóstico rápido por sintoma

| Sintoma | Causa provável | Correção |
|---|---|---|
| Job falha em segundos com `ValidationError` no import | Um secret obrigatório não foi criado (`RAPIDAPI_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`) | Crie o secret listado na seção 2 e rode de novo via `workflow_dispatch` |
| `ModuleNotFoundError: No module named 'radar_ev.config'` | `PYTHONPATH` removido do step de execução (o `--no-root` não instala o pacote) | Reinsira `PYTHONPATH: ${{ github.workspace }}/src` no env do job |
| A resolução de resultados nunca roda | Cron único no schedule (o single-shot não resolve por padrão) | Use os dois crons `0 12,18 * * *` + `0 23 * * *` com o step condicional `--resolve` na meia-noite 23 UTC |
| Pipeline roda, mas "perde" o histórico a cada execução | `TURSO_*` vazios → fallback para SQLite local do runner (efêmero) | Configure os 2 secrets Turso (seção 2) |
| `Rate limit atingido` repetido e pipeline para | Limite do plano free da API-Football (~100 req/dia) | Verifique a cota no dashboard; aumente intervalo `RATE_LIMIT_DELAY` ou plano |
| Telegram não envia (mas pipeline conclui) | Bot/token inválidos ou chat_id errado | O erro não quebra o job (é tratado); confira `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` |

### Reproduzindo localmente

O comando do CI com as variáveis do `.env`:

```bash
$env:PYTHONPATH = "$PWD\src"                    # Windows PowerShell
export PYTHONPATH="$PWD/src"                    # Linux/macOS
poetry run python src/radar_ev/orchestrator.py --mock    # smoke teste do fetch (offline)
poetry run python src/radar_ev/orchestrator.py --resolve # roda SÓ a resolução (modo real)
```

> O `--no-root` do CI não instala o `radar_ev`, então o `PYTHONPATH=src` é
> obrigatório para `python src/radar_ev/orchestrator.py` importar o pacote —
> use sempre que reproduzir o comando do workflow fora de um `poetry install`
> completo.

### Outras dicas

- **Re-run**: na página do run, **Re-run jobs** reexecuta todos os steps (idempotente).
- **Logs de secrets**: o GitHub mascara automaticamente valores de secrets nos logs; nunca cole um secret em log/issue.
- **Sobposição de runs**: há `concurrency` no workflow; uma run manual aguarda a agendada terminar.
- **Minutos do free tier**: 3 runs/dia × ~4–10 min ≈ 360–900 min/mês. O timeout de 10 min trava jobs perdidos consumindo minutos. Para acelerar o install (economizar ~1–2 min/run), troque o step por:
  ```bash
  poetry install --no-root --only main   # pula pytest, ruff, mypy
  ```