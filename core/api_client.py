import requests
import json
import time
import webbrowser
import logging
from pathlib import Path
from dotenv import load_dotenv
import os
import sys

# 프로젝트 루트 경로 추가 및 경로 임포트
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
from utils.paths import ENV_FILE

class WQClient:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://api.worldquantbrain.com"
        self.email = None
        self.password = None
        self.is_logged_in = False
        
    def login(self, email, password):
        """WorldQuant Brain 로그인 및 Persona 인증 처리"""
        self.email = email
        self.password = password
        
        auth_url = f"{self.base_url}/authentication"
        response = self.session.post(auth_url, auth=(self.email, self.password))
        
        if response.status_code == 201:
            logging.info("Successfully logged in to WorldQuant Brain.")
            self.is_logged_in = True
            return True
        elif response.status_code == 401:
            resp_json = response.json()
            if 'inquiry' in resp_json:
                # Persona 생체 인증 필요 시 브라우저 자동 실행
                persona_url = f"https://api.worldquantbrain.com/authentication/persona?inquiry={resp_json['inquiry']}"
                logging.warning("🔐 Biometric authentication (Persona) required!")
                logging.info(f"Opening browser for authentication: {persona_url}")
                
                webbrowser.open(persona_url)
                input("Please complete authentication in your browser and press Enter here...")
                
                # 인증 완료 후 재시도
                return self.login(self.email, self.password)
            else:
                logging.error(f"Login failed: {resp_json}")
                return False
        else:
            logging.error(f"Unexpected error during login: {response.status_code}")
            return False

    def simulate(self, alpha_code, settings=None):
        """알파 시뮬레이션 요청"""
        if not self.is_logged_in:
            logging.error("Not logged in. Call login() first.")
            return None

        default_settings = {
            "instrumentType": "EQUITY",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 6,
            "neutralization": "SUBINDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
            "unitHandling": "VERIFY",
            "nanHandling": "OFF",
            "language": "FASTEXPR",
            "visualization": False
        }
        if settings:
            default_settings.update(settings)

        sim_url = f"{self.base_url}/simulations"
        payload = {
            "type": "REGULAR",
            "settings": default_settings,
            "regular": alpha_code
        }

        response = self.session.post(sim_url, json=payload)
        
        if response.status_code == 201:
            sim_id_url = response.headers.get("Location")
            logging.info(f"Simulation started: {sim_id_url}")
            return sim_id_url
        elif response.status_code == 401:
            logging.info("Session expired. Re-logging...")
            if self.login(self.email, self.password):
                return self.simulate(alpha_code, settings)
        else:
            logging.error(f"Simulation failed: {response.text}")
            return None

    def check_progress(self, sim_url):
        """시뮬레이션 진행 상황 모니터링"""
        while True:
            response = self.session.get(sim_url)
            if response.status_code != 200:
                logging.error(f"Failed to check progress: {response.status_code}")
                return None
            
            data = response.json()
            progress = data.get("progress", 0)
            logging.info(f"Simulation progress: {progress*100:.1f}%")
            
            if progress == 1.0:
                alpha_id = data.get("alpha")
                logging.info(f"Simulation complete! Alpha ID: {alpha_id}")
                return alpha_id
            
            if data.get("status") in ["FAILED", "ERROR"]:
                error_msg = data.get("message", "Unknown simulation error")
                logging.error(f"Simulation error: {error_msg}")
                return {"status": "FAILED", "message": error_msg}
                
            time.sleep(10) # 10초마다 확인

    def get_alpha_results(self, alpha_id):
        """최종 시뮬레이션 지표 및 상관계수 정보 가져오기"""
        alpha_url = f"{self.base_url}/alphas/{alpha_id}"
        response = self.session.get(alpha_url)
        if response.status_code == 200:
            return response.json()
        return None

    def get_detailed_stats(self, alpha_id):
        """연도별 성과 등 상세 지표 가져오기"""
        check_url = f"{self.base_url}/alphas/{alpha_id}/check"
        response = self.session.get(check_url)
        if response.status_code == 200:
            return response.json()
        return None

if __name__ == "__main__":
    # 테스트 코드는 실제 이메일/비번이 필요하므로 구조만 확인
    print("WQClient module loaded.")
