import os
import time
import json
from google import genai
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from utils.paths import ENV_FILE, OPERATORS_JSON, DATAFIELDS_JSON

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class GeminiEngine:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = "gemini-2.5-flash-lite"
        
        # Throttling 관련 변수 (RPM 10 타겟)
        self.requests_log = []
        self.max_requests_per_minute = 10  # 15회 중 10회만 사용하여 안전 확보
        self.last_request_time = datetime.now() - timedelta(seconds=20)
        self.min_interval = 6  # 요청 간 최소 6초 대기 (이전 22초에서 대폭 단축)
        
        # 지식 베이스 로드
        with open(OPERATORS_JSON, 'r', encoding='utf-8') as f:
            self.operators = json.load(f)
        with open(DATAFIELDS_JSON, 'r', encoding='utf-8') as f:
            self.datafields = json.load(f)

    def _wait_for_rate_limit(self):
        """지능형 속도 조절 (에러 없을 땐 빠르게, 한도 근접 시 대기)"""
        now = datetime.now()
        
        # 1. 최소 간격 (버스트 방지용 - 아주 짧게)
        elapsed = (now - self.last_request_time).total_seconds()
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
            now = datetime.now()

        # 2. 분당 호출 횟수 체크
        self.requests_log = [t for t in self.requests_log if now - t < timedelta(minutes=1)]
        if len(self.requests_log) >= self.max_requests_per_minute:
            wait_time = 60 - (now - self.requests_log[0]).total_seconds()
            if wait_time > 0:
                logging.info(f"⚡ Near Quota Limit: Pausing for {wait_time:.1f}s to stay safe...")
                time.sleep(wait_time + 1)
        
        self.last_request_time = datetime.now()
        self.requests_log.append(self.last_request_time)

    def search_fields(self, keyword, limit=30):
        """키워드 기반 데이터 필드 검색"""
        results = [f for f in self.datafields if keyword.lower() in f['description'].lower() or keyword.lower() in f['id'].lower()]
        return results[:limit]

    def generate_alpha(self, prompt_context):
        """전략 생성 메인 루프 (429 에러 시 조용히 대기)"""
        max_retries = 3
        for attempt in range(max_retries):
            self._wait_for_rate_limit()
            
            # ... (system_instruction 생략 - 이전과 동일하게 유지)
            # (중략된 부분은 replace 도구가 알아서 처리하도록 context 유지)
            
            try:
                # (중략된 로직 내부)
                system_instruction = f"""
                You are an expert Quantitative Researcher at WorldQuant. 
                Task: Develop high-quality Alpha factors using WorldQuant's FASTEXPR language.
                
                FASTEXPR Syntax Rules:
                1. Basic: +, -, *, /, ^, log(x), exp(x), abs(x).
                2. Time-series (must have lookback 't'): ts_sum(x, t), ts_mean(x, t), stddev(x, t), ts_rank(x, t), ts_delta(x, t), ts_delay(x, t).
                3. Cross-sectional: rank(x), scale(x), group_mean(x, g), group_zscore(x, g).
                4. Logic: if_else(condition, true_val, false_val).
                
                Critical Rules:
                - Use "stddev(x, t)" for time-series standard deviation (NOT ts_stddev).
                - Use math symbols (+, -, *, /) directly.
                - Ensure all open parentheses are closed.
                - Provide ONLY the raw FASTEXPR string.
                """
                
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt_context,
                    config={'system_instruction': system_instruction}
                )
                raw_code = response.text.strip()
                
                # Cleanup
                import re
                clean_code = re.sub(r'```(?:[a-zA-Z]+)?\n?', '', raw_code).replace('```', '').strip()
                clean_code = clean_code.strip("'").strip('"')
                
                # 자동 치환 (Safety Layer)
                clean_code = re.sub(r'ts_stddev\(', r'stddev(', clean_code)
                clean_code = re.sub(r'\bstdev\(', r'stddev(', clean_code)
                clean_code = re.sub(r'mul\(([^,]+),\s*([^)]+)\)', r'(\1 * \2)', clean_code)
                clean_code = re.sub(r'div\(([^,]+),\s*([^)]+)\)', r'(\1 / \2)', clean_code)
                clean_code = re.sub(r'add\(([^,]+),\s*([^)]+)\)', r'(\1 + \2)', clean_code)
                clean_code = re.sub(r'sub\(([^,]+),\s*([^)]+)\)', r'(\1 - \2)', clean_code)
                
                return clean_code
                
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    wait_sec = 40 * (attempt + 1)
                    logging.info(f"⏳ Gemini is busy (429). Resting for {wait_sec}s before trying again...")
                    time.sleep(wait_sec)
                else:
                    logging.error(f"Gemini API Error: {e}")
                    return None
        return None

if __name__ == "__main__":
    engine = GeminiEngine()
    # 테스트용 검색
    fields = engine.search_fields("cashflow")
    print(f"Found {len(fields)} cashflow fields.")
