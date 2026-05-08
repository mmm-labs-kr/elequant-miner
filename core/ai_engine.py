import os
import time
import json
import re
import logging
from datetime import datetime, timedelta, date
from google import genai
from utils.paths import ENV_FILE, OPERATORS_JSON, DATAFIELDS_JSON, DATA_DIR


class DailyQuotaExhausted(Exception):
    """Raised when a model's daily request quota is fully consumed."""


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
Correct FASTEXPR examples — study the argument counts carefully:
1. Momentum:         rank(ts_mean(returns, 20))
2. Mean Reversion:   -rank(ts_delta(close, 5))
3. Volume Surge:     rank(volume / ts_mean(volume, 20))
4. Sector Neutral:   group_zscore(ts_mean(returns, 60), sector)
5. Reversal:         -ts_rank(close, 20)
6. Combined:         rank(ts_mean(returns, 20)) - rank(ts_std_dev(returns, 20))
7. Conditional:      if_else(or(greater(returns, 0), less(ts_mean(volume,5), 1000)), rank(close), -rank(close))
8. Safe divide:      divide(close, add(high, add(low, 0.000001)))
9.  Accruals anomaly: rank(subtract(ts_mean(mdf_coa, 4), ts_mean(mdf_roa, 4)))
10. Vol skew signal:  rank(subtract(ts_mean(opt6_pvolu, 5), ts_mean(opt6_cvolu, 5)))
11. Price channel (Williams %R style):
    divide(subtract(subtract(multiply(2, close), high), low), add(subtract(high, low), 0.000001))
    ← denominator is add(expr, epsilon), NOT (expr, epsilon) or divide(a, b, epsilon)
"""

ALPHA_DESIGN_PRINCIPLES = """\
=== ALPHA DESIGN PRINCIPLES (follow these to avoid overfitting and ensure robustness) ===
- Economic rationale first: the signal must have a logical reason to predict future returns
- Simple over complex: a clean 1-line expression beats an over-engineered 5-line formula
- Always use rank() or zscore() on raw data to handle outliers and ensure uniform distribution
- Neutralize sector/market bias: wrap with group_zscore(x, subindustry) or group_neutralize when signal may carry sector beta
- Avoid parameter overloading: minimize use of limit/scale/truncation operators — they hide noise rather than fix it
- Winsorize extreme values instead of clipping: winsorize(x, std=3) is safer than hard if_else cutoffs
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
            raise DailyQuotaExhausted(
                f"Daily quota exhausted ({self._daily_count}/{self.max_rpd} requests used). "
                "Restart tomorrow or increase quota limits."
            )

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
        backend = os.getenv("GEMINI_BACKEND", "aistudio").lower()

        if backend == "vertex":
            project  = os.getenv("GOOGLE_CLOUD_PROJECT")
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            if not project:
                raise ValueError("Vertex AI 사용 시 GOOGLE_CLOUD_PROJECT를 .env에 설정하세요.")
            self.client = genai.Client(vertexai=True, project=project, location=location)
            logging.info(f"Gemini backend: Vertex AI (project={project}, location={location})")
        else:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("AI Studio 사용 시 GEMINI_API_KEY를 .env에 설정하세요.")
            self.client = genai.Client(api_key=api_key)
            logging.info("Gemini backend: AI Studio")

        self._gen_quota = _ModelQuota(GEN_RPM, GEN_RPD, min_interval=15)
        self._fix_quota = _ModelQuota(FIX_RPM, FIX_RPD, min_interval=3)
        self._category_idx = 0

        with open(OPERATORS_JSON, 'r', encoding='utf-8') as f:
            self.operators = json.load(f)
        with open(DATAFIELDS_JSON, 'r', encoding='utf-8') as f:
            self.datafields = json.load(f)

        self._operator_ref = self._build_operator_reference()
        self._field_ref = self._build_field_reference()
        self._field_ids = {f['id'] for f in self.datafields}
        self._operator_names = {op['name'] for op in self.operators}
        self._load_quota_state()

    def _build_operator_reference(self) -> str:
        by_cat: dict[str, list[str]] = {}
        for op in self.operators:
            by_cat.setdefault(op['category'], []).append(op['definition'])
        lines = []
        for cat, defs in by_cat.items():
            lines.append(f"[{cat}] " + "  ".join(defs))
        return "\n".join(lines)

    def _build_field_reference(self) -> str:
        by_cat: dict[str, list[str]] = {}
        for f in self.datafields:
            by_cat.setdefault(f.get('category', 'Other'), []).append(f['id'])
        lines = []
        for cat, ids in sorted(by_cat.items()):
            lines.append(f"[{cat}]\n" + ", ".join(ids))
        return "\n".join(lines)

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

    # FASTEXPR 토큰 중 필드명이 아닌 것 (연산자명은 self._operator_names로 별도 관리)
    _FASTEXPR_NONFIELD = frozenset({'true', 'false'})

    def unknown_fields(self, code: str) -> list[str]:
        """코드에서 datafields.json에 없는 식별자 토큰을 반환."""
        tokens = set(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', code))
        tokens -= self._operator_names
        tokens -= self._FASTEXPR_NONFIELD
        return sorted(t for t in tokens if t not in self._field_ids)

    @staticmethod
    def _validate(code: str) -> bool:
        if not code or len(code) < 5:
            return False
        if code.count('(') != code.count(')'):
            return False
        if re.search(r'\d[eE][+-]?\d', code):
            return False  # scientific notation not supported
        known_fn = ('rank(', 'ts_', 'scale(', 'group_', 'log(',
                    'abs(', 'if_else(', 'zscore(')
        return any(fn in code for fn in known_fn)

    @staticmethod
    def _clean(raw: str) -> str:
        code = re.sub(r'```(?:[a-zA-Z]+)?\n?', '', raw).replace('```', '').strip().strip("'\"")
        code = re.sub(r'\bts_stddev\(', 'ts_std_dev(', code)
        code = re.sub(r'\bstddev\(', 'ts_std_dev(', code)
        code = re.sub(r'\bstdev\(', 'ts_std_dev(', code)
        # Short aliases → canonical FASTEXPR names
        code = re.sub(r'\bmul\(', 'multiply(', code)
        code = re.sub(r'\bdiv\(', 'divide(', code)
        code = re.sub(r'\bsub\(', 'subtract(', code)
        # rank/zscore/scale/normalize accept only 1 mandatory arg;
        # strip trailing float literal (e.g. rank(x, 0.001) → rank(x))
        code = re.sub(r'\b(rank|zscore|scale|normalize)\(([^,)]+),\s*\d*\.\d+\)', r'\1(\2)', code)
        # gt()/lt()/ge()/le() are not FASTEXPR operators — replace with correct names
        code = re.sub(r'\bgt\(', 'greater(', code)
        code = re.sub(r'\blt\(', 'less(', code)
        code = re.sub(r'\bge\(', 'greater_equal(', code)
        code = re.sub(r'\ble\(', 'less_equal(', code)
        # negative(x) does not exist — replace with multiply(-1, x)
        code = re.sub(r'\bnegative\(', 'multiply(-1, ', code)
        # FASTEXPR does not support scientific notation — convert to decimal
        code = re.sub(
            r'\d+(?:\.\d+)?[eE][+-]?\d+',
            lambda m: format(float(m.group(0)), 'f').rstrip('0').rstrip('.') or '0',
            code
        )
        code = GeminiEngine._fix_arithmetic_patterns(code)
        return code

    @staticmethod
    def _split_top_args(s: str) -> list[str]:
        """Split s by top-level commas (not inside parentheses)."""
        args, depth, buf = [], 0, []
        for ch in s:
            if ch == '(':
                depth += 1
                buf.append(ch)
            elif ch == ')':
                depth -= 1
                buf.append(ch)
            elif ch == ',' and depth == 0:
                args.append(''.join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        if buf:
            args.append(''.join(buf).strip())
        return args

    @staticmethod
    def _fix_arithmetic_patterns(code: str) -> str:
        """Fix infix arithmetic and epsilon-tuple patterns the LLM generates."""
        # Simple (word OP word) infix not after a function name → functional form
        code = re.sub(r'(?<![a-zA-Z_\d])\((\w+)\s*\+\s*(\w+)\)', r'add(\1, \2)', code)
        code = re.sub(r'(?<![a-zA-Z_\d])\((\w+)\s*-\s*(\w+)\)', r'subtract(\1, \2)', code)
        code = re.sub(r'(?<![a-zA-Z_\d])\((\w+)\s*\*\s*(\w+)\)', r'multiply(\1, \2)', code)
        code = re.sub(r'(?<![a-zA-Z_\d])\((\w+)\s*/\s*(\w+)\)', r'divide(\1, \2)', code)

        _FLOAT_RE = re.compile(r'^-?[\d.]+$')
        result = []
        i = 0
        n = len(code)
        while i < n:
            matched = False
            # Fix 3-arg divide(A, B, eps)/subtract(A, B, eps) where eps is a float literal
            for fn in ('divide', 'subtract'):
                end = i + len(fn) + 1
                if code[i:end] == fn + '(' and (i == 0 or not (code[i-1].isalnum() or code[i-1] == '_')):
                    j = i + len(fn) + 1
                    depth = 1
                    while j < n and depth > 0:
                        if code[j] == '(':
                            depth += 1
                        elif code[j] == ')':
                            depth -= 1
                        j += 1
                    inner = code[i + len(fn) + 1: j - 1]
                    args = GeminiEngine._split_top_args(inner)
                    if len(args) == 3 and _FLOAT_RE.match(args[2].strip()):
                        eps = args[2].strip()
                        if fn == 'divide':
                            # divide(A, B, eps) → divide(A, add(B, eps))
                            result.append(f"divide({args[0]}, add({args[1]}, {eps}))")
                        else:
                            # subtract(A, B, eps) → subtract(A, B) — drop stray epsilon
                            result.append(f"subtract({args[0]}, {args[1]})")
                        i = j
                        matched = True
                        break
            if matched:
                continue
            # Fix (expr, float_literal) tuple not after a function name → add(expr, float)
            if code[i] == '(' and (i == 0 or not (code[i-1].isalnum() or code[i-1] == '_')):
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if code[j] == '(':
                        depth += 1
                    elif code[j] == ')':
                        depth -= 1
                    j += 1
                inner = code[i + 1: j - 1]
                args = GeminiEngine._split_top_args(inner)
                if len(args) == 2 and _FLOAT_RE.match(args[1].strip()):
                    result.append(f"add({args[0]}, {args[1].strip()})")
                    i = j
                    continue
            result.append(code[i])
            i += 1
        return ''.join(result)

    _VALID_UNIVERSES = {"TOP3000", "TOP2000", "TOP1000", "TOP500", "TOP200", "TOPSP500"}
    _VALID_NEUTRALIZATIONS = {"NONE", "MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY"}

    def _parse_response(self, raw: str) -> tuple[str, dict]:
        """Try to parse JSON response; fall back to treating the whole thing as code."""
        cleaned = re.sub(r'```(?:[a-zA-Z]*)?\n?', '', raw).replace('```', '').strip()
        try:
            data = json.loads(cleaned)
            code = self._clean(str(data.get('code', '')))
            settings = {}
            if 'decay' in data:
                v = int(data['decay'])
                if 1 <= v <= 512:
                    settings['decay'] = v
            if 'truncation' in data:
                v = float(data['truncation'])
                if 0.0 < v <= 1.0:
                    settings['truncation'] = round(v, 4)
            if 'universe' in data:
                v = str(data['universe']).upper()
                if v in self._VALID_UNIVERSES:
                    settings['universe'] = v
            if 'neutralization' in data:
                v = str(data['neutralization']).upper()
                if v in self._VALID_NEUTRALIZATIONS:
                    settings['neutralization'] = v
            if 'pasteurization' in data:
                v = str(data['pasteurization']).upper()
                if v in ('ON', 'OFF'):
                    settings['pasteurization'] = v
            if 'nanHandling' in data:
                v = str(data['nanHandling']).upper()
                if v in ('ON', 'OFF'):
                    settings['nanHandling'] = v
            return code, settings
        except (json.JSONDecodeError, ValueError, TypeError):
            return self._clean(cleaned), {}

    def generate_alpha(self, prompt_context: str, is_fix: bool = False) -> tuple[str, dict] | None:
        """Generate or fix a FASTEXPR alpha. Returns (code, settings_override) or None."""
        model = FIX_MODEL if is_fix else GEN_MODEL
        quota = self._fix_quota if is_fix else self._gen_quota

        if is_fix:
            output_section = (
                "=== OUTPUT ===\n"
                "Return ONLY the raw FASTEXPR expression. No markdown, no explanation, no comments."
            )
        else:
            output_section = """\
=== OUTPUT (JSON only, no markdown) ===
Return a single JSON object with these keys:
- "code": the FASTEXPR expression (required)
- "decay": integer 1-512; short signals 3-10, medium 10-20, slow fundamental 20-40
- "truncation": float 0.02-0.20; concentrated → 0.04-0.06, diffuse → 0.08-0.15
- "universe": TOP3000 (default) | TOP2000 | TOP1000 | TOP500 | TOP200 | TOPSP500
- "neutralization": SUBINDUSTRY (default) | INDUSTRY | SECTOR | MARKET | NONE

Example: {"code": "rank(ts_mean(returns, 20))", "decay": 10, "truncation": 0.08, "universe": "TOP3000", "neutralization": "SUBINDUSTRY"}"""

        system_instruction = f"""\
You are an expert Quantitative Researcher generating WorldQuant Brain FASTEXPR alpha factors.

=== COMPLETE OPERATOR REFERENCE (use ONLY these) ===
{self._operator_ref}

=== STRICT ARGUMENT RULES ===
- d (lookback) must be a POSITIVE INTEGER: ts_mean(x, 20) ✓  ts_mean(x, 0.5) ✗
- rank(x) takes 1 arg; optional rate must be 0 or 2 (integer): rank(x) ✓  rank(x, 0.001) ✗
- zscore(x) takes exactly 1 arg: zscore(x) ✓  zscore(x, 0.001) ✗
- scale(x) takes exactly 1 mandatory arg: scale(x) ✓
- normalize(x) takes exactly 1 mandatory arg
- ts_std_dev(x,d) is the ONLY standard deviation function — stddev() and ts_stddev() do NOT exist
- group_mean(x, weight, group) requires ALL 3 args
- group values: sector, industry, subindustry
- To avoid division by zero use decimal notation ONLY — NEVER use 1e-6 or any scientific notation (FASTEXPR does not support it)

=== FORBIDDEN PATTERNS (these will cause immediate simulation failure) ===
- gt(x,y) and lt(x,y) do NOT exist → use greater(x,y) and less(x,y)
- negative(x) does NOT exist → use multiply(-1, x)
- Python infix `or` / `and` keywords are INVALID → always use function form: or(cond1, cond2)  and(cond1, cond2)
- divide() and subtract() take exactly 2 args — NEVER pass epsilon as a 3rd arg
  Wrong: subtract(multiply(2,close), high, low)    Right: subtract(subtract(multiply(2,close), high), low)
  Wrong: divide(a, b, 0.000001)                    Right: divide(a, add(b, 0.000001))
  Wrong: divide(a, (b, 0.000001))                  Right: divide(a, add(b, 0.000001))
- (high + low) infix inside function args is unreliable → use add(high, low) instead
- group_zscore(x, group): x must be a single expression, group must be sector/industry/subindustry
- Event-type fields (fnd6_*, fn_*, adv*_a quarterly/annual fields) cannot be used directly
  in arithmetic operators — wrap them first: ts_sum(fnd6_sales, 4) or ts_mean(fn_eps_a, 4)
- ONLY use field IDs from the complete list below — any other identifier is invalid

=== COMPLETE VALID DATA FIELDS (use ONLY these IDs — no other field names exist) ===
{self._field_ref}

{FASTEXPR_EXAMPLES}

{ALPHA_DESIGN_PRINCIPLES}

{output_section}"""

        max_retries = 3
        for attempt in range(max_retries):
            quota.wait()
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt_context,
                    config={
                        'system_instruction': system_instruction,
                        'temperature': 0.7 if is_fix else 1.5,
                    }
                )
                raw = response.text.strip()
                self._save_quota_state()

                if is_fix:
                    code = self._clean(raw)
                    settings: dict = {}
                else:
                    code, settings = self._parse_response(raw)

                if self._validate(code):
                    return code, settings
                logging.warning(f"Validation failed (attempt {attempt+1}): {code[:100]}")
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    logging.info("Rate limited (429) — caller will handle backoff.")
                    return None  # 슬립 없이 즉시 반환, 슬롯 폴링 차단 방지
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
