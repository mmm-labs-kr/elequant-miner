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
                                results['detailed'] = detailed # 상세 지표 주입
                                self._process_results(slot['alpha_id'], results)
                            
                            logging.info(f"Slot Complete: Alpha {slot['alpha_id']}")
                            slots.remove(slot)
                            
                        elif status in ["FAILED", "ERROR"]:
                            error_msg = data.get("message", "Unknown error")
                            logging.warning(f"Strategy Error (Alpha {slot['alpha_id']}): {error_msg}")
                            
                            if slot['attempt'] < 2: # 최대 3회 시도 (0, 1, 2)
                                logging.info(f"Retrying Alpha {slot['alpha_id']} with fix (Attempt {slot['attempt']+1})...")
                                fixed_code = self._generate_strategy(parent={'code': slot['code']}, error_msg=error_msg)
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
        # 관련 데이터 필드 검색 (사용자 명령어가 있으면 해당 키워드 우선 사용)
        search_keyword = "price"
        if self.user_directive:
            # 명령어에서 핵심 키워드 추출 (간단히 공백 기준)
            search_keyword = self.user_directive.split()[0]
        
        import random
        selected_fields = self.ai.search_fields(search_keyword, limit=20)
        fields_context = "\nAvailable Data Fields for reference:\n" + "\n".join([f"- {f['id']}: {f['description']}" for f in selected_fields])

        if error_msg:
            prompt = f"""
            The following FASTEXPR code produced an error in WorldQuant Brain:
            Code: {parent['code'] if parent else 'Previous code'}
            Error: {error_msg}

            Please fix the syntax or logical error.
            {fields_context}
            Only return the fixed raw FASTEXPR code.
            """
        elif self.user_directive and not parent:
            # 사용자가 특정 연구 테마를 입력한 경우 (최우선 순위)
            prompt = f"""
            Research Topic: {self.user_directive}
            
            Based on this topic, develop a high-quality WorldQuant Alpha factor using FASTEXPR.
            Focus on the relationships between relevant indicators.
            {fields_context}
            """
        elif parent:
            prompt = f"""
            Based on the following successful alpha:
            Code: {parent['code']}
            Stats: Sharpe {parent['sharpe']}, Fitness {parent['fitness']}, Turnover {parent['turnover']}

            Improve this alpha. {"(Focus: " + self.user_directive + ")" if self.user_directive else ""}
            {fields_context}
            """
        else:
            theme = random.choice(["Mean Reversion", "Momentum", "Value", "Quality", "Growth"])
            prompt = f"Create a new alpha factor focusing on {theme} using WorldQuant FASTEXPR.\n{fields_context}"

        logging.info(f"Generating {'fix' if error_msg else ('directed' if self.user_directive else 'auto')} strategy...")
        return self.ai.generate_alpha(prompt)

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
        """시뮬레이션 결과 분석 및 DB 저장 (연도별 일관성 체크 포함)"""
        # 에러 메시지 확인
        if results.get('status') in ['FAILED', 'ERROR']:
            error_msg = results.get('message', 'Unknown error')
            logging.error(f"Alpha {alpha_id} failed: {error_msg}")
            self._update_alpha_status(alpha_id, f"FAILED: {error_msg[:50]}")
            return False

        is_stats = results.get('is', {})
        sharpe = is_stats.get('sharpe', 0)
        fitness = is_stats.get('fitness', 0)
        turnover = is_stats.get('turnover', 0)
        margin = is_stats.get('margin', 0)
        
        # 연도별 일관성 체크 (Yearly Consistency)
        yearly_pass = True
        detailed = results.get('detailed', {})
        if detailed and 'years' in detailed:
            for year_data in detailed['years']:
                y_sharpe = year_data.get('sharpe', 0)
                if y_sharpe < self.criteria['yearly_sharpe_min']:
                    yearly_pass = False
                    logging.info(f"Alpha {alpha_id} failed yearly check: {year_data.get('year')} Sharpe {y_sharpe}")
                    break

        correlations = is_stats.get('correlations', [])
        max_corr = max([c.get('value', 0) for c in correlations]) if correlations else 0
        
        success = (sharpe >= self.criteria['sharpe'] and 
                   fitness >= self.criteria['fitness'] and 
                   self.criteria['turnover_min'] <= turnover <= self.criteria['turnover_max'] and
                   max_corr < self.criteria['correlation_max'] and
                   yearly_pass)

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Metrics 저장
        cursor.execute("""
            INSERT INTO metrics (alpha_id, sharpe, turnover, fitness, margin, success_flag)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (alpha_id, sharpe, turnover, fitness, margin, 1 if success else 0))
        
        # 상태 업데이트
        status = "PASSED" if success else "REJECTED"
        if max_corr >= self.criteria['correlation_max']:
            status = "REJECTED_BY_CORR"
            
        cursor.execute("UPDATE alphas SET status = ? WHERE id = ?", (status, alpha_id))
        
        # 피드백 생성
        feedback = f"Sharpe: {sharpe}, Max Corr: {max_corr}, Status: {status}"
        cursor.execute("INSERT INTO feedback (alpha_id, llm_analysis) VALUES (?, ?)", (alpha_id, feedback))
        
        conn.commit()
        conn.close()
        
        logging.info(f"Alpha {alpha_id} processed. Status: {status}, Sharpe: {sharpe}")

    def _get_best_parent(self):
        """DB에서 가장 성과가 좋은 합격 전략 중 하나를 선택하여 진화의 기반으로 삼음"""
        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.id, a.code, m.sharpe, m.fitness, m.turnover 
            FROM alphas a JOIN metrics m ON a.id = m.alpha_id 
            WHERE m.success_flag = 1 
            ORDER BY m.sharpe DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if rows:
            import random
            return rows[random.randint(0, len(rows)-1)]
        return None

if __name__ == "__main__":
    email = os.getenv("WQ_EMAIL")
    password = os.getenv("WQ_PASSWORD")
    
    if not email or not password:
        print("Error: WQ_EMAIL or WQ_PASSWORD not found in environment variables.")
        print("Please check your .env file or environment settings.")
    else:
        miner = ElequantMiner(email, password)
        miner.run()
