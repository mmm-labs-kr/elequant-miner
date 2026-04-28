import sqlite3
from pathlib import Path

class DBManager:
    def __init__(self, db_path="research/elequant.db"):
        self.db_path = Path(db_path)
        self.init_db()

    def init_db(self):
        # 디렉토리가 없으면 생성
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Alphas table (user_hypothesis, hypothesis_text 추가)
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

        # 기존 테이블에 컬럼이 없는 경우를 위한 패치
        try:
            cursor.execute("ALTER TABLE alphas ADD COLUMN user_hypothesis INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE alphas ADD COLUMN hypothesis_text TEXT")
        except sqlite3.OperationalError:
            # 이미 컬럼이 존재함
            pass

        # Metrics table
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

        # Feedback table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                alpha_id INTEGER PRIMARY KEY,
                llm_analysis TEXT,
                improvement_plan TEXT,
                FOREIGN KEY (alpha_id) REFERENCES alphas (id)
            )
        ''')

        conn.commit()
        conn.close()
        print(f"Database initialized at {self.db_path}")

if __name__ == "__main__":
    db = DBManager()
