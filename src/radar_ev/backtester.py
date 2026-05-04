"""
backtester.py — Motor de Backtesting para o Radar +EV.

Analisa as oportunidades salvas no Data Lake (history.db) e calcula
métricas de desempenho do sistema para provar lucratividade ao longo do tempo.
"""

import sqlite3

def run_backtest(db_path: str = "data/history.db"):
    """Lê os dados do banco de dados e exibe um relatório financeiro."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Lê todas as oportunidades do banco
        cursor.execute("SELECT * FROM opportunities")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            print("============================================================")
            print("📈 RELATÓRIO DE BACKTESTING - RADAR +EV")
            print("============================================================")
            print("Nenhum dado histórico encontrado. O sistema precisa rodar e")
            print("salvar oportunidades no banco de dados primeiro.")
            return

        ev_total = sum(r['ev_percent'] for r in rows)
        odd_total = sum(r['offered_odd'] for r in rows)
        stake_total = sum(r['recommended_stake'] for r in rows)
        
        count = len(rows)

        print("============================================================")
        print("📈 RELATÓRIO DE BACKTESTING - RADAR +EV")
        print("============================================================")
        print(f"Total de Oportunidades Encontradas: {count}")
        print(f"Média de Valor Esperado (EV): {ev_total / count:.2f}%")
        print(f"Média de Odd Oferecida: {odd_total / count:.2f}")
        print(f"Banca Sugerida Média (Kelly): {stake_total / count:.2f}%")
        print("------------------------------------------------------------")
        
        # Simulação de Lucratividade Teórica
        print("💰 Projeção Financeira Teórica (Baseada no EV):")
        
        banca_inicial = 1000.0
        banca_atual = banca_inicial
        
        for row in rows:
            stake_pct = row['recommended_stake'] / 100.0
            valor_aposta = banca_atual * stake_pct
            
            # O lucro esperado de uma aposta é (Valor Apostado * EV%)
            lucro_esperado = valor_aposta * (row['ev_percent'] / 100.0)
            banca_atual += lucro_esperado
            
        roi = ((banca_atual - banca_inicial) / banca_inicial) * 100
        
        print(f"Banca Inicial (Simulada): R$ {banca_inicial:.2f}")
        print(f"Banca Final Projetada: R$ {banca_atual:.2f}")
        print(f"ROI Esperado: {roi:.2f}%")
        print("============================================================")
        print("Nota: Para lucro real, integre com a API de /fixtures/events para")
        print("checar quem venceu e usar a coluna result_won.")

    except sqlite3.Error as e:
        print(f"Erro ao acessar o banco de dados: {e}")
    except Exception as e:
        print(f"Erro durante o backtest: {e}")

if __name__ == "__main__":
    run_backtest()
