"""
export_report.py — Gerador de Relatórios Comerciais.

Exporta todo o histórico de operações do banco de dados (SQLite) para um
arquivo CSV (aberto nativamente no Excel). Ideal para enviar aos clientes
como "Relatório de Transparência" e prova de lucratividade (Proof of Profit).
"""

import csv
from datetime import datetime
from radar_ev.database import db
from radar_ev.db import get_db_connection
import structlog

logger = structlog.get_logger(__name__)

def generate_csv_report(output_file: str = "relatorio_transparencia.csv"):
    """Gera um CSV com todas as apostas resolvidas e o saldo financeiro."""
    try:
        with get_db_connection(db.db_path) as conn:
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

            # Contadores de remoção de vig (par Over/Under vs. fallback)
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN vig_removed = 1 THEN 1 ELSE 0 END), 0) AS with_vig,
                    COALESCE(SUM(CASE WHEN vig_removed = 0 THEN 1 ELSE 0 END), 0) AS fallback
                FROM opportunities
                WHERE status = 'RESOLVED'
            """)
            total, with_vig, fallback = cursor.fetchone()
            if total:
                fallback_pct = (fallback * 100.0) / total
                with_vig_pct = (with_vig * 100.0) / total
            else:
                fallback_pct = with_vig_pct = 0.0

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
            writer.writerow([])
            writer.writerow(["MONITORAMENTO DE VIG (par Over/Under)"])
            writer.writerow(["Apostas com vig removido (par encontrado)", f"{with_vig} ({with_vig_pct:.1f}%)"])
            writer.writerow(["Apostas com fallback (odd bruta)", f"{fallback} ({fallback_pct:.1f}%)"])
            if total:
                writer.writerow(["Taxa de fallback", f"{fallback_pct:.1f}%"])
            
        print(f"✅ Relatório comercial gerado com sucesso: {output_file}")
        print(f"💰 Saldo Atual Comprovado: {saldo_acumulado:+.2f}%")
        if total:
            print(
                f"🔎 Vig removido: {with_vig} ({with_vig_pct:.1f}%) | "
                f"Fallback: {fallback} ({fallback_pct:.1f}%)"
            )

    except Exception as e:
        logger.error("report_generation_failed", error=str(e))
        print(f"❌ Erro ao gerar o relatório: {str(e)}")

if __name__ == "__main__":
    generate_csv_report()
