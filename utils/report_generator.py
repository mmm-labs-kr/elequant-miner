import sqlite3
import pandas as pd
from pathlib import Path
import sys

# 프로젝트 루트 경로 설정
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
from utils.paths import DB_PATH

class ReportGenerator:
    def __init__(self, db_path=str(DB_PATH)):
        self.db_path = db_path

    def generate_summary(self):
        """전체 전략 현황 요약 리포트 생성"""
        conn = sqlite3.connect(self.db_path)
        
        # 전체 통계
        stats_query = """
            SELECT status, COUNT(*) as count 
            FROM alphas 
            GROUP BY status
        """
        stats_df = pd.read_sql_query(stats_query, conn)
        
        # 합격 전략 상세 (성능순)
        passed_query = """
            SELECT a.id, a.code, m.sharpe, m.fitness, m.turnover, a.created_at
            FROM alphas a
            JOIN metrics m ON a.id = m.alpha_id
            WHERE m.success_flag = 1
            ORDER BY m.sharpe DESC
        """
        passed_df = pd.read_sql_query(passed_query, conn)
        
        conn.close()
        
        report_path = PROJECT_ROOT / "research" / "summary_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# 📈 Elequant-Miner Research Summary Report\n\n")
            f.write(f"## 1. Overall Status\n")
            f.write(stats_df.to_markdown(index=False))
            f.write("\n\n")
            
            f.write(f"## 2. Top Performing Alphas (Passed Criteria)\n")
            if not passed_df.empty:
                f.write(passed_df.to_markdown(index=False))
            else:
                f.write("No alphas have passed the criteria yet.\n")
            
            f.write(f"\n\n*Report generated at: {pd.Timestamp.now()}*\n")
            
        print(f"Report generated at: {report_path}")
        return report_path

if __name__ == "__main__":
    gen = ReportGenerator()
    gen.generate_summary()
