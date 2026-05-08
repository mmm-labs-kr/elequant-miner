import requests
import time
import webbrowser
import logging
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
from utils.paths import ENV_FILE

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds between retries for result fetching


class WQClient:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://api.worldquantbrain.com"
        self.email = None
        self.password = None
        self.is_logged_in = False

    def login(self, email, password):
        self.email = email
        self.password = password

        response = self.session.post(f"{self.base_url}/authentication", auth=(email, password))

        if response.status_code == 201:
            logging.info("Logged in to WorldQuant Brain.")
            self.is_logged_in = True
            return True

        if response.status_code == 401:
            resp_json = response.json()
            if 'inquiry' in resp_json:
                persona_url = (
                    f"https://api.worldquantbrain.com/authentication/persona"
                    f"?inquiry={resp_json['inquiry']}"
                )
                logging.warning("Biometric authentication (Persona) required.")
                logging.info(f"Opening browser: {persona_url}")
                webbrowser.open(persona_url)
                input("Complete authentication in your browser and press Enter...")
                return self.login(self.email, self.password)

            logging.error(f"Login failed: {resp_json}")
            return False

        logging.error(f"Unexpected login error: {response.status_code}")
        return False

    def _relogin_if_needed(self, response) -> bool:
        """Re-authenticate on 401 and return True so the caller can retry."""
        if response.status_code == 401:
            logging.info("Session expired. Re-logging in...")
            return self.login(self.email, self.password)
        return False

    def simulate(self, alpha_code, settings=None):
        if not self.is_logged_in:
            logging.error("Not logged in.")
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
            "visualization": False,
        }
        if settings:
            default_settings.update(settings)

        payload = {"type": "REGULAR", "settings": default_settings, "regular": alpha_code}
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(f"{self.base_url}/simulations", json=payload)
            except requests.exceptions.ConnectionError as e:
                logging.warning(f"simulate connection error (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                logging.error("simulate failed after all retries due to connection error.")
                return None

            if response.status_code == 201:
                sim_url = response.headers.get("Location")
                logging.info(f"Simulation started: {sim_url}")
                return sim_url

            if self._relogin_if_needed(response):
                continue

            logging.error(f"Simulation request failed: {response.text}")
            return None
        return None

    def get_alpha_results(self, alpha_id):
        """Fetch final simulation metrics with retry on transient failures."""
        url = f"{self.base_url}/alphas/{alpha_id}"
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url)
            except requests.exceptions.ConnectionError as e:
                logging.warning(f"get_alpha_results connection error (attempt {attempt+1}): {e}")
                time.sleep(RETRY_DELAY)
                continue
            if response.status_code == 200:
                return response.json()
            if self._relogin_if_needed(response):
                continue
            logging.warning(f"get_alpha_results attempt {attempt+1} failed: {response.status_code}")
            time.sleep(RETRY_DELAY)
        logging.error(f"Failed to fetch alpha results for {alpha_id} after {MAX_RETRIES} attempts.")
        return None

    def list_alphas(self, limit: int = 100) -> list[dict]:
        """Fetch user's alpha history from WQ Brain. Returns list of alpha dicts."""
        results = []
        offset = 0
        while True:
            url = f"{self.base_url}/alphas?type=REGULAR&limit={limit}&offset={offset}"
            response = self.session.get(url)
            if self._relogin_if_needed(response):
                continue
            if response.status_code != 200:
                logging.error(f"list_alphas failed: {response.status_code}")
                break
            data = response.json()
            batch = data.get("results", data if isinstance(data, list) else [])
            results.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return results

    def poll_simulation(self, sim_url: str):
        """Poll simulation status URL with retry on connection errors.
        Returns Response object or None if all retries fail."""
        for attempt in range(MAX_RETRIES):
            try:
                return self.session.get(sim_url)
            except requests.exceptions.ConnectionError as e:
                logging.warning(f"poll_simulation connection error (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        logging.error(f"poll_simulation failed after {MAX_RETRIES} attempts: {sim_url}")
        return None

    def get_detailed_stats(self, alpha_id):
        """Fetch per-year breakdown with retry on transient failures."""
        url = f"{self.base_url}/alphas/{alpha_id}/check"
        for attempt in range(MAX_RETRIES):
            response = self.session.get(url)
            if response.status_code == 200:
                return response.json()
            if self._relogin_if_needed(response):
                continue
            logging.warning(f"get_detailed_stats attempt {attempt+1} failed: {response.status_code}")
            time.sleep(RETRY_DELAY)
        logging.error(f"Failed to fetch detailed stats for {alpha_id} after {MAX_RETRIES} attempts.")
        return None


if __name__ == "__main__":
    print("WQClient module loaded.")
