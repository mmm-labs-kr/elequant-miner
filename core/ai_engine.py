import os
import time
import json
import re
import logging
from datetime import datetime, timedelta, date
from google import genai
from utils.paths import ENV_FILE, OPERATORS_JSON, DATAFIELDS_JSON, DATA_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

GEN_MODEL = "gemini-2.5-flash"
FIX_MODEL = "gemini-2.5-flash-lite"

GEN_RPM = 8
GEN_RPD = 480
FIX_RPM = 25
FIX_RPD = 1400

QUOTA_STATE_FILE = DATA_DIR / "quota_state.json"

FIELD_CATEGORIES = [
    "price", "volume", "cashflow", "earnings", "revenue", "debt",
    "dividend", "return", "volatility", "momentum", "sentiment", "growth",
    "equity", "asset", "margin", "ratio", "yield", "shares"
]

FASTEXPR_EXAMPLES = """\
Good FASTEXPR Alpha Examples:
1. Momentum:       rank(ts_mean(close / ts_delay(close, 1) - 1, 20))
2. Mean Reversion: -rank(ts_delta(close, 5)) * (1 / (rank(stddev(close, 20)) + 0.001))
3. Value:          scale(group_zscore(earnings / market_cap, sector))
4. Volume Surge:   rank(volume / ts_mean(volume, 20)) * rank(ts_delta(close, 1))
5. Quality:        -rank(ts_mean(total_debt / equity, 60)) + rank(ts_mean(net_income / equity, 60))
6. Reversal:       -ts_rank(close, 5) + rank(ts_mean(close, 60))
"""


class _ModelQuota:
    """Tracks RPM and RPD for a single model."""

    def __init__(self, max_rpm: int, max_rpd: int, min_interval: float):
        self.max_rpm = max_rpm
        self.max_rpd = max_rpd
        self.min_interval = min_interval
        self._rpm_log: list[datetime] = []
        self._last_call = datetime.now() - timedelta(seconds=min_interval + 1)
        self._day = date.today()
        self._daily_count = 0

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._day:
            self._day = today
            self._daily_count = 0

    @property
    def daily_remaining(self) -> int:
        self._reset_daily_if_needed()
        return self.max_rpd - self._daily_count

    def wait(self):
        self._reset_daily_if_needed()

        if self._daily_count >= self.max_rpd:
            tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
            wait_sec = (tomorrow - datetime.now()).total_seconds() + 60
            logging.warning(f"Daily quota exhausted. Sleeping {wait_sec/3600:.1f}h until tomorrow...")
            time.sleep(wait_sec)
            self._reset_daily_if_needed()

        elapsed = (datetime.now() - self._last_call).total_seconds()
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        now = datetime.now()
        self._rpm_log = [t for t in self._rpm_log if now - t < timedelta(minutes=1)]
        if len(self._rpm_log) >= self.max_rpm:
            wait_sec = 60 - (now - self._rpm_log[0]).total_seconds() + 1
            logging.info(f"Near RPM limit — waiting {wait_sec:.1f}s...")
            time.sleep(wait_sec)

        self._last_call = datetime.now()
        self._rpm_log.append(self._last_call)
        self._daily_count += 1


class GeminiEngine:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=self.api_key)

        self._gen_quota = _ModelQuota(GEN_RPM, GEN_RPD, min_interval=8)
        self._fix_quota = _ModelQuota(FIX_RPM, FIX_RPD, min_interval=3)
        self._category_idx = 0

        with open(OPERATORS_JSON, 'r', encoding='utf-8') as f:
            self.operators = json.load(f)
        with open(DATAFIELDS_JSON, 'r', encoding='utf-8') as f:
            self.datafields = json.load(f)

        self._load_quota_state()

    # ------------------------------------------------------------------ quota persistence

    def _load_quota_state(self):
        """Restore today's call counts from disk so restarts don't reset the daily quota."""
        if not QUOTA_STATE_FILE.exists():
            return
        try:
            data = json.loads(QUOTA_STATE_FILE.read_text(encoding='utf-8'))
            if data.get("date") == str(date.today()):
                self._gen_quota._daily_count = data.get(GEN_MODEL, 0)
                self._fix_quota._daily_count = data.get(FIX_MODEL, 0)
                logging.info(
                    f"Quota state restored: gen={self._gen_quota._daily_count}, "
                    f"fix={self._fix_quota._daily_count}"
                )
        except Exception as e:
            logging.warning(f"Could not load quota state: {e}")

    def _save_quota_state(self):
        """Persist today's call counts to disk after each API call."""
        try:
            data = {
                "date": str(date.today()),
                GEN_MODEL: self._gen_quota._daily_count,
                FIX_MODEL: self._fix_quota._daily_count,
            }
            QUOTA_STATE_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception as e:
            logging.warning(f"Could not save quota state: {e}")

    # ------------------------------------------------------------------ public interface

    @property
    def daily_remaining(self) -> dict:
        return {
            GEN_MODEL: self._gen_quota.daily_remaining,
            FIX_MODEL: self._fix_quota.daily_remaining,
        }

    def search_fields(self, keyword: str | None = None, limit: int = 25) -> list:
        """Return relevant data fields; rotates categories when no keyword is given."""
        if keyword:
            results = [
                f for f in self.datafields
                if keyword.lower() in f['description'].lower()
                or keyword.lower() in f['id'].lower()
            ]
            if results:
                return results[:limit]

        cat = FIELD_CATEGORIES[self._category_idx % len(FIELD_CATEGORIES)]
        self._category_idx += 1
        results = [
            f for f in self.datafields
            if cat.lower() in f['description'].lower()
            or cat.lower() in f['id'].lower()
        ]
        return results[:limit]

    @staticmethod
    def _validate(code: str) -> bool:
        if not code or len(code) < 5:
            return False
        if code.count('(') != code.count(')'):
            return False
        known_fn = ('rank(', 'ts_', 'stddev(', 'scale(', 'group_', 'log(',
                    'abs(', 'if_else(', 'exp(')
        return any(fn in code for fn in known_fn)

    @staticmethod
    def _clean(raw: str) -> str:
        code = re.sub(r'```(?:[a-zA-Z]+)?\n?', '', raw).replace('```', '').strip().strip("'\"")
        code = re.sub(r'ts_stddev\(', 'stddev(', code)
        code = re.sub(r'\bstdev\(', 'stddev(', code)
        code = re.sub(r'mul\(([^,]+),\s*([^)]+)\)', r'(\1 * \2)', code)
        code = re.sub(r'div\(([^,]+),\s*([^)]+)\)', r'(\1 / \2)', code)
        code = re.sub(r'add\(([^,]+),\s*([^)]+)\)', r'(\1 + \2)', code)
        code = re.sub(r'sub\(([^,]+),\s*([^)]+)\)', r'(\1 - \2)', code)
        return code

    def generate_alpha(self, prompt_context: str, is_fix: bool = False) -> str | None:
        """Generate or fix a FASTEXPR alpha. Uses FIX_MODEL when is_fix=True to conserve quota."""
        model = FIX_MODEL if is_fix else GEN_MODEL
        quota = self._fix_quota if is_fix else self._gen_quota

        system_instruction = f"""\
You are an expert Quantitative Researcher at WorldQuant.
Generate high-quality Alpha factors using WorldQuant's FASTEXPR language.

FASTEXPR Rules:
- Arithmetic: +  -  *  /  ^ (power)
- Functions: log(x), exp(x), abs(x)
- Time-series (integer lookback t): ts_sum(x,t) ts_mean(x,t) stddev(x,t) ts_rank(x,t) ts_delta(x,t) ts_delay(x,t)
- Cross-sectional: rank(x) scale(x) group_mean(x,g) group_zscore(x,g)
- Conditional: if_else(condition, true_val, false_val)

Critical rules:
- Use stddev(x,t) NOT ts_stddev — this is the most common error
- Every open parenthesis must have a matching close parenthesis
- Use exact field IDs from the provided list (e.g. close, volume, earnings)
- Output ONLY the raw FASTEXPR expression — no markdown, no explanation

{FASTEXPR_EXAMPLES}"""

        max_retries = 3
        for attempt in range(max_retries):
            quota.wait()
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt_context,
                    config={'system_instruction': system_instruction}
                )
                code = self._clean(response.text.strip())
                self._save_quota_state()
                if self._validate(code):
                    return code
                logging.warning(f"Validation failed (attempt {attempt+1}): {code[:100]}")
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    wait_sec = min(60 * (2 ** attempt), 300)
                    logging.info(f"429 received. Backoff: {wait_sec}s (attempt {attempt+1})...")
                    time.sleep(wait_sec)
                else:
                    logging.error(f"Gemini API error: {e}")
                    return None
        logging.error("Max retries exceeded for alpha generation.")
        return None


    def analyze_result(self, prompt: str) -> str | None:
        """Generate free-form analytical text about a simulation result. Uses flash-lite."""
        system_instruction = """\
You are an expert Quantitative Researcher at WorldQuant analyzing alpha simulation results.
Be concise, technical, and actionable. Maximum 120 words.
Focus on: what drove the result, which sub-expressions are problematic, and specific FASTEXPR edits that would fix the weakest metric."""

        max_retries = 2
        for attempt in range(max_retries):
            self._fix_quota.wait()
            try:
                response = self.client.models.generate_content(
                    model=FIX_MODEL,
                    contents=prompt,
                    config={'system_instruction': system_instruction}
                )
                self._save_quota_state()
                return response.text.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    wait_sec = min(60 * (2 ** attempt), 300)
                    logging.info(f"429 on analysis. Backoff: {wait_sec}s...")
                    time.sleep(wait_sec)
                else:
                    logging.error(f"Analysis error: {e}")
                    return None
        return None


if __name__ == "__main__":
    engine = GeminiEngine()
    fields = engine.search_fields("cashflow")
    print(f"Found {len(fields)} cashflow fields.")
    print(f"Daily remaining — gen: {engine.daily_remaining[GEN_MODEL]}, fix: {engine.daily_remaining[FIX_MODEL]}")
