"""
export_report.py — Gerador de Relatórios Comerciais.

Exporta todo o histórico de operações do banco de dados (SQLite) para um
arquivo CSV (aberto nativamente no Excel). Ideal para enviar aos clientes
como "Relatório de Transparência" e prova de lucratividade (Proof of Profit).
"""

import sqlite3
import csv
from datetime import datetime
from radar_ev.database import db
import structlog

logger = structlog.get_logger(__name__)

def generate_csv_report(output_file: str = "relatorio_transparencia.csv"):
    """Gera um CSV com todas as apostas resolvidas e o saldo financeiro."""
    try:
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            
            # Puxa apenas os jogos que já foram resolvidos
            cursor.execute("""
                SELECT 
                    match_date, home_team, away_team, league, market, 
                    offered_odd, confidence, recommended_stake, 
                    result_won, profit
                FROM opportunities
                WHERE status = 'RESOLVED'
                ORDER BY match_date ASC
            """)
            
            rows = cursor.fetchall()

        if not rows:
            print("❌ Nenhum dado resolvido para exportar no momento. Rode o result_resolver.py primeiro.")
            return

        with open(output_file, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            
            # Cabeçalho profissional
            writer.writerow([
                "Data do Jogo", "Mandante", "Visitante", "Campeonato", "Mercado",
                "Odd Pega", "Confiança do Robô", "Banca Recomendada (%)", 
                "Resultado (Green/Red)", "Lucro/Prejuízo (%)"
            ])
            
            saldo_acumulado = 0.0
            
            for row in rows:
                match_date = row[0][:10]  # Pega só YYYY-MM-DD
                home = row[1]
                away = row[2]
                league = row[3]
                market = row[4]
                odd = f"{row[5]:.2f}"
                conf = f"{row[6]*100:.1f}%"
                stake = f"{row[7]:.2f}%"
                
                won = "GREEN ✅" if row[8] else "RED ❌"
                profit = row[9]
                saldo_acumulado += profit
                profit_str = f"{profit:+.2f}%"
                
                writer.writerow([
                    match_date, home, away, league, market, odd, 
                    conf, stake, won, profit_str
                ])
                
            # Rodapé de resumo financeiro
            writer.writerow([])
            writer.writerow(["", "", "", "", "", "", "", "", "SALDO TOTAL:", f"{saldo_acumulado:+.2f}% da Banca"])
            
        print(f"✅ Relatório comercial gerado com sucesso: {output_file}")
        print(f"💰 Saldo Atual Comprovado: {saldo_acumulado:+.2f}%")

    except Exception as e:
        logger.error("report_generation_failed", error=str(e))
        print(f"❌ Erro ao gerar o relatório: {str(e)}")

if __name__ == "__main__":
    generate_csv_report()
