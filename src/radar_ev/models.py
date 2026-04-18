from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class Match(BaseModel):
    id: int
    home_team: str
    away_team: str
    datetime: datetime
    league: str
    referee: Optional[str] = None
    home_lineup_confirmed: bool = False
    away_lineup_confirmed: bool = False
    motivation_index: float = Field(0.0, ge=0.0, le=10.0)
    model_config = ConfigDict(from_attributes=True)

class Odds(BaseModel):
    match_id: int
    market: str
    odd_value: float = Field(..., gt=0)
    offered_by: str = "betano"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_config = ConfigDict(from_attributes=True)

class Prediction(BaseModel):
    match_id: int
    market: str
    probability: float = Field(..., ge=0.0, le=1.0)
    fair_odd: float
    model_version: str
    model_config = ConfigDict(from_attributes=True)

class Opportunity(BaseModel):
    match: Match
    market: str
    fair_odd: float
    offered_odd: float
    ev_percent: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    model_config = ConfigDict(from_attributes=True)
