import random
import time
import logging
import sys
import sqlite3
import os
from pathlib import Path
from dotenv import load_dotenv

# 내부 모듈 임포트
from core.db_manager import DBManager
from core.ai_engine import GeminiEngine
from core.api_client import WQClient
from utils.paths import DB_PATH, ENV_FILE, LOGS_DIR

# 프로그램 시작 시 .env 로드 (단 한 번만 수행)
load_dotenv(ENV_FILE)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / "miner.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

class ElequantMiner:
    def __init__(self, email, password):
        self.db = DBManager(str(DB_PATH))
        self.ai = GeminiEngine()
        self.wq = WQClient()
        self.email = email
        self.password = password
        self.user_directive = None # 오늘의 특수 연구 명령
        
        # 합격 기준 설정 (IQC 기준 바탕)
        self.criteria = {
            "sharpe": 1.25,
            "fitness": 1.0,
            "turnover_min": 1.0,
            "turnover_max": 70.0,
            "correlation_max": 0.7,
            "yearly_sharpe_min": 0.1 # 연도별 최소 Sharpe 기준
        }

    def run(self):
        """메인 마이닝 루프 (3개 슬롯 병렬 관리)"""
        # 시작 시 사용자 연구 테마 입력받기
        print("\n" + "="*50)
        print("💡 Elequant-Miner Research Guide Mode")
        print("오늘 연구하고 싶은 테마나 지표 명령어를 입력하세요.")
        print("(예: '배당수익률과 부채비율을 엮어줘', 'RSI 리버전 전략 집중 연구')")
        print("그냥 엔터를 치면 'Full Auto' 모드로 동작합니다.")
        print("="*50)
        self.user_directive = input("입력: ").strip() or None

        if not self.wq.login(self.email, self.password):
            logging.error("Failed to initialize miner due to login failure.")
            return

        logging.info("🚀 Elequant-Miner started successfully! (Parallel Slots: 3)")
        
        # 슬롯 관리 (최대 3개)
        # 각 슬롯: {'sim_url': str, 'alpha_id': int, 'attempt': int, 'code': str, 'parent_alpha': dict}
        slots = []
        max_slots = 3

        try:
            while True:
                # 1. 빈 슬롯 채우기
                while len(slots) < max_slots:
                    parent_alpha = self._get_best_parent()
                    alpha_code = self._generate_strategy(parent_alpha)
                    
                    if not alpha_code:
                        logging.warning("Strategy generation paused due to API limits. Waiting 60s...")
                        time.sleep(60) # 대기 시간 30s -> 60s로 증가
                        break 

                    alpha_id = self._save_alpha(alpha_code, parent_alpha['id'] if parent_alpha else None)
                    sim_url = self.wq.simulate(alpha_code)
                    
                    if sim_url:
                        slots.append({
                            'sim_url': sim_url,
                            'alpha_id': alpha_id,
                            'attempt': 0,
                            'code': alpha_code,
                            'parent_alpha': parent_alpha
                        })
                        # 슬롯 채우기 간격 추가 (API 과부하 방지)
                        time.sleep(15) # 10s -> 15s로 증가하여 더 여유 있게 운영
                    else:
                        logging.error(f"Failed to start simulation for Alpha {alpha_id}")
                
                # 2. 모든 슬롯 상태 체크 (진행률 확인)
                for slot in slots[:]: # 복사본으로 루프 (항목 제거 대비)
                    try:
                        # WQ 서버 상태 확인 (비동기처럼 작동하도록 1회만 get 수행)
                        response = self.wq.session.get(slot['sim_url'])
                        if response.status_code != 200:
                            logging.error(f"Slot Error ({response.status_code}): {slot['sim_url']}")
                            slots.remove(slot)
                            continue
                            
                        data = response.json()
                        progress = data.get("progress", 0)
                        status = data.get("status")
                        
                        if progress == 1.0:
                            alpha_wq_id = data.get("alpha")
                            results = self.wq.get_alpha_results(alpha_wq_id)
                            detailed = self.wq.get_detailed_stats(alpha_wq_id)
                            
                            if results:
                                results['detailed'] = detailed
                                results['_code'] = slot['code']  # LLM 분석용
                                self._process_results(slot['alpha_id'], results)
                            
                            logging.info(f"Slot Complete: Alpha {slot['alpha_id']}")
                            slots.remove(slot)
                            
                        elif status in ["FAILED", "ERROR"]:
                            error_msg = data.get("message", "Unknown error")
                            logging.warning(f"Strategy Error (Alpha {slot['alpha_id']}): {error_msg}")
                            
                            if slot['attempt'] < 2: # 최대 3회 시도 (0, 1, 2)
                                logging.info(f"Retrying Alpha {slot['alpha_id']} with fix (Attempt {slot['attempt']+1}), quota left: {self.ai.daily_remaining}...")
                                fixed_code = self._generate_strategy(
                                    parent={'code': slot['code']}, error_msg=error_msg
                                )
                                if fixed_code:
                                    new_sim_url = self.wq.simulate(fixed_code)
                                    if new_sim_url:
                                        slot['sim_url'] = new_sim_url
                                        slot['code'] = fixed_code
                                        slot['attempt'] += 1
                                        continue
                            
                            # 재시도 실패 또는 한도 초과
                            self._update_alpha_status(slot['alpha_id'], f"FAILED: {error_msg[:50]}")
                            slots.remove(slot)
                        else:
                            # 아직 진행 중
                            pass

                    except Exception as e:
                        logging.error(f"Error checking slot: {e}")
                
                if not slots:
                    time.sleep(10) # 모든 슬롯이 비었으면 잠시 대기
                else:
                    time.sleep(15) # 슬롯 체크 간격

        except KeyboardInterrupt:
            logging.info("\n🛑 Stop signal received. Shutting down Elequant-Miner...")
            logging.info(f"Ongoing simulations: {len(slots)} are still running on WQ server.")
            logging.info("You can check their progress on the WQ Brain website later.")

    def _generate_strategy(self, parent=None, error_msg=None):
        """Gemini를 사용하여 전략 생성 또는 에러 수정 (사용자 명령 반영)"""


        # 사용자 지시어에서 키워드 추출, 없으면 None → 엔진의 카테고리 로테이션 사용
        directive_keyword = self.user_directive.split()[0] if self.user_directive else None
        selected_fields = self.ai.search_fields(directive_keyword, limit=25)
        fields_context = "\nAvailable Data Fields:\n" + "\n".join(
            [f"- {f['id']}: {f['description']}" for f in selected_fields]
        )

        is_fix = bool(error_msg)

        if error_msg:
            prompt = f"""\
FASTEXPR code that caused an error in WorldQuant Brain:
Code: {parent['code'] if parent else 'N/A'}
Error: {error_msg}

Fix the error. Return ONLY the corrected raw FASTEXPR expression.
{fields_context}"""

        elif self.user_directive and not parent:
            prompt = f"""\
Research Topic: {self.user_directive}

Create a high-quality WorldQuant Alpha factor using FASTEXPR based on this topic.
Combine the most relevant data fields in a non-trivial, statistically motivated way.
Return ONLY the raw FASTEXPR expression.
{fields_context}"""

        elif parent:
            keys = parent.keys() if hasattr(parent, 'keys') else parent.keys()
            sharpe   = parent['sharpe']   if 'sharpe'   in keys else '?'
            fitness  = parent['fitness']  if 'fitness'  in keys else '?'
            turnover = parent['turnover'] if 'turnover' in keys else '?'
            analysis = parent['llm_analysis'] if 'llm_analysis' in keys and parent['llm_analysis'] else None
            analysis_section = f"\nPrevious analysis of this strategy:\n{analysis}\n" if analysis else ""

            prompt = f"""\
Successful alpha to evolve:
Code: {parent['code']}
Sharpe: {sharpe}, Fitness: {fitness}, Turnover: {turnover}
{analysis_section}
Evolve this alpha using the analysis above as a guide.
Possible improvements: change lookback windows, add sector neutralization,
combine with a complementary signal, or substitute higher-quality data fields.
{"Focus area: " + self.user_directive if self.user_directive else ""}
Return ONLY the evolved raw FASTEXPR expression.
{fields_context}"""

        else:
            themes = [
                "Short-term Mean Reversion using price and volume",
                "Earnings Quality factor using fundamental data",
                "Momentum with volatility scaling",
                "Sector-neutral Value factor",
                "Liquidity-adjusted Growth factor",
            ]
            theme = random.choice(themes)
            prompt = f"""\
Create a novel WorldQuant Alpha factor based on: {theme}
Use 2-3 data fields in combination. Make it non-trivial and statistically motivated.
Return ONLY the raw FASTEXPR expression.
{fields_context}"""

        mode = 'fix' if is_fix else ('directed' if self.user_directive else 'auto')
        remaining = self.ai.daily_remaining
        logging.info(f"Generating [{mode}] strategy — daily quota left: gen={remaining['gemini-2.5-flash']}, fix={remaining['gemini-2.5-flash-lite']}")
        return self.ai.generate_alpha(prompt, is_fix=is_fix)

    def _save_alpha(self, code, parent_id):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        user_hypo = 1 if self.user_directive else 0
        cursor.execute("""
            INSERT INTO alphas (code, parent_id, user_hypothesis, hypothesis_text) 
            VALUES (?, ?, ?, ?)
        """, (code, parent_id, user_hypo, self.user_directive))
        alpha_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return alpha_id

    def _update_alpha_status(self, alpha_id, status):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE alphas SET status = ? WHERE id = ?", (status, alpha_id))
        conn.commit()
        conn.close()

    def _process_results(self, alpha_id, results):
        """시뮬레이션 결과 분석 및 DB 저장."""
        if results.get('status') in ['FAILED', 'ERROR']:
            error_msg = results.get('message', 'Unknown error')
            logging.error(f"Alpha {alpha_id} simulation error: {error_msg}")
            self._update_alpha_status(alpha_id, f"FAILED: {error_msg[:50]}")
            return False

        is_stats = results.get('is', {})
        sharpe   = is_stats.get('sharpe', 0)
        fitness  = is_stats.get('fitness', 0)
        turnover = is_stats.get('turnover', 0)
        margin   = is_stats.get('margin', 0)

        # 연도별 일관성 체크
        yearly_pass = True
        detailed = results.get('detailed') or {}
        if 'years' in detailed:
            for year_data in detailed['years']:
                y_sharpe = year_data.get('sharpe', 0)
                if y_sharpe < self.criteria['yearly_sharpe_min']:
                    yearly_pass = False
                    logging.info(
                        f"Alpha {alpha_id} yearly fail: "
                        f"{year_data.get('year')} Sharpe={y_sharpe:.3f}"
                    )
                    break

        correlations = is_stats.get('correlations', [])
        max_corr = max((c.get('value', 0) for c in correlations), default=0)

        # 기준별 통과 여부 계산
        checks = {
            "Sharpe":   sharpe  >= self.criteria['sharpe'],
            "Fitness":  fitness >= self.criteria['fitness'],
            "Turnover": self.criteria['turnover_min'] <= turnover <= self.criteria['turnover_max'],
            "MaxCorr":  max_corr < self.criteria['correlation_max'],
            "Yearly":   yearly_pass,
        }
        success = all(checks.values())

        # 실패 기준 상세 로그
        failed = [k for k, v in checks.items() if not v]
        if failed:
            logging.info(
                f"Alpha {alpha_id} REJECTED — failed: {', '.join(failed)} | "
                f"Sharpe={sharpe:.3f}, Fitness={fitness:.3f}, "
                f"Turnover={turnover:.1f}%, MaxCorr={max_corr:.3f}"
            )

        # 합격 전략 품질 등급 (A: 우수 / B: 양호 / C: 기준 충족)
        if success:
            if sharpe >= 1.6 and 10 <= turnover <= 50:
                status = "PASSED_A"
            elif sharpe >= 1.4:
                status = "PASSED_B"
            else:
                status = "PASSED_C"
        elif max_corr >= self.criteria['correlation_max']:
            status = "REJECTED_BY_CORR"
        else:
            status = "REJECTED"

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO metrics (alpha_id, sharpe, turnover, fitness, margin, success_flag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (alpha_id, sharpe, turnover, fitness, margin, 1 if success else 0)
        )
        cursor.execute("UPDATE alphas SET status = ? WHERE id = ?", (status, alpha_id))
        # 빈 feedback 행 선점 (LLM 분석 결과는 비동기로 채워짐)
        cursor.execute("INSERT OR IGNORE INTO feedback (alpha_id) VALUES (?)", (alpha_id,))
        conn.commit()
        conn.close()

        logging.info(
            f"Alpha {alpha_id} → {status} | "
            f"Sharpe={sharpe:.3f}, Fitness={fitness:.3f}, "
            f"Turnover={turnover:.1f}%, MaxCorr={max_corr:.3f}"
        )

        # LLM 결과 분석 및 DB 저장 (루프를 막지 않도록 결과만 저장)
        self._store_llm_analysis(alpha_id, results.get('_code', ''), sharpe, fitness,
                                 turnover, margin, max_corr, yearly_pass, status, checks)
        return success

    def _store_llm_analysis(self, alpha_id, code, sharpe, fitness,
                            turnover, margin, max_corr, yearly_pass, status, checks):
        """LLM에게 결과를 분석시키고 feedback 테이블에 저장."""
        failed_lines = [
            f"  - {k}: {v}" for k, v in {
                "Sharpe": f"{sharpe:.3f} (need ≥{self.criteria['sharpe']})",
                "Fitness": f"{fitness:.3f} (need ≥{self.criteria['fitness']})",
                "Turnover": f"{turnover:.1f}% (need {self.criteria['turnover_min']}-{self.criteria['turnover_max']}%)",
                "MaxCorr": f"{max_corr:.3f} (need <{self.criteria['correlation_max']})",
                "Yearly Sharpe": f"{'pass' if yearly_pass else 'fail'}",
            }.items() if not checks.get(k.split()[0], True)
        ]
        failed_section = ("Failed criteria:\n" + "\n".join(failed_lines)) if failed_lines else "All criteria passed."

        prompt = f"""\
Alpha FASTEXPR Code:
{code if code else '(not recorded)'}

Result: {status}
Sharpe={sharpe:.3f}, Fitness={fitness:.3f}, Turnover={turnover:.1f}%, Margin={margin:.4f}, MaxCorr={max_corr:.3f}
{failed_section}

Analyze: What structural feature of this alpha drove the result?
What specific sub-expression changes would most improve the weakest metric?"""

        analysis = self.ai.analyze_result(prompt)
        if analysis:
            conn = sqlite3.connect(self.db.db_path)
            conn.execute(
                "UPDATE feedback SET llm_analysis = ? WHERE alpha_id = ?",
                (analysis, alpha_id)
            )
            conn.commit()
            conn.close()
            logging.info(f"Alpha {alpha_id} LLM analysis stored.")

    def _get_best_parent(self):
        """합격 전략 중 품질 점수 상위 5개에서 가중 랜덤 선택.

        quality_score = Sharpe × Fitness / (1 + |turnover - 25| / 25)
        — Sharpe와 Fitness가 높고 Turnover가 25% 근처일수록 높은 점수.
        """
        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.id, a.code, m.sharpe, m.fitness, m.turnover, f.llm_analysis,
                   (m.sharpe * m.fitness) / (1.0 + ABS(m.turnover - 25.0) / 25.0) AS quality_score
            FROM alphas a
            JOIN metrics m ON a.id = m.alpha_id
            LEFT JOIN feedback f ON a.id = f.alpha_id
            WHERE m.success_flag = 1
            ORDER BY quality_score DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        # 상위 전략일수록 더 자주 선택되도록 가중치 부여 (rank 1 → weight 5, rank 5 → weight 1)
        weights = list(range(len(rows), 0, -1))
        total = sum(weights)
        r = random.random() * total
        cumulative = 0
        for row, w in zip(rows, weights):
            cumulative += w
            if r <= cumulative:
                return row
        return rows[0]

if __name__ == "__main__":
    email = os.getenv("WQ_EMAIL")
    password = os.getenv("WQ_PASSWORD")
    
    if not email or not password:
        print("Error: WQ_EMAIL or WQ_PASSWORD not found in environment variables.")
        print("Please check your .env file or environment settings.")
    else:
        miner = ElequantMiner(email, password)
        miner.run()
