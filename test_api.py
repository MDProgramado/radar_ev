import asyncio
from radar_ev.collectors.football_api import FootballAPICollector
from datetime import datetime

async def test():
    collector = FootballAPICollector()
    try:
        matches = await collector.get_today_matches(datetime.now())
        print(f"Encontrados {len(matches)} jogos hoje.")
        for m in matches[:3]:
            print(f"{m.home_team} vs {m.away_team} - {m.datetime}")
    except Exception as e:
        print(f"ERRO: {e}")
    finally:
        await collector.close()

asyncio.run(test())