"""
test_config.py — Testes para o módulo de configuração.

Verifica:
- Carregamento de variáveis de ambiente.
- Validação de tipos e limites.
- Property derby_teams_list (CSV → lista).
"""

import os
import pytest


class TestSettings:
    """Testes para a classe Settings."""

    def test_settings_loaded(self):
        """Verifica que as settings são carregadas sem erro."""
        from radar_ev.config import settings
        assert settings.rapidapi_key is not None
        assert len(settings.rapidapi_key) > 0

    def test_min_ev_percent_default(self):
        from radar_ev.config import settings
        assert settings.min_ev_percent >= 0.0

    def test_pre_match_hours_default(self):
        from radar_ev.config import settings
        assert settings.pre_match_hours >= 1

    def test_derby_teams_is_string(self):
        """Verifica que derby_teams é armazenado como string."""
        from radar_ev.config import settings
        assert isinstance(settings.derby_teams, str)

    def test_derby_teams_list_is_list(self):
        """Verifica que derby_teams_list retorna uma lista."""
        from radar_ev.config import settings
        result = settings.derby_teams_list
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)
        assert len(result) > 0


class TestDerbyTeamsProperty:
    """Testes para a propriedade derby_teams_list."""

    def test_parse_csv_string(self):
        from radar_ev.config import Settings
        s = Settings.model_construct(derby_teams="Flamengo,Fluminense,Vasco")
        assert s.derby_teams_list == ["Flamengo", "Fluminense", "Vasco"]

    def test_parse_csv_with_spaces(self):
        from radar_ev.config import Settings
        s = Settings.model_construct(derby_teams="  Flamengo , Fluminense , Vasco  ")
        assert s.derby_teams_list == ["Flamengo", "Fluminense", "Vasco"]

    def test_parse_json_like(self):
        from radar_ev.config import Settings
        s = Settings.model_construct(derby_teams='["Flamengo", "Fluminense"]')
        assert "Flamengo" in s.derby_teams_list
        assert "Fluminense" in s.derby_teams_list

    def test_empty_string(self):
        from radar_ev.config import Settings
        s = Settings.model_construct(derby_teams="")
        assert s.derby_teams_list == []
