from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    rapidapi_key: str = Field(..., alias="RAPIDAPI_KEY")
    rapidapi_host: str = Field("api-football-v1.p.rapidapi.com", alias="RAPIDAPI_HOST")
    odds_api_key: str = Field(..., alias="ODDS_API_KEY")
    telegram_token: str = Field(..., alias="TELEGRAM_TOKEN")
    telegram_chat_id: str = Field(..., alias="TELEGRAM_CHAT_ID")
    min_ev_percent: float = Field(5.0, alias="MIN_EV_PERCENT")
    pre_match_hours: int = Field(4, alias="PRE_MATCH_HOURS")
    derby_teams: List[str] = Field(
        ["Flamengo", "Fluminense", "Boca Juniors", "River Plate"],
        alias="DERBY_TEAMS"
    )
    model_path: str = Field("models/poisson_corners.pkl", alias="MODEL_PATH")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("derby_teams", mode="before")
    @classmethod
    def parse_derby_teams(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

settings = Settings()
