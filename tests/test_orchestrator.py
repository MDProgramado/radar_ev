"""
test_orchestrator.py — Testes para o pipeline orquestrador.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.ev_calculator import calculate_ev, calculate_ev_vig_removed
from radar_ev.models import Match, Odds, Prediction
from radar_ev.orchestrator import (
    _calculate_ev_without_vig,
    _complementary_odd,
    _count_resolved,
    _filter_matches,
    _extract_threshold,
    _get_prediction_for_market,
    _parse_market,
    _report_fallback_alert,
    _run_result_resolution,
)
from radar_ev.vig_stats import VigStats, vig_stats
from datetime import datetime, timedelta, timezone
import sqlite3

# Roteia structlog para o logging padrão para permitir captura via caplog.
import structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.processors.KeyValueRenderer(sort_keys=False),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)


def _match(**kwargs):
    """Cria um Match com IDs válidos, pronto para predição."""
    defaults = dict(
        id=1,
        home_team="Palmeiras",
        away_team="Corinthians",
        datetime=datetime.now(timezone.utc) + timedelta(hours=2),
        league="Serie A",
        home_team_id=101,
        away_team_id=102,
        league_id=71,
        season=2026,
    )
    defaults.update(kwargs)
    return Match(**defaults)


class TestFilterMatches:
    def test_filters_by_league(self):
        now = datetime.now(timezone.utc)
        matches = [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now + timedelta(hours=2),
                  league="Premier League"),
            Match(id=2, home_team="C", away_team="D",
                  datetime=now + timedelta(hours=2),
                  league="Liga Desconhecida"),
        ]
        filtered = _filter_matches(matches)
        assert len(filtered) == 1
        assert filtered[0].league == "Premier League"

    def test_filters_past_matches(self):
        now = datetime.now(timezone.utc)
        matches = [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now - timedelta(hours=1),
                  league="Premier League"),
        ]
        filtered = _filter_matches(matches)
        assert len(filtered) == 0


class TestExtractThreshold:
    def test_extract_from_corners(self):
        assert _extract_threshold("corners_over_9.5") == 9.5

    def test_extract_from_cards(self):
        assert _extract_threshold("cards_over_4.5") == 4.5

    def test_default_value(self):
        assert _extract_threshold("unknown_market", 9.5) == 9.5

    def test_extract_integer(self):
        assert _extract_threshold("corners_over_10") == 10.0


@pytest.mark.asyncio
async def test_mock_pipeline():
    """Testa o pipeline com dados mockados."""
    from radar_ev.orchestrator import _run_mock_pipeline
    opps = await _run_mock_pipeline()
    assert isinstance(opps, list)


class TestNormalizeMarketName:
    """Normalizador canônico (casos reais da Betano)."""

    norm = staticmethod(FootballAPICollector._normalize_market_name)

    def test_goals_over_25(self):
        assert self.norm("Goals Over/Under", "Over 2.5") == "goals_over_2.5"

    def test_goals_under_25(self):
        assert self.norm("Goals Over/Under", "Under 2.5") == "goals_under_2.5"

    def test_goals_over_under_first_half_rejected(self):
        assert self.norm("Goals Over/Under First Half", "Over 0.5") is None

    def test_corners_over_95(self):
        assert self.norm("Corners Over/Under", "Over 9.5") == "corners_over_9.5"

    def test_cards_over_45(self):
        assert self.norm("Cards Over/Under", "Over 4.5") == "cards_over_4.5"

    def test_cards_under_45(self):
        assert self.norm("Cards Over/Under", "Under 4.5") == "cards_under_4.5"

    def test_total_home_rejected(self):
        assert self.norm("Total - Home", "Under 2.5") is None

    def test_total_away_rejected(self):
        assert self.norm("Total - Away", "Under 1.5") is None

    def test_home_team_rejected(self):
        assert self.norm("Home Team Total Goals", "Over 1.5") is None

    def test_away_team_rejected(self):
        assert self.norm("Away Team Total Goals", "Under 1.5") is None

    def test_match_winner_rejected(self):
        assert self.norm("Match Winner", "Home") is None

    def test_asian_handicap_rejected(self):
        assert self.norm("Asian Handicap", "Home +0.25") is None

    def test_odd_even_rejected(self):
        assert self.norm("Odd/Even", "Even") is None

    def test_double_chance_rejected(self):
        assert self.norm("Double Chance", "Home/Draw") is None

    def test_home_away_bet_rejected(self):
        assert self.norm("Corners Home/Away", "Home") is None

    def test_home_corners_over_under_rejected(self):
        assert self.norm("Home Corners Over/Under", "Over 3.5") is None

    def test_away_corners_over_under_rejected(self):
        assert self.norm("Away Corners Over/Under", "Over 5.5") is None

    def test_home_team_goals_over_under_rejected(self):
        assert self.norm("Home Team Goals Over/Under", "Over 1.5") is None

    def test_home_cards_over_under_rejected(self):
        assert self.norm("Home Cards Over/Under", "Over 2.5") is None

    def test_total_corners_over_95(self):
        assert self.norm("Total Corners", "Over 9.5") == "corners_over_9.5"

    def test_corners_over_under_spaces(self):
        assert self.norm("Corners Over Under", "Over 9.5") == "corners_over_9.5"

    def test_no_direction_rejected(self):
        assert self.norm("Goals Over/Under", "Total 2.5") is None

    def test_no_line_rejected(self):
        assert self.norm("Goals Over/Under", "Over") is None

    def test_shot_on_target_rejected(self):
        assert self.norm("Shot On Target", "Over 4.5") is None


class TestParseMarketWhitelist:
    """Whitelist estrita de mercados (regex + range de sanity)."""

    def test_corners_over_95_accepted(self):
        assert _parse_market("corners_over_9.5") == ("corners", "over", 9.5)

    def test_goals_under_25_accepted(self):
        assert _parse_market("goals_under_2.5") == ("goals", "under", 2.5)

    def test_cards_over_45_accepted(self):
        assert _parse_market("cards_over_4.5") == ("cards", "over", 4.5)

    def test_corners_over_10_rejected_out_of_range(self):
        assert _parse_market("corners_over_1.0") is None

    def test_corners_over_200_rejected_out_of_range(self):
        assert _parse_market("corners_over_20.0") is None

    def test_odd_even_rejected(self):
        assert _parse_market("odd_even_even") is None

    def test_double_chance_rejected(self):
        assert _parse_market("double_chance_home_away") is None

    def test_asian_handicap_rejected(self):
        assert _parse_market("asian_handicap_away_-1") is None

    def test_first_half_team_goals_rejected(self):
        assert _parse_market("away_team_total_goals(1st_half)_under_1.5") is None

    def test_anytime_goal_scorer_rejected(self):
        assert _parse_market("home_anytime_goal_scorer_nicolas_jackson") is None

    def test_partial_market_rejected(self):
        assert _parse_market("corners_over") is None

    def test_three_decimals_rejected(self):
        assert _parse_market("corners_over_9.555") is None


class TestGetPredictionForMarket:
    """Mediação real do pipeline: rejeita antes de tocar a API, aceita e despacha."""

    @pytest.mark.asyncio
    async def test_rejected_market_returns_none_with_ids(self):
        pred = await _get_prediction_for_market(
            match=_match(),
            market="odd_even_even",
            football_api=AsyncMock(),
        )
        assert pred is None

    @pytest.mark.asyncio
    async def test_rejected_market_returns_none_without_api_call(self):
        with patch("radar_ev.orchestrator.make_corners_prediction") as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="corners_over_1.0",  # fora do range
                football_api=AsyncMock(),
            )
            assert pred is None
            mock_pred.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepted_corners_dispatches_to_predictor(self):
        expected = object()
        with patch(
            "radar_ev.orchestrator.make_corners_prediction",
            new=AsyncMock(return_value=expected),
        ) as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="corners_over_9.5",
                football_api=AsyncMock(),
            )
            assert pred is expected
            mock_pred.assert_awaited_once()
            _, kwargs = mock_pred.await_args
            assert kwargs["threshold"] == 9.5

    @pytest.mark.asyncio
    async def test_accepted_goals_under_dispatches_with_is_over_false(self):
        from radar_ev.models import Prediction

        fake_pred = Prediction(
            match_id=1,
            market="goals_under_2.5",
            probability=0.5,
            fair_odd=2.0,
            model_version="test",
        )
        with patch(
            "radar_ev.orchestrator.make_goals_prediction",
            new=AsyncMock(return_value=fake_pred),
        ) as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="goals_under_2.5",
                football_api=AsyncMock(),
            )
            assert pred is fake_pred
            _, kwargs = mock_pred.await_args
            assert kwargs["is_over"] is False
            assert kwargs["threshold"] == 2.5


class TestVigFallback:
    """Integração: par Over/Under, fallback e limiares divergentes."""

    def _pred(self, prob: float = 0.55) -> Prediction:
        return Prediction(
            match_id=10,
            market="corners_over_9.5",
            probability=prob,
            fair_odd=round(1.0 / prob, 4),
            model_version="test",
        )

    def _odds(self, market: str, odd_value: float) -> Odds:
        return Odds(match_id=10, market=market, odd_value=odd_value)

    def test_pair_over_under_uses_vig_removed(self):
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [
            self._odds("corners_over_9.5", 2.10),
            self._odds("corners_under_9.5", 1.75),
        ]
        ev, vig_used, divergent = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is True
        assert divergent is False
        assert ev == pytest.approx(calculate_ev_vig_removed(pred, 2.10, 1.75))
        assert _complementary_odd("corners_over_9.5", odds_list) == 1.75
        assert vig_stats.with_vig == 1
        assert vig_stats.fallback == 0
        assert vig_stats.fallback_limiar_divergente == 0

    def test_without_pair_falls_back_and_logs_warning(self, caplog):
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [self._odds("corners_over_9.5", 2.10)]
        with caplog.at_level("WARNING"):
            ev, vig_used, divergent = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is False
        assert divergent is False
        assert ev == pytest.approx(calculate_ev(pred, 2.10))
        assert _complementary_odd("corners_over_9.5", odds_list) is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("complementary_odd_not_found" in r.getMessage() for r in warnings)
        assert any("match_id=10" in r.getMessage() for r in warnings)
        assert vig_stats.fallback == 1
        assert vig_stats.fallback_limiar_divergente == 0

    def test_mismatched_line_is_fallback_not_pair(self, caplog):
        # Definido: par exige a MESMA linha (over_9.5 + under_10.5 ≠ par).
        # Limiares diferentes → sem complemento → fallback para odd bruta.
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [
            self._odds("corners_over_9.5", 2.10),
            self._odds("corners_under_10.5", 1.75),
        ]
        with caplog.at_level("WARNING"):
            ev, vig_used, _divergent = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is False
        assert ev == pytest.approx(calculate_ev(pred, 2.10))
        assert _complementary_odd("corners_over_9.5", odds_list) is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("complementary_odd_not_found" in r.getMessage() for r in warnings)

    def test_mismatched_line_detected_as_divergent(self, caplog):
        # Complemento com limiar DIFERENTE existe → marcado como divergente
        # (candidato a interpolação futura), contado à parte do fallback geral.
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [
            self._odds("corners_over_9.5", 2.10),
            self._odds("corners_under_10.5", 1.75),
        ]
        with caplog.at_level("WARNING"):
            ev, vig_used, divergent = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is False
        assert divergent is True
        assert ev == pytest.approx(calculate_ev(pred, 2.10))
        assert vig_stats.fallback == 1
        assert vig_stats.fallback_limiar_divergente == 1
        assert vig_stats.divergente_percent == pytest.approx(100.0)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("limiar_divergente=True" in r.getMessage() for r in warnings)

    def test_find_divergent_ignores_unrelated_markets(self):
        # "cards_over_4.5" na lista não é complemento de corners_over_9.5.
        pred = self._pred(0.55)
        odds_list = [self._odds("cards_over_4.5", 2.10)]
        _ev, _vig_used, divergent = _calculate_ev_without_vig(
            pred, 2.10, "corners_over_9.5", odds_list
        )
        assert divergent is False


class TestVigFallbackStats:
    """Contadores: % de oportunidades com vig removido vs. fallback."""

    def _pred(self) -> Prediction:
        return Prediction(
            match_id=1,
            market="corners_over_9.5",
            probability=0.55,
            fair_odd=1.82,
            model_version="test",
        )

    def test_five_opportunities_three_pairs(self):
        # 5 avaliações → 3 com par Over/Under, 2 sem → 60% de sucesso.
        vig_stats.reset()
        pair = [
            Odds(match_id=1, market="corners_over_9.5", odd_value=1.90),
            Odds(match_id=1, market="corners_under_9.5", odd_value=1.90),
        ]
        lone = [Odds(match_id=1, market="corners_over_9.5", odd_value=1.90)]
        pred = self._pred()

        for odds_list in [pair, pair, pair, lone, lone]:
            _calculate_ev_without_vig(pred, 1.90, "corners_over_9.5", odds_list)

        assert vig_stats.with_vig == 3
        assert vig_stats.fallback == 2
        assert vig_stats.total == 5
        assert vig_stats.vig_percent == pytest.approx(60.0)
        assert vig_stats.fallback_percent == pytest.approx(40.0)

    def test_alert_when_fallback_above_threshold(self, caplog):
        stats = VigStats()
        stats.with_vig = 3
        stats.fallback = 5  # 62.5% de fallback > 20%
        with caplog.at_level("ERROR"):
            message = _report_fallback_alert(stats)
        assert message is not None
        assert "ALERTA:" in message
        assert "62.5%" in message
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("vig_fallback_alert" in r.getMessage() for r in errors)

    def test_no_alert_below_threshold(self):
        stats = VigStats()
        stats.with_vig = 5
        stats.fallback = 1  # 16.7% < 20%
        assert _report_fallback_alert(stats) is None

    def test_no_alert_when_no_evaluations(self):
        stats = VigStats()
        assert _report_fallback_alert(stats) is None

    def test_alert_in_mock_is_info_not_error(self, caplog):
        # Em mock 100% de fallback é esperado (dado sintético sem pares) →
        # nunca deve subir ERROR, apenas INFO.
        stats = VigStats()
        stats.fallback = 8  # 100% fallback
        with caplog.at_level("INFO"):
            message = _report_fallback_alert(stats, in_mock=True)
        assert message is not None
        assert "ALERTA:" in message
        assert "100.0%" in message
        assert not any(r.levelname == "ERROR" for r in caplog.records)
        infos = [r for r in caplog.records if r.levelname == "INFO"]
        assert any("vig_fallback_alert" in r.getMessage() for r in infos)

    def test_alert_level_config_info(self, caplog):
        # FALLBACK_ALERT_LEVEL=INFO: mesma mensagem, mas level INFO.
        stats = VigStats()
        stats.fallback = 8
        with caplog.at_level("INFO"):
            message = _report_fallback_alert(stats, level="INFO")
        assert message is not None
        assert not any(r.levelname == "ERROR" for r in caplog.records)
        infos = [r for r in caplog.records if r.levelname == "INFO"]
        assert any("vig_fallback_alert" in r.getMessage() for r in infos)

    def test_threshold_from_settings_default(self, caplog):
        from radar_ev.config import settings

        stats = VigStats()
        stats.with_vig = 5
        assert settings.fallback_warning_threshold == 20.0
        stats.fallback = 5  # 50% > 20% → dispara
        with caplog.at_level("ERROR"):
            message = _report_fallback_alert(stats)
        assert message is not None


class TestResultResolution:
    """Integração do result_resolver no pipeline (--resolve/--no-resolve)."""

    @staticmethod
    def _make_db(path: str, rows):
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE opportunities "
            "(id INTEGER PRIMARY KEY, status TEXT, result_won BOOLEAN)"
        )
        conn.executemany(
            "INSERT INTO opportunities (id, status, result_won) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()

    def test_count_resolved_green_red(self, tmp_path):
        db_path = str(tmp_path / "hist.db")
        self._make_db(
            db_path,
            [(1, "RESOLVED", 1), (2, "RESOLVED", 0), (3, "PENDING", None)],
        )
        resolver = Mock(db_path=db_path)
        counts = _count_resolved(
            resolver, [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 99}]
        )
        assert counts == {"greens": 1, "reds": 1, "nulls": 0, "still_pending": 1}

    def test_count_resolved_categorizes_null_separately(self, tmp_path):
        # 5 RESOLVED: 3 GREEN, 1 RED, 1 NULL → NULL não vira RED.
        db_path = str(tmp_path / "hist.db")
        self._make_db(
            db_path,
            [
                (1, "RESOLVED", 1),
                (2, "RESOLVED", 1),
                (3, "RESOLVED", 1),
                (4, "RESOLVED", 0),
                (5, "RESOLVED", None),  # cancelado/adiado/indeterminado
            ],
        )
        resolver = Mock(db_path=db_path)
        counts = _count_resolved(
            resolver,
            [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}],
        )
        assert counts == {"greens": 3, "reds": 1, "nulls": 1, "still_pending": 0}

    def test_count_resolved_empty(self, tmp_path):
        db_path = str(tmp_path / "hist.db")
        self._make_db(db_path, [(1, "RESOLVED", 1)])
        resolver = Mock(db_path=db_path)
        assert _count_resolved(resolver, []) == {
            "greens": 0, "reds": 0, "nulls": 0, "still_pending": 0,
        }

    @pytest.mark.asyncio
    async def test_run_resolution_summary_logs(self, caplog, tmp_path):
        db_path = str(tmp_path / "hist.db")
        self._make_db(
            db_path,
            [(1, "RESOLVED", 1), (2, "RESOLVED", 0), (3, "PENDING", None)],
        )
        fake = Mock()
        fake.db_path = db_path
        fake.get_pending_opportunities = Mock(
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}]
        )
        fake.resolve_all = AsyncMock()
        with patch(
            "radar_ev.result_resolver.ResultResolver", return_value=fake
        ):
            with caplog.at_level("INFO"):
                summary = await _run_result_resolution()
        assert summary == {
            "pending": 3,
            "greens": 1,
            "reds": 1,
            "nulls": 0,
            "still_pending": 1,
            "resolved": 2,
        }
        fake.resolve_all.assert_awaited_once()
        infos = [r for r in caplog.records if r.levelname == "INFO"]
        assert any("result_resolution_summary" in r.getMessage() for r in infos)

    @pytest.mark.asyncio
    async def test_run_resolution_warns_on_high_nulls(self, caplog, tmp_path):
        # 10 RESOLVED com 2 NULL (20% > 5%) → WARNING result_resolution_null_high
        db_path = str(tmp_path / "hist.db")
        rows = [
            (i, "RESOLVED", 1) for i in range(1, 6)
        ] + [
            (i, "RESOLVED", 0) for i in range(6, 9)
        ] + [
            (9, "RESOLVED", None),
            (10, "RESOLVED", None),
        ]
        self._make_db(db_path, rows)
        fake = Mock()
        fake.db_path = db_path
        fake.get_pending_opportunities = Mock(
            return_value=[{"id": i} for i in range(1, 11)]
        )
        fake.resolve_all = AsyncMock()
        with patch(
            "radar_ev.result_resolver.ResultResolver", return_value=fake
        ):
            with caplog.at_level("WARNING"):
                summary = await _run_result_resolution()
        assert summary["nulls"] == 2
        assert summary["resolved"] == 10
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("result_resolution_null_high" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_run_resolution_accepts_nulls_below_threshold(self, caplog, tmp_path):
        # 40 RESOLVED com 1 NULL (2.5% < 5%) → sem WARNING.
        db_path = str(tmp_path / "hist.db")
        rows = [(i, "RESOLVED", 1) for i in range(1, 40)]
        rows.append((40, "RESOLVED", None))
        self._make_db(db_path, rows)
        fake = Mock()
        fake.db_path = db_path
        fake.get_pending_opportunities = Mock(
            return_value=[{"id": i} for i in range(1, 41)]
        )
        fake.resolve_all = AsyncMock()
        with patch(
            "radar_ev.result_resolver.ResultResolver", return_value=fake
        ):
            with caplog.at_level("WARNING"):
                summary = await _run_result_resolution()
        assert summary["nulls"] == 1
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not any("result_resolution_null_high" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_run_resolution_no_pending_skips_resolve(self, caplog):
        fake = Mock()
        fake.get_pending_opportunities = Mock(return_value=[])
        fake.resolve_all = AsyncMock()
        with patch(
            "radar_ev.result_resolver.ResultResolver", return_value=fake
        ):
            summary = await _run_result_resolution()
        assert summary == {
            "pending": 0, "greens": 0, "reds": 0, "nulls": 0, "still_pending": 0,
        }
        fake.resolve_all.assert_not_awaited()


class TestCli:
    """CLI (argparse) — flags, --league, --list-leagues e salvaguarda de mock."""

    @pytest.fixture(autouse=True)
    def _reset_mock_flag(self, monkeypatch):
        # R5: flag em memória precisa começar limpa em cada teste.
        monkeypatch.setattr(
            "radar_ev.orchestrator._logged_mock_resolution_disabled", False
        )

    def test_parser_parses_all_flags(self):
        from radar_ev.orchestrator import build_arg_parser

        args = build_arg_parser().parse_args(
            ["--mock", "--resolve", "--no-resolve", "--league", "Premier League"]
        )
        assert args.mock is True
        assert args.resolve is True
        assert args.no_resolve is True
        assert args.league == "Premier League"

    def test_parser_defaults(self):
        from radar_ev.orchestrator import build_arg_parser

        args = build_arg_parser().parse_args([])
        assert args.mock is False
        assert args.daemon is False
        assert args.resolve is False
        assert args.no_resolve is False
        assert args.league is None

    def test_league_filter_applied_in_filter_matches(self):
        now = datetime.now(timezone.utc)
        matches = [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now + timedelta(hours=2), league="Premier League"),
            Match(id=2, home_team="C", away_team="D",
                  datetime=now + timedelta(hours=2), league="La Liga"),
        ]
        filtered = _filter_matches(matches, league_filter="La Liga")
        assert [m.league for m in filtered] == ["La Liga"]

    @pytest.mark.asyncio
    async def test_mock_forces_resolution_off_even_with_resolve(self):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--mock", "--resolve"])
        with patch(
            "radar_ev.orchestrator._run_result_resolution",
            new=AsyncMock(),
        ) as mock_res, patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe:
            await _execute_cli(args)
        mock_res.assert_not_awaited()
        mock_pipe.assert_awaited_once_with(use_mock=True, league_filter=None)

    @pytest.mark.asyncio
    async def test_mock_logs_resolution_disabled(self, caplog):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--mock", "--resolve"])
        with patch(
            "radar_ev.orchestrator._run_result_resolution",
            new=AsyncMock(),
        ), patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ):
            with caplog.at_level("INFO"):
                await _execute_cli(args)
        infos = [r for r in caplog.records if r.levelname == "INFO"]
        assert any("mock_resolution_disabled" in r.getMessage() for r in infos)

    @pytest.mark.asyncio
    async def test_single_shot_does_not_resolve_by_default(self):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args([])
        with patch(
            "radar_ev.orchestrator._run_result_resolution",
            new=AsyncMock(),
        ) as mock_res, patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe:
            await _execute_cli(args)
        mock_res.assert_not_awaited()
        mock_pipe.assert_awaited_once_with(use_mock=False, league_filter=None)

    @pytest.mark.asyncio
    async def test_daemon_resolves_each_cycle(self):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--daemon"])
        with patch(
            "radar_ev.orchestrator._run_result_resolution",
            new=AsyncMock(),
        ) as mock_res, patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe, patch(
            "radar_ev.orchestrator.asyncio.sleep",
            side_effect=KeyboardInterrupt,
        ):
            await _execute_cli(args)
        mock_res.assert_awaited_once()
        mock_pipe.assert_awaited_once_with(use_mock=False, league_filter=None)

    @pytest.mark.asyncio
    async def test_resolve_flag_runs_only_resolution(self):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--resolve"])
        with patch(
            "radar_ev.orchestrator._run_result_resolution",
            new=AsyncMock(return_value={}),
        ) as mock_res, patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe:
            await _execute_cli(args)
        mock_res.assert_awaited_once()
        mock_pipe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_pipeline_threads_league_filter_to_real(self):
        # Regressão: _run_real_pipeline usa league_filter — precisa recebê-lo
        # por parâmetro. Sem isso, modo real quebra com NameError.
        from radar_ev.orchestrator import run_pipeline

        with patch(
            "radar_ev.orchestrator._run_real_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_real:
            opps = await run_pipeline(use_mock=False, league_filter="Premier League")
        assert opps == []
        mock_real.assert_awaited_once_with(league_filter="Premier League")

    @pytest.mark.asyncio
    async def test_mock_daemon_logs_disabled_only_once(self, caplog):
        # R5: daemon mock com 3 ciclos → apenas 1 INFO de resolução desabilitada.
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--mock", "--daemon"])
        sleep_mock = AsyncMock(side_effect=[None, None, KeyboardInterrupt()])
        with patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe, patch(
            "radar_ev.orchestrator.asyncio.sleep", new=sleep_mock
        ):
            with caplog.at_level("INFO"):
                await _execute_cli(args)
        assert mock_pipe.await_count == 3
        infos = [
            r for r in caplog.records
            if r.levelname == "INFO" and "mock_resolution_disabled" in r.getMessage()
        ]
        assert len(infos) == 1

    @pytest.mark.asyncio
    async def test_list_leagues_does_not_run_pipeline(self, capsys):
        from radar_ev.orchestrator import _execute_cli, build_arg_parser

        args = build_arg_parser().parse_args(["--mock", "--list-leagues"])
        with patch(
            "radar_ev.orchestrator.run_pipeline",
            new=AsyncMock(return_value=[]),
        ) as mock_pipe:
            await _execute_cli(args)
        mock_pipe.assert_not_awaited()
        out = capsys.readouterr().out
        assert "Ligas disponíveis" in out
        assert "Brasileirão" in out


class TestLeagueFilterDiagnostics:
    """Risco 4: --league com nome errado → WARNING + sugestões."""

    @staticmethod
    def _matches():
        now = datetime.now(timezone.utc)
        return [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now + timedelta(hours=2), league="Premier League"),
            Match(id=2, home_team="C", away_team="D",
                  datetime=now + timedelta(hours=2), league="Premier League"),
            Match(id=3, home_team="E", away_team="F",
                  datetime=now + timedelta(hours=2), league="La Liga"),
        ]

    def test_top_leagues_ranked_by_count(self):
        from radar_ev.orchestrator import _top_leagues

        top = _top_leagues(self._matches(), top_n=10)
        assert top[0] == {"name": "Premier League", "count": 2}
        assert top[1] == {"name": "La Liga", "count": 1}

    def test_unknown_league_logs_warning_and_suggests(self, caplog, capsys):
        with caplog.at_level("WARNING"):
            filtered = _filter_matches(
                self._matches(), league_filter="Liga Inexistente"
            )
        assert filtered == []
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("league_filter_no_matches" in r.getMessage() for r in warnings)
        out = capsys.readouterr().out
        assert "Liga Inexistente" in out
        assert "Premier League" in out
        assert "La Liga" in out

    def test_known_league_does_not_warn(self, caplog):
        with caplog.at_level("WARNING"):
            filtered = _filter_matches(self._matches(), league_filter="La Liga")
        assert [m.league for m in filtered] == ["La Liga"]
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not any("league_filter_no_matches" in r.getMessage() for r in warnings)


class TestMainExitCodes:
    """Exit code do processo: 0 sucesso, 1 falha (crash ou exceção)."""

    def test_main_returns_1_when_pipeline_crashes(self, monkeypatch):
        from radar_ev.http_client import RateLimitError
        from radar_ev.orchestrator import main

        async def boom(*args, **kwargs):
            raise RateLimitError("You have reached the request limit for the day")

        monkeypatch.setattr("radar_ev.orchestrator._run_real_pipeline", boom)
        assert main([]) == 1

    def test_main_returns_1_when_resolution_fails(self, monkeypatch):
        from radar_ev.orchestrator import main

        async def boom(*args, **kwargs):
            raise RuntimeError("falha ao enviar relatório Telegram")

        monkeypatch.setattr("radar_ev.orchestrator._run_result_resolution", boom)
        assert main(["--resolve"]) == 1

    def test_main_returns_0_on_successful_pipeline(self):
        from radar_ev.orchestrator import main

        assert main(["--mock"]) == 0
