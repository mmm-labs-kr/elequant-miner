import psutil
import logging
import os

def check_resources():
    """노트북 리소스 상태 확인 (백그라운드 실행용)"""
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    
    logging.info(f"System Check - CPU: {cpu_usage}%, Memory: {memory_info.percent}%")
    
    # 리소스가 너무 높으면 경고 (필요 시 miner.py에서 sleep 추가 가능)
    if cpu_usage > 90 or memory_info.percent > 90:
        logging.warning("⚠️ High system resource usage detected!")
        return False
    return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check_resources()
