"""
config.py — Carregamento e validação de variáveis de ambiente.

Utiliza pydantic-settings para carregar automaticamente as variáveis do arquivo .env,
validar tipos e expor a instância global `settings` para uso em todos os módulos.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from pathlib import Path


class Settings(BaseSettings):
    """Configurações centralizadas do Radar +EV.

    Todas as variáveis são carregadas automaticamente do .env.
    Variáveis obrigatórias (sem default) lançam ValidationError se ausentes.
    """

    # ---- API-Football ----
    rapidapi_key: str = Field(
        ...,
        alias="RAPIDAPI_KEY",
        description="Chave de autenticação da API-Football (api-sports.io)",
    )
    rapidapi_host: str = Field(
        "v3.football.api-sports.io",
        alias="RAPIDAPI_HOST",
        description="Host da API-Football",
    )
    # Temporada usada nas consultas de estatísticas (/teams/statistics).
    # IMPORTANTE: o plano Free da API-Football BLOQUEIA temporadas recentes
    # (200 + errors {plan: "Free plans do not have access to this season,
    # try from 2022 to 2024."}) — a temporada atual (2026) NÃO é acessível.
    # Enquanto o plano for Free, manter 2024; atualizar ao mudar de plano.
    season: int = Field(
        2024,
        alias="SEASON",
        description=(
            "Temporada histórica usada no /teams/statistics "
            "(plano Free cobre até 2024)"
        ),
    )

    # ---- The Odds API (legado, mantido por compatibilidade) ----
    odds_api_key: str = Field(
        "not_used",
        alias="ODDS_API_KEY",
        description="Chave da The Odds API (depreciado)",
    )

    # ---- Telegram ----
    telegram_token: str = Field(
        ...,
        alias="TELEGRAM_TOKEN",
        description="Token do bot do Telegram",
    )
    telegram_chat_id: str = Field(
        ...,
        alias="TELEGRAM_CHAT_ID",
        description="ID do chat/grupo no Telegram",
    )

    # ---- Limiares de Operação ----
    min_ev_percent: float = Field(
        5.0,
        alias="MIN_EV_PERCENT",
        ge=0.0,
        description="EV mínimo percentual para considerar uma oportunidade",
    )
    pre_match_hours: int = Field(
        4,
        alias="PRE_MATCH_HOURS",
        ge=1,
        description="Janela de horas antes do jogo para análise",
    )

    # ---- Cálculo de EV ----
    vig_removal_method: str = Field(
        "proportional",
        alias="VIG_REMOVAL_METHOD",
        description=(
            "Método de remoção do overround (vig) da casa antes de comparar "
            "com a probabilidade do modelo (proportional)"
        ),
    )

    # ---- Gestão de Risco (Stake) ----
    # Fração do Kelly completo usada na stake. 1/4 Kelly: drawdown esperado
    # ~50% menor que o Kelly completo, sacrificando apenas ~25% do crescimento
    # teórico — proteção contra superestimativa do modelo (model mispricing) e
    # variância nos primeiros meses de operação.
    kelly_fraction: float = Field(
        0.25,
        alias="KELLY_FRACTION",
        gt=0.0,
        le=1.0,
        description=(
            "Fração do Kelly completo aplicada à stake. 1/4 Kelly reduz o "
            "drawdown esperado em ~50% com perda de apenas ~25% do crescimento "
            "teórico (gestão de risco conservadora)"
        ),
    )

    # ---- Monitoramento de Vig (taxa de fallback de pares Over/Under) ----
    # FALLBACK_WARNING_THRESHOLD: 20% é um chute inicial. Revisar após a
    # primeira execução real em produção. Se a Betano publicar Under para
    # <80% dos mercados estruturalmente, aumentar o threshold.
    fallback_warning_threshold: float = Field(
        20.0,
        alias="FALLBACK_WARNING_THRESHOLD",
        ge=0.0,
        le=100.0,
        description=(
            "% mínimo de avaliações de EV que caíram em fallback (sem par "
            "Over/Under) para disparar o alerta de coleta"
        ),
    )
    fallback_alert_level: str = Field(
        "ERROR",
        alias="FALLBACK_ALERT_LEVEL",
        description=(
            "Nível do log de alerta de fallback: 'ERROR' ou 'INFO'. Em modo "
            "mock o alerta é sempre INFO (dado sintético sem pares)"
        ),
    )

    # ---- Resolução de Resultados (result_resolver) ----
    # Percentual de RESOLVED com result_won IS NULL que dispara WARNING.
    result_resolution_null_threshold: float = Field(
        5.0,
        alias="RESULT_RESOLUTION_NULL_THRESHOLD",
        ge=0.0,
        le=100.0,
        description=(
            "% máximo aceitável de resoluções indeterminadas (NULL) sobre o "
            "total de RESOLVED antes de emitir WARNING (cancelamentos/"
            "adiamentos/dados incompletos)"
        ),
    )

    # ---- Whitelist de Mercados (Range de Sanity) ----
    # Mercados fora destes limites são rejeitados antes de qualquer chamada de API.
    market_corners_min: float = Field(
        2.5,
        alias="MARKET_CORNERS_MIN",
        description="Limiar mínimo aceito para escanteios",
    )
    market_corners_max: float = Field(
        15.5,
        alias="MARKET_CORNERS_MAX",
        description="Limiar máximo aceito para escanteios",
    )
    market_cards_min: float = Field(
        1.5,
        alias="MARKET_CARDS_MIN",
        description="Limiar mínimo aceito para cartões",
    )
    market_cards_max: float = Field(
        8.5,
        alias="MARKET_CARDS_MAX",
        description="Limiar máximo aceito para cartões",
    )
    market_goals_min: float = Field(
        0.5,
        alias="MARKET_GOALS_MIN",
        description="Limiar mínimo aceito para gols",
    )
    market_goals_max: float = Field(
        5.5,
        alias="MARKET_GOALS_MAX",
        description="Limiar máximo aceito para gols",
    )

    # ---- Regras de Negócio ----
    # Armazenado como string bruta para evitar erro de JSON parsing do pydantic-settings.
    # Use a propriedade `derby_teams_list` para obter a lista parseada.
    derby_teams: str = Field(
        "Flamengo,Fluminense,Boca Juniors,River Plate",
        alias="DERBY_TEAMS",
        description="Lista de times para Derby Mode, separados por vírgula",
    )

    # ---- Modelo ----
    model_path: str = Field(
        "models/poisson_corners.pkl",
        alias="MODEL_PATH",
        description="Caminho para o modelo Poisson serializado (futuro)",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def derby_teams_list(self) -> List[str]:
        """Retorna derby_teams como lista de strings parseada.

        Suporta formatos:
        - CSV: "Flamengo,Fluminense"
        - JSON-like: '["Flamengo","Fluminense"]'
        """
        if not self.derby_teams:
            return []
        cleaned = self.derby_teams.strip("[]\"'")
        return [item.strip().strip("\"'") for item in cleaned.split(",") if item.strip()]


# Instância global — importar em qualquer módulo com `from radar_ev.config import settings`
settings = Settings()
