"""
test_rules.py — Testes para o motor de regras de negócio (RN01–RN05).
"""

from datetime import datetime, timedelta, timezone
import pytest
from radar_ev.models import Match, Opportunity
from radar_ev.rules import (
    apply_all_rules, bet_builder_filter, derby_mode_filter,
    lineup_filter, motivation_filter, pre_match_filter,
)


def _match(home="Palmeiras", away="Corinthians", motivation=8.0,
           lineup=True, dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc) + timedelta(hours=2)
    return Match(id=1, home_team=home, away_team=away, datetime=dt,
                 league="Brasileirão", referee="Wilton Sampaio",
                 home_lineup_confirmed=lineup, away_lineup_confirmed=lineup,
                 motivation_index=motivation)


def _opp(match, market="corners_over_9.5"):
    return Opportunity(match=match, market=market, fair_odd=2.0,
                       offered_odd=1.8, ev_percent=11.1, confidence=0.72,
                       reasoning="Teste")


class TestMotivationFilter:
    def test_below(self):
        ok, r = motivation_filter(_opp(_match(motivation=5.0)), 7.0)
        assert not ok and "RN01" in r

    def test_above(self):
        ok, _ = motivation_filter(_opp(_match(motivation=8.5)), 7.0)
        assert ok

    def test_at_threshold(self):
        ok, _ = motivation_filter(_opp(_match(motivation=7.0)), 7.0)
        assert ok


class TestLineupFilter:
    def test_not_confirmed_goals(self):
        ok, r = lineup_filter(_opp(_match(lineup=False), "goals"))
        assert not ok and "RN02" in r

    def test_not_confirmed_corners(self):
        ok, _ = lineup_filter(_opp(_match(lineup=False), "corners_over_9.5"))
        assert ok

    def test_confirmed_goals(self):
        ok, _ = lineup_filter(_opp(_match(lineup=True), "goals"))
        assert ok


class TestDerbyModeFilter:
    dt = ["Flamengo", "Fluminense", "Boca Juniors", "River Plate"]

    def test_blocks_goals(self):
        ok, r = derby_mode_filter(_opp(_match("Flamengo", "Fluminense"), "goals"), self.dt)
        assert not ok and "RN03" in r

    def test_allows_cards(self):
        ok, _ = derby_mode_filter(_opp(_match("Flamengo", "Fluminense"), "cards_over_4.5"), self.dt)
        assert ok

    def test_non_derby(self):
        ok, _ = derby_mode_filter(_opp(_match(), "goals"), self.dt)
        assert ok


class TestPreMatchFilter:
    def test_future(self):
        ok, _ = pre_match_filter(_opp(_match()))
        assert ok

    def test_past(self):
        ok, r = pre_match_filter(_opp(_match(dt=datetime.now(timezone.utc) - timedelta(hours=1))))
        assert not ok and "já começou" in r


class TestApplyAllRules:
    dt = ["Flamengo", "Fluminense"]

    def test_all_pass(self):
        ok, r = apply_all_rules(_opp(_match()), self.dt, 7.0)
        assert ok and "aprovadas" in r

    def test_stops_first(self):
        ok, r = apply_all_rules(_opp(_match(motivation=3.0)), self.dt, 7.0)
        assert not ok and "RN01" in r

    def test_derby_cards_ok(self):
        ok, _ = apply_all_rules(_opp(_match("Flamengo", "Fluminense", 9.0), "cards_over_4.5"), self.dt, 7.0)
        assert ok