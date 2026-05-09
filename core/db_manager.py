import sqlite3
from pathlib import Path


class DBManager:
    def __init__(self, db_path="research/elequant.db"):
        self.db_path = Path(db_path)
        self.init_db()

    def init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alphas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                parent_id INTEGER,
                user_hypothesis INTEGER DEFAULT 0,
                hypothesis_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES alphas (id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                alpha_id INTEGER PRIMARY KEY,
                sharpe REAL,
                turnover REAL,
                fitness REAL,
                margin REAL,
                drawdown REAL,
                returns REAL,
                sub_sharpe REAL,
                max_corr REAL,
                quality_score REAL,
                failed_checks TEXT,
                region TEXT,
                universe TEXT,
                success_flag INTEGER,
                FOREIGN KEY (alpha_id) REFERENCES alphas (id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                alpha_id INTEGER PRIMARY KEY,
                llm_analysis TEXT,
                improvement_plan TEXT,
                FOREIGN KEY (alpha_id) REFERENCES alphas (id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL,
                child_id INTEGER NOT NULL,
                generation INTEGER DEFAULT 1,
                FOREIGN KEY (parent_id) REFERENCES alphas (id),
                FOREIGN KEY (child_id) REFERENCES alphas (id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS yearly_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alpha_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                sharpe REAL,
                turnover REAL,
                fitness REAL,
                returns REAL,
                drawdown REAL,
                margin REAL,
                long_count INTEGER,
                short_count INTEGER,
                UNIQUE(alpha_id, year),
                FOREIGN KEY (alpha_id) REFERENCES alphas (id)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alphas_status ON alphas (status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alphas_parent ON alphas (parent_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_success ON metrics (success_flag)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_sharpe ON metrics (sharpe DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage (parent_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_yearly_alpha ON yearly_metrics (alpha_id)')

        for col, ddl in [
            ("user_hypothesis",   "INTEGER DEFAULT 0"),
            ("hypothesis_text",   "TEXT"),
            ("sim_url",           "TEXT"),
            ("wq_alpha_id",       "TEXT"),
            ("source",            "TEXT DEFAULT 'miner'"),
            ("nearmiss_attempts", "INTEGER DEFAULT 0"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE alphas ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass

        for col, ddl in [
            ("returns",       "REAL"),
            ("sub_sharpe",    "REAL"),
            ("max_corr",      "REAL"),
            ("quality_score", "REAL"),
            ("failed_checks", "TEXT"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE metrics ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass

        # 기존 PASSED_A/B/C → PASSED 정규화
        cursor.execute(
            "UPDATE alphas SET status = 'PASSED' WHERE status IN ('PASSED_A', 'PASSED_B', 'PASSED_C')"
        )

        # 기존 PASSED 행에 quality_score 백필
        cursor.execute("""
            UPDATE metrics
            SET quality_score = ROUND(
                (sharpe * fitness) / (1.0 + ABS(turnover - 25) / 25.0), 4
            )
            WHERE success_flag = 1
              AND sharpe > 0 AND fitness > 0
              AND quality_score IS NULL
        """)

        # REJECTED 행에 failed_checks 백필 (수치로 판단 가능한 4개 체크)
        cursor.execute("""
            UPDATE metrics
            SET failed_checks = RTRIM(
                CASE WHEN sharpe   <  1.25 THEN 'LOW_SHARPE,'   ELSE '' END ||
                CASE WHEN fitness  <  1.0  THEN 'LOW_FITNESS,'  ELSE '' END ||
                CASE WHEN turnover <  1.0  THEN 'LOW_TURNOVER,' ELSE '' END ||
                CASE WHEN turnover > 70.0  THEN 'HIGH_TURNOVER,' ELSE '' END,
                ','
            )
            WHERE success_flag = 0
              AND (failed_checks IS NULL OR failed_checks = '')
              AND sharpe IS NOT NULL AND fitness IS NOT NULL AND turnover IS NOT NULL
        """)

        conn.commit()
        conn.close()
        print(f"Database initialized at {self.db_path}")


if __name__ == "__main__":
    db = DBManager()
