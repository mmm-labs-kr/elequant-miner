import os
from google import genai
from dotenv import load_dotenv
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
from utils.paths import ENV_FILE

def test_connection():
    load_dotenv(ENV_FILE)
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        print("Error: GEMINI_API_KEY not found.")
        return False

    try:
        # gemini-2.5-flash 모델 확정 사용
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents="Hello, verify connection."
        )
        
        if response and response.text:
            print(f"Success! Gemini 2.5 Flash says: {response.text.strip()}")
            return True
        else:
            print("Received empty response.")
            return False
            
    except Exception as e:
        print(f"Connection failed: {str(e)}")
        return False

if __name__ == "__main__":
    test_connection()
