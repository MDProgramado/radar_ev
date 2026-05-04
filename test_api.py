"""
test_api.py — Script rápido para testar conectividade com a API-Football.

Uso: poetry run python test_api.py
"""

import asyncio
from datetime import datetime

from radar_ev.collectors.football_api import FootballAPICollector


async def test_connectivity() -> None:
    """Testa se a API-Football está acessível e retorna dados."""
    print("🔌 Testando conectividade com API-Football...")
    print()

    async with FootballAPICollector() as collector:
        try:
            matches = await collector.get_today_matches(datetime.now())
            print(f"✅ Conexão OK! {len(matches)} jogos encontrados hoje.")
            print()

            if matches:
                print("📋 Primeiros 5 jogos:")
                for m in matches[:5]:
                    print(
                        f"  ⚽ {m.home_team} vs {m.away_team} "
                        f"({m.league}) — {m.datetime.strftime('%H:%M')}"
                    )

                # Testa busca de odds do primeiro jogo
                print(f"\n🎯 Buscando odds para {matches[0].home_team} vs {matches[0].away_team}...")
                odds = await collector.get_odds(matches[0].id)
                if odds:
                    print(f"  ✅ {len(odds)} odds encontradas.")
                    for o in odds[:3]:
                        print(f"    📊 {o.market}: {o.odd_value:.2f}")
                else:
                    print("  ⚠️ Sem odds da Betano para este jogo.")
            else:
                print("ℹ️ Nenhum jogo hoje.")

        except Exception as e:
            print(f"❌ ERRO: {e}")
            print("Verifique sua RAPIDAPI_KEY no .env")


if __name__ == "__main__":
    asyncio.run(test_connectivity())