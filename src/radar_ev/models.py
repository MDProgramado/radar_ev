"""
models.py — Entidades de domínio do Radar +EV.

Define as quatro entidades principais usando Pydantic v2:
- Match: partida de futebol com metadados e IDs para consulta de estatísticas.
- Odds: odd de um mercado específico oferecida por uma casa de apostas.
- Prediction: predição do modelo (probabilidade real e odd justa).
- Opportunity: oportunidade +EV aprovada pelas regras de negócio.

Todas utilizam ConfigDict(from_attributes=True) para permitir criação a partir de
dicionários e ORMs.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Match(BaseModel):
    """Representa uma partida de futebol com todos os metadados necessários.

    Campos adicionais (home_team_id, away_team_id, league_id, season) são
    necessários para consultar estatísticas via API-Football.
    """

    id: int
    home_team: str
    away_team: str
    datetime: datetime
    league: str
    referee: Optional[str] = None
    home_lineup_confirmed: bool = False
    away_lineup_confirmed: bool = False
    motivation_index: float = Field(
        0.0,
        ge=0.0,
        le=10.0,
        description="Índice de motivação do jogo (0=irrelevante, 10=decisivo)",
    )

    # Campos para suporte ao modelo Poisson (preenchidos pelo collector)
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    league_id: Optional[int] = None
    season: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class Odds(BaseModel):
    """Odd de um mercado específico oferecida por uma casa de apostas.

    O campo `market` segue a convenção: `tipo_over/under_valor`
    Ex: corners_over_9.5, cards_over_4.5, shots_over_10.5
    """

    match_id: int
    market: str
    odd_value: float = Field(..., gt=0, description="Valor da odd decimal")
    offered_by: str = "betano"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(from_attributes=True)


class Prediction(BaseModel):
    """Predição gerada pelo modelo estatístico.

    Contém a probabilidade real estimada e a odd justa (fair_odd = 1/probabilidade).
    """

    match_id: int
    market: str
    probability: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Probabilidade real estimada pelo modelo",
    )
    fair_odd: float = Field(..., gt=0, description="Odd justa = 1/probabilidade")
    model_version: str = Field(
        ...,
        description="Versão do modelo que gerou a predição (ex: poisson_v1)",
    )

    model_config = ConfigDict(from_attributes=True)


class Opportunity(BaseModel):
    """Oportunidade de aposta com EV positivo, aprovada pelas regras de negócio.

    Esta é a entidade final enviada ao Telegram e registrada no log.
    """

    match: Match
    market: str
    fair_odd: float = Field(..., gt=0)
    offered_odd: float = Field(..., gt=0)
    ev_percent: float = Field(..., description="Valor esperado percentual")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confiança do modelo (probabilidade real)",
    )
    reasoning: str = Field(
        ...,
        description="Justificativa/motivo da aprovação",
    )
    recommended_stake_percent: float = Field(
        0.0,
        description="Porcentagem sugerida da banca (Critério de Kelly Fracionário)",
    )

    model_config = ConfigDict(from_attributes=True)

    @property
    def summary(self) -> str:
        """Resumo formatado da oportunidade para logs."""
        return (
            f"{self.match.home_team} vs {self.match.away_team} | "
            f"{self.market} | EV={self.ev_percent:.1f}% | "
            f"Odd: {self.offered_odd:.2f} → Justa: {self.fair_odd:.2f}"
        )
