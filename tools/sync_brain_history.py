"""
WQ Brain 전략 전체 동기화 스크립트
- 웹/API로 돌린 모든 알파를 로컬 DB + dedup에 임포트
- 이미 DB에 있는 것은 건너뜀 (wq_alpha_id 기준)
사용: python tools/sync_brain_history.py
"""
import sys
import os
import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# PROJECT_ROOT를 sys.path에 추가한 뒤에야 로컬 패키지를 임포트할 수 있음
from dotenv import load_dotenv          # noqa: E402
from utils.paths import ENV_FILE, DATA_DIR, DB_PATH  # noqa: E402
from utils.dedup_manager import DedupManager         # noqa: E402
from core.api_client import WQClient                 # noqa: E402
from core.db_manager import DBManager                # noqa: E402

load_dotenv(ENV_FILE)

CRITERIA = {"sharpe": 1.25, "fitness": 1.0, "turnover_min": 1.0, "turnover_max": 70.0}


def compute_status(sharpe, fitness, turnover) -> tuple[str, int]:
    """metrics로 우리 status 체계 계산. (status, success_flag) 반환."""
    passed = (
        sharpe   >= CRITERIA["sharpe"]
        and fitness  >= CRITERIA["fitness"]
        and CRITERIA["turnover_min"] <= turnover <= CRITERIA["turnover_max"]
    )
    return ("PASSED", 1) if passed else ("REJECTED", 0)


def compute_quality_score(sharpe, fitness, turnover) -> float:
    if sharpe > 0 and fitness > 0:
        return round((sharpe * fitness) / (1.0 + abs(turnover - 25) / 25.0), 4)
    return 0.0


def fetch_all_alphas(wq: WQClient) -> list[dict]:
    results = []
    url = f"{wq.base_url}/users/self/alphas?limit=100"
    while url:
        r = wq.session.get(url)
        if r.status_code == 401 and wq._relogin_if_needed(r):
            continue
        if r.status_code != 200:
            print(f"  FAILED {r.status_code}")
            break
        data = r.json()
        batch = data.get("results", [])
        results.extend(batch)
        next_url = data.get("next")
        if next_url and next_url.startswith("http://"):
            next_url = "https://" + next_url[7:]
        url = next_url
        print(f"  수신: {len(results)}/{data.get('count', '?')}개")
    return results


def get_existing_wq_ids(conn) -> set:
    rows = conn.execute("SELECT wq_alpha_id FROM alphas WHERE wq_alpha_id IS NOT NULL").fetchall()
    return {r[0] for r in rows}


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    email = os.getenv("WQ_EMAIL")
    password = os.getenv("WQ_PASSWORD")
    if not email or not password:
        print("WQ_EMAIL / WQ_PASSWORD not set in .env")
        return

    DBManager(str(DB_PATH))

    wq = WQClient()
    if not wq.login(email, password):
        print("Login failed.")
        return

    print("WQ Brain 알파 목록 가져오는 중...")
    alphas = fetch_all_alphas(wq)
    print(f"총 {len(alphas)}개 수신\n")
    if not alphas:
        return

    conn = sqlite3.connect(DB_PATH)
    dedup = DedupManager(DATA_DIR / "shared_tried.json")
    existing_wq_ids = get_existing_wq_ids(conn)

    added_db = skipped_db = no_code = no_metrics = 0

    for alpha in alphas:
        wq_id = alpha.get("id")
        if not wq_id:
            continue

        # 이미 DB에 있으면 스킵
        if wq_id in existing_wq_ids:
            skipped_db += 1
            continue

        # FASTEXPR 코드 추출
        regular = alpha.get("regular", {})
        code = regular.get("code") if isinstance(regular, dict) else (regular if isinstance(regular, str) else None)
        if not code or not code.strip():
            no_code += 1
            continue

        # metrics 추출
        is_stats = alpha.get("is") or {}
        sharpe   = is_stats.get("sharpe")
        fitness  = is_stats.get("fitness")
        turnover = is_stats.get("turnover")
        margin   = is_stats.get("margin", 0) or 0

        # WQ Brain API는 turnover를 소수(0~1)로 반환 → % 단위로 변환
        if turnover is not None and turnover < 1.0:
            turnover = turnover * 100

        if sharpe is None or fitness is None or turnover is None:
            no_metrics += 1
            # metrics 없어도 코드는 dedup에 추가
            dedup.add(code)
            continue

        status, success_flag = compute_status(sharpe, fitness, turnover)
        quality_score = compute_quality_score(sharpe, fitness, turnover) if success_flag else 0.0
        created_at = alpha.get("dateCreated", "")[:19].replace("T", " ")

        cur = conn.execute(
            """INSERT INTO alphas (code, status, source, wq_alpha_id, created_at)
               VALUES (?, ?, 'brain_web', ?, ?)""",
            (code.strip(), status, wq_id, created_at or None)
        )
        local_id = cur.lastrowid

        conn.execute(
            """INSERT OR IGNORE INTO metrics
               (alpha_id, sharpe, turnover, fitness, margin, quality_score, success_flag)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (local_id, sharpe, turnover, fitness, margin, quality_score, success_flag)
        )
        conn.execute("INSERT OR IGNORE INTO feedback (alpha_id) VALUES (?)", (local_id,))

        dedup.add(code)
        added_db += 1

    conn.commit()
    conn.close()

    print(f"DB 신규 추가:  {added_db}개")
    print(f"DB 이미 존재: {skipped_db}개")
    print(f"코드 없음:    {no_code}개")
    print(f"metrics 없음: {no_metrics}개")
    print(f"dedup 현재:   {dedup.count}개")


if __name__ == "__main__":
    main()
