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

        # Tracks parent→child lineage for strategy evolution trees
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

        # Indexes for the most frequent query patterns
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alphas_status ON alphas (status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alphas_parent ON alphas (parent_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_success ON metrics (success_flag)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_sharpe ON metrics (sharpe DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage (parent_id)')

        # Schema migration: add columns if they were missing in an older DB
        for col, ddl in [
            ("user_hypothesis", "INTEGER DEFAULT 0"),
            ("hypothesis_text", "TEXT"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE alphas ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass

        conn.commit()
        conn.close()
        print(f"Database initialized at {self.db_path}")


if __name__ == "__main__":
    db = DBManager()
