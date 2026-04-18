import pytest
from datetime import datetime, timezone, timedelta
from radar_ev.models import Match, Opportunity
from radar_ev.rules import motivation_filter, lineup_filter, derby_mode_filter, pre_match_filter, apply_all_rules

def create_sample_match(home="Flamengo", away="Fluminense", motivation=8.0, lineup_confirmed=True, dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc) + timedelta(hours=2)  # futuro
    return Match(
        id=1,
        home_team=home,
        away_team=away,
        datetime=dt,
        league="Brasileirão",
        referee="Wilton Sampaio",
        home_lineup_confirmed=lineup_confirmed,
        away_lineup_confirmed=lineup_confirmed,
        motivation_index=motivation,
    )

def create_opp(match, market="corners"):
    return Opportunity(
        match=match,
        market=market,
        fair_odd=2.0,
        offered_odd=1.8,
        ev_percent=11.1,
        confidence=0.9,
        reasoning=""
    )

def test_motivation_filter():
    match = create_sample_match(motivation=5.0)
    opp = create_opp(match)
    ok, reason = motivation_filter(opp, min_motivation=7.0)
    assert not ok
    assert "Motivação baixa" in reason

def test_lineup_filter():
    match = create_sample_match(lineup_confirmed=False)
    opp = create_opp(match, market="goals")
    ok, reason = lineup_filter(opp)
    assert not ok
    assert "Escalação não confirmada" in reason

def test_derby_mode():
    derby_teams = ["Flamengo", "Fluminense", "Boca Juniors", "River Plate"]
    match = create_sample_match(home="Flamengo", away="Fluminense")
    opp = create_opp(match, market="goals")
    ok, reason = derby_mode_filter(opp, derby_teams)
    assert not ok
    assert "Derby mode" in reason

def test_pre_match_filter():
    # Jogo no passado
    past_time = datetime.now(timezone.utc) - timedelta(hours=1)
    match = create_sample_match(dt=past_time)
    opp = create_opp(match)
    ok, reason = pre_match_filter(opp)
    assert not ok
    assert "já começou" in reason

def test_apply_all_rules_success():
    derby_teams = ["Flamengo", "Fluminense"]
    match = create_sample_match(home="Palmeiras", away="Corinthians", motivation=8.0, lineup_confirmed=True)
    opp = create_opp(match, market="cards")
    ok, reason = apply_all_rules(opp, derby_teams)
    assert ok
    assert reason == "Todas as regras aprovadas"