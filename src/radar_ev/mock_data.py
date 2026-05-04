"""
mock_data.py — Dados sintéticos para testes offline.

Fornece partidas, odds e predições fictícias para uso quando `use_mock=True`.
Permite testar o pipeline completo sem depender de chamadas reais à API.
"""

from datetime import datetime, timedelta, timezone

from radar_ev.models import Match, Odds, Prediction


def get_mock_matches() -> list[Match]:
    """Retorna uma lista de partidas fictícias para teste.

    Inclui cenários variados:
    - Derby (Flamengo vs Fluminense) com alta motivação.
    - Jogo normal (Palmeiras vs Corinthians) com motivação média.
    - Jogo de liga europeia (Barcelona vs Real Madrid) — derby europeu.
    - Jogo com dados de equipe completos para teste do Poisson.
    """
    now = datetime.now(timezone.utc)

    matches = [
        Match(
            id=1001,
            home_team="Flamengo",
            away_team="Fluminense",
            datetime=now + timedelta(hours=2),
            league="Brasileirão",
            referee="Wilton Sampaio",
            home_lineup_confirmed=True,
            away_lineup_confirmed=True,
            motivation_index=8.5,
            home_team_id=127,
            away_team_id=124,
            league_id=71,
            season=2025,
        ),
        Match(
            id=1002,
            home_team="Palmeiras",
            away_team="Corinthians",
            datetime=now + timedelta(hours=3),
            league="Brasileirão",
            referee="Raphael Claus",
            home_lineup_confirmed=True,
            away_lineup_confirmed=True,
            motivation_index=9.0,
            home_team_id=121,
            away_team_id=131,
            league_id=71,
            season=2025,
        ),
        Match(
            id=1003,
            home_team="Barcelona",
            away_team="Real Madrid",
            datetime=now + timedelta(hours=4),
            league="La Liga",
            referee="Mateu Lahoz",
            home_lineup_confirmed=True,
            away_lineup_confirmed=True,
            motivation_index=9.5,
            home_team_id=529,
            away_team_id=541,
            league_id=140,
            season=2025,
        ),
        Match(
            id=1004,
            home_team="Internacional",
            away_team="Grêmio",
            datetime=now + timedelta(hours=2, minutes=30),
            league="Brasileirão",
            referee="Anderson Daronco",
            home_lineup_confirmed=False,
            away_lineup_confirmed=True,
            motivation_index=7.5,
            home_team_id=119,
            away_team_id=130,
            league_id=71,
            season=2025,
        ),
    ]
    return matches


def get_mock_odds(match_id: int) -> list[Odds]:
    """Retorna odds fictícias para um jogo.

    Gera odds para múltiplos mercados (escanteios, cartões) com valores
    que simulam cenários de EV positivo e negativo.
    """
    now = datetime.now(timezone.utc)

    return [
        Odds(
            match_id=match_id,
            market="corners_over_9.5",
            odd_value=1.85,
            offered_by="betano",
            timestamp=now,
        ),
        Odds(
            match_id=match_id,
            market="corners_over_10.5",
            odd_value=2.40,
            offered_by="betano",
            timestamp=now,
        ),
        Odds(
            match_id=match_id,
            market="cards_over_4.5",
            odd_value=2.10,
            offered_by="betano",
            timestamp=now,
        ),
        Odds(
            match_id=match_id,
            market="cards_over_5.5",
            odd_value=3.20,
            offered_by="betano",
            timestamp=now,
        ),
    ]


def get_mock_prediction(match_id: int, market: str) -> Prediction:
    """Retorna predição fictícia para um mercado.

    Simula diferentes probabilidades por mercado para testar
    cenários de EV positivo e negativo.
    """
    # Mapeamento de mercado → probabilidade mock
    market_probs = {
        "corners_over_9.5": 0.58,   # λ ≈ 10.5 → P(X>9) ≈ 0.58
        "corners_over_10.5": 0.42,  # λ ≈ 10.5 → P(X>10) ≈ 0.42
        "cards_over_4.5": 0.55,     # λ ≈ 5.0 → P(X>4) ≈ 0.55
        "cards_over_5.5": 0.38,     # λ ≈ 5.0 → P(X>5) ≈ 0.38
    }

    prob = market_probs.get(market, 0.50)

    return Prediction(
        match_id=match_id,
        market=market,
        probability=prob,
        fair_odd=round(1 / prob, 2),
        model_version="mock_v2",
    )


def get_mock_team_statistics(team_id: int) -> dict:
    """Retorna estatísticas fictícias de um time para teste do predictor.

    Simula a estrutura da resposta da API-Football /teams/statistics.
    """
    # Estatísticas variadas por time
    stats_by_team = {
        127: {  # Flamengo
            "corners": {"for": {"average": {"total": "5.8"}}, "against": {"average": {"total": "4.2"}}},
            "cards": {"yellow": {"0-15": {"total": 8}, "16-30": {"total": 12}, "31-45": {"total": 15},
                                  "46-60": {"total": 10}, "61-75": {"total": 14}, "76-90": {"total": 18}},
                      "red": {"0-15": {"total": 0}, "16-30": {"total": 1}, "31-45": {"total": 0},
                              "46-60": {"total": 1}, "61-75": {"total": 2}, "76-90": {"total": 1}}},
            "fixtures": {"played": {"total": 30}},
        },
        124: {  # Fluminense
            "corners": {"for": {"average": {"total": "4.5"}}, "against": {"average": {"total": "5.5"}}},
            "cards": {"yellow": {"0-15": {"total": 10}, "16-30": {"total": 14}, "31-45": {"total": 12},
                                  "46-60": {"total": 11}, "61-75": {"total": 16}, "76-90": {"total": 20}},
                      "red": {"0-15": {"total": 1}, "16-30": {"total": 0}, "31-45": {"total": 1},
                              "46-60": {"total": 0}, "61-75": {"total": 1}, "76-90": {"total": 2}}},
            "fixtures": {"played": {"total": 30}},
        },
        121: {  # Palmeiras
            "corners": {"for": {"average": {"total": "6.2"}}, "against": {"average": {"total": "3.8"}}},
            "cards": {"yellow": {"0-15": {"total": 6}, "16-30": {"total": 10}, "31-45": {"total": 11},
                                  "46-60": {"total": 9}, "61-75": {"total": 13}, "76-90": {"total": 15}},
                      "red": {"0-15": {"total": 0}, "16-30": {"total": 0}, "31-45": {"total": 1},
                              "46-60": {"total": 0}, "61-75": {"total": 1}, "76-90": {"total": 0}}},
            "fixtures": {"played": {"total": 28}},
        },
    }

    return {"response": stats_by_team.get(team_id, {
        "corners": {"for": {"average": {"total": "5.0"}}, "against": {"average": {"total": "5.0"}}},
        "cards": {"yellow": {"0-15": {"total": 8}, "16-30": {"total": 10}, "31-45": {"total": 12},
                              "46-60": {"total": 10}, "61-75": {"total": 12}, "76-90": {"total": 14}},
                  "red": {"0-15": {"total": 0}, "16-30": {"total": 1}, "31-45": {"total": 0},
                          "46-60": {"total": 1}, "61-75": {"total": 1}, "76-90": {"total": 1}}},
        "fixtures": {"played": {"total": 25}},
    })}