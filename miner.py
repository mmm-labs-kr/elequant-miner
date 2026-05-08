import random
import time
import logging
import sqlite3
import os
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich import box

from core.db_manager import DBManager
from core.ai_engine import GeminiEngine, DailyQuotaExhausted
from core.api_client import WQClient
from utils.dedup_manager import DedupManager
from utils.paths import DB_PATH, ENV_FILE, LOGS_DIR, DATA_DIR

load_dotenv(ENV_FILE)

console = Console()

# 파일은 타임스탬프 포함 전체 포맷, 터미널은 rich 렌더링
file_handler = logging.FileHandler(LOGS_DIR / "miner.log", encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    force=True,
    handlers=[
        file_handler,
        RichHandler(console=console, show_path=False, markup=True, rich_tracebacks=True),
    ]
)


class ElequantMiner:
    def __init__(self, email, password):
        self.db = DBManager(str(DB_PATH))
        self.ai = GeminiEngine()
        self.wq = WQClient()
        self.dedup = DedupManager(DATA_DIR / "shared_tried.json")
        self.email = email
        self.password = password
        self.user_directive = None
        self._gen_round = 0  # 0=PASSED발전, 1=near-miss개선, 2=신규탐색
        self._recent_codes: list[str] = []  # 최근 생성 코드 (다양성 유도용)

        self.criteria = {
            "sharpe": 1.25,
            "fitness": 1.0,
            "turnover_min": 1.0,
            "turnover_max": 70.0,
            "correlation_max": 0.7,
            "yearly_sharpe_min": 0.1,
        }

    def run(self):
        console.print(Panel(
            "[bold cyan]오늘 연구하고 싶은 테마나 지표를 입력하세요.[/]\n"
            "[dim](예: '배당수익률과 부채비율을 엮어줘', 'RSI 리버전 전략')[/]\n"
            "[dim]엔터만 치면 Full Auto 모드로 동작합니다.[/]",
            title="[bold]💡 Elequant-Miner[/]",
            border_style="cyan",
        ))
        self.user_directive = input("입력: ").strip() or None

        if not self.wq.login(self.email, self.password):
            logging.error("Login failed.")
            return

        logging.info(
            f"🚀 Miner started! Slots: 3 | "
            f"Dedup pool: [cyan]{self.dedup.count}[/] known strategies"
        )

        slots = self._resume_pending()
        max_slots = 3
        session_stats = {"tried": 0, "passed": 0, "failed": 0}
        gen_retry_after = 0.0  # rate limit backoff 타이머

        try:
            while True:
                # 빈 슬롯 채우기 — 3-mode 로테이션
                # mode 0: PASSED 발전, mode 1: near-miss 개선, mode 2: 신규 탐색
                while len(slots) < max_slots:
                    if time.time() < gen_retry_after:
                        remaining = gen_retry_after - time.time()
                        logging.info(f"Generation rate-limited — {remaining:.0f}s 후 재시도")
                        break

                    mode = self._gen_round % 3
                    self._gen_round += 1

                    if mode == 0:
                        parent_alpha = self._get_best_parent()
                        is_nearmiss = False
                        mode_label = "PASSED 발전" if parent_alpha else "신규 탐색(fallback)"
                    elif mode == 1:
                        parent_alpha = self._get_nearmiss_parent()
                        is_nearmiss = True
                        mode_label = "near-miss 개선" if parent_alpha else "신규 탐색(fallback)"
                        if not parent_alpha:
                            is_nearmiss = False
                    else:
                        parent_alpha = None
                        is_nearmiss = False
                        mode_label = "신규 탐색"

                    gen_result = self._generate_strategy(parent_alpha, is_nearmiss=is_nearmiss)

                    if not gen_result:
                        gen_retry_after = time.time() + 60
                        logging.warning("Generation rate-limited — 60s 후 재시도 (슬롯 폴링 계속)")
                        break

                    alpha_code, alpha_settings = gen_result

                    if self.dedup.is_duplicate(alpha_code):
                        logging.info("[yellow]⚠ Duplicate strategy detected — skipping[/]")
                        continue

                    self.dedup.add(alpha_code)
                    alpha_id = self._save_alpha(alpha_code, parent_alpha['id'] if parent_alpha else None)
                    sim_url = self.wq.simulate(alpha_code, alpha_settings or None)

                    if sim_url:
                        self._save_sim_url(alpha_id, sim_url)
                        settings_str = ", ".join(f"{k}={v}" for k, v in alpha_settings.items()) if alpha_settings else "default"
                        slots.append({
                            'sim_url': sim_url,
                            'alpha_id': alpha_id,
                            'attempt': 0,
                            'code': alpha_code,
                            'parent_alpha': parent_alpha,
                            'submitted_at': time.time(),
                            'last_heartbeat': 0,
                            'last_progress': -1,
                        })
                        session_stats["tried"] += 1
                        logging.info(
                            f"Slot {len(slots)}/3 | [bold]#{alpha_id}[/] "
                            f"[dim]{mode_label}[/] ({settings_str})"
                        )
                        time.sleep(15)
                    else:
                        logging.error(f"Simulation start failed for Alpha #{alpha_id}")

                # 슬롯 상태 체크
                for slot in slots[:]:
                    try:
                        response = self.wq.session.get(slot['sim_url'])
                        if response.status_code != 200:
                            logging.error(f"Slot HTTP {response.status_code}: {slot['sim_url']}")
                            slots.remove(slot)
                            continue

                        data = response.json()
                        progress = data.get("progress", 0)
                        status = data.get("status")

                        if status in ("COMPLETE", "WARNING") or progress == 1.0:
                            alpha_wq_id = data.get("alpha")
                            results = self.wq.get_alpha_results(alpha_wq_id)
                            detailed = self.wq.get_detailed_stats(alpha_wq_id)

                            if results:
                                results['detailed'] = detailed
                                results['_code'] = slot['code']
                                passed = self._process_results(slot['alpha_id'], results)
                                if passed:
                                    session_stats["passed"] += 1
                                else:
                                    session_stats["failed"] += 1

                            slots.remove(slot)
                            self._print_session_stats(session_stats)

                        elif status in ["FAILED", "ERROR"]:
                            error_msg = data.get("message", "Unknown error")
                            logging.warning(
                                f"[yellow]⚠ Alpha #{slot['alpha_id']} Error:[/] {error_msg}\n"
                                f"  Code: {slot['code'][:120]}"
                            )

                            if slot['attempt'] < 2:
                                logging.info(
                                    f"🔧 Retrying Alpha #{slot['alpha_id']} "
                                    f"(attempt {slot['attempt']+1}/3)"
                                )
                                fix_result = self._generate_strategy(
                                    parent={'code': slot['code']}, error_msg=error_msg
                                )
                                if fix_result:
                                    fixed_code, _ = fix_result
                                    new_sim_url = self.wq.simulate(fixed_code)
                                    if new_sim_url:
                                        self._save_sim_url(slot['alpha_id'], new_sim_url)
                                        slot['sim_url'] = new_sim_url
                                        slot['code'] = fixed_code
                                        slot['attempt'] += 1
                                        continue

                            self._update_alpha_status(slot['alpha_id'], f"FAILED: {error_msg[:50]}")
                            session_stats["failed"] += 1
                            slots.remove(slot)

                        else:
                            elapsed = time.time() - slot.get('submitted_at', time.time())
                            last_progress = slot.get('last_progress', -1)
                            time_since_update = time.time() - slot.get('last_heartbeat', 0)
                            # 진행률 5% 이상 변화 or 2분마다 게이지 갱신
                            if progress - last_progress >= 0.05 or (elapsed > 30 and time_since_update > 120):
                                self._print_progress(slot['alpha_id'], progress, elapsed)
                                slot['last_progress'] = progress
                                slot['last_heartbeat'] = time.time()

                    except Exception as e:
                        logging.error(f"Slot check error: {e}")

                if not slots:
                    time.sleep(10)
                else:
                    time.sleep(15)

        except DailyQuotaExhausted as e:
            console.print(Panel(
                f"[bold red]{e}[/]\n\n"
                f"[bold]이번 세션:[/] "
                f"시도 {session_stats['tried']} | "
                f"[green]합격 {session_stats['passed']}[/] | "
                f"[red]실패 {session_stats['failed']}[/]\n"
                "[dim]내일 자정 이후 재시작하세요.[/]",
                title="[bold red]📵 일일 할당량 소진 — 종료[/]",
                border_style="red",
            ))
        except KeyboardInterrupt:
            console.print(Panel(
                f"[bold]진행 중인 시뮬레이션:[/] {len(slots)}개 — WQ 서버에서 계속 실행 중\n"
                f"[bold]이번 세션:[/] "
                f"시도 {session_stats['tried']} | "
                f"[green]합격 {session_stats['passed']}[/] | "
                f"[red]실패 {session_stats['failed']}[/]\n"
                "[dim]결과는 WQ Brain 웹사이트 또는 대시보드에서 확인하세요.[/]",
                title="[bold yellow]🛑 종료[/]",
                border_style="yellow",
            ))

    def _resume_pending(self) -> list:
        """이전 세션에서 PENDING 상태로 남은 시뮬레이션을 복구."""
        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, code, sim_url FROM alphas "
            "WHERE status = 'PENDING' AND sim_url IS NOT NULL"
        ).fetchall()
        conn.close()
        if not rows:
            return []
        logging.info(f"이전 세션 PENDING [cyan]{len(rows)}[/]개 복구 중...")
        return [
            {
                'sim_url': r['sim_url'],
                'alpha_id': r['id'],
                'attempt': 3,
                'code': r['code'],
                'parent_alpha': None,
                'submitted_at': time.time() - 300,
                'last_heartbeat': 0,
                'last_progress': -1,
            }
            for r in rows
        ]

    @staticmethod
    def _print_progress(alpha_id: int, progress: float, elapsed: float):
        bar_width = 24
        filled = int(progress * bar_width)
        bar = '█' * filled + '░' * (bar_width - filled)
        pct = f"{progress * 100:5.1f}%"
        label = "대기 중" if progress == 0 else pct
        console.print(
            f"  ⏳ Alpha [bold]#{alpha_id}[/]  [{bar}] {label}  "
            f"({elapsed / 60:.1f}분 경과)",
            highlight=False,
        )

    def _print_session_stats(self, stats):
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_row("이번 세션", "")
        t.add_row("  시도", str(stats["tried"]))
        t.add_row("  합격", f"[green]{stats['passed']}[/]")
        t.add_row("  실패", f"[red]{stats['failed']}[/]")
        console.print(t)

    def _generate_strategy(self, parent=None, error_msg=None, is_nearmiss=False):
        directive_keyword = self.user_directive.split()[0] if self.user_directive else None
        selected_fields = self.ai.search_fields(directive_keyword, limit=25)
        fields_context = "\nAvailable Data Fields:\n" + "\n".join(
            [f"- {f['id']}: {f['description']}" for f in selected_fields]
        )

        # A: 최근 생성 패턴 요약 (explore/evolve에서 반복 방지)
        diversity_hint = ""
        if self._recent_codes and not error_msg and not is_nearmiss:
            recent_sample = self._recent_codes[-5:]
            diversity_hint = (
                "\n=== DIVERSITY REQUIREMENT ===\n"
                "These were recently generated — your output must be STRUCTURALLY DIFFERENT "
                "(use different data fields, different transformation logic, different economic rationale):\n"
                + "\n".join(f"- {c}" for c in recent_sample)
            )

        # B: SELF_CORRELATION 탈락 전략 (explore에서 anti-example 제공)
        corr_hint = ""
        if not error_msg and not is_nearmiss and not parent:
            corr_rejected = self._get_corr_rejected(limit=4)
            if corr_rejected:
                corr_hint = (
                    "\n=== AVOID CORRELATION ===\n"
                    "These alphas had good metrics but were rejected because they are too similar "
                    "to already-submitted strategies. Generate something with DIFFERENT structure:\n"
                    + "\n".join(f"- {c}" for c in corr_rejected)
                )

        is_fix = bool(error_msg)

        if error_msg:
            event_hint = (
                "\nHint: 'does not support event inputs' means the field is quarterly/annual event data. "
                "Wrap it with ts_sum(field, 4) or ts_mean(field, 4) before using in arithmetic."
                if "event input" in error_msg else ""
            )
            prompt = f"""\
FASTEXPR code that caused an error in WorldQuant Brain:
Code: {parent['code'] if parent else 'N/A'}
Error: {error_msg}{event_hint}

Fix the error. Return ONLY the corrected raw FASTEXPR expression.
{fields_context}"""

        elif is_nearmiss and parent:
            keys = parent.keys()
            sharpe   = parent['sharpe']   if 'sharpe'   in keys else 0
            fitness  = parent['fitness']  if 'fitness'  in keys else 0
            turnover = parent['turnover'] if 'turnover' in keys else 0
            failed_str = parent['failed_checks'] if 'failed_checks' in keys else ""
            failed_set = set(failed_str.split(',')) if failed_str else set()
            analysis = parent['llm_analysis']  if 'llm_analysis'  in keys and parent['llm_analysis'] else None

            # 실패한 각 체크에 대해 수치 gap + 구체적 개선 안내 생성
            fail_lines = []
            if 'LOW_SHARPE' in failed_set and isinstance(sharpe, (int, float)):
                gap = 1.25 - sharpe
                severity = "far from target — consider a fundamentally different signal" if gap > 0.5 else "close — minor structural fix may suffice"
                fail_lines.append(
                    f"- LOW_SHARPE: {sharpe:.3f} → need ≥1.25 (gap {gap:.3f}, {severity})\n"
                    f"  Fix: \n"
                    f"  1. group_zscore(signal, subindustry) removes sector/size noise that dilutes Sharpe\n"
                    f"  2. Combine with an orthogonal signal: e.g., add ts_zscore(volume_signal, 20) to price signal\n"
                    f"  3. Use ts_zscore(x, 60) on the raw signal before ranking to filter noise\n"
                    f"  4. If sharpe < 0.5, the signal direction may be wrong — try multiply(-1, signal)"
                )
            if 'LOW_FITNESS' in failed_set and isinstance(fitness, (int, float)):
                gap = 1.0 - fitness
                fail_lines.append(
                    f"- LOW_FITNESS: {fitness:.3f} → need ≥1.0 (gap {gap:.3f})\n"
                    f"  Fix (Fitness = consistency across years):\n"
                    f"  1. ts_decay_linear(signal, 5) smooths signal to reduce year-to-year variance\n"
                    f"  2. Use longer lookback (20→60) to capture more stable patterns\n"
                    f"  3. group_zscore neutralization helps fitness by removing macro regime effects\n"
                    f"  4. Avoid signals sensitive to a single year — check if lookback spans multiple regimes"
                )
            if 'LOW_TURNOVER' in failed_set and isinstance(turnover, (int, float)):
                fail_lines.append(
                    f"- LOW_TURNOVER: {turnover:.1f}% → need ≥1%\n"
                    f"  Fix: shorten lookback windows, use faster signals (returns, volume), or remove ts_decay_linear"
                )
            if 'HIGH_TURNOVER' in failed_set and isinstance(turnover, (int, float)):
                fail_lines.append(
                    f"- HIGH_TURNOVER: {turnover:.1f}% → need ≤70%\n"
                    f"  Fix: increase decay parameter (try 15-30), use ts_decay_linear, switch to slower fundamental signals"
                )
            if 'LOW_SUB_UNIVERSE_SHARPE' in failed_set:
                fail_lines.append(
                    f"- LOW_SUB_UNIVERSE_SHARPE: Sharpe={sharpe:.3f} overall but signal collapses on smaller-cap subsets\n"
                    f"  Root cause: signal relies on large-cap price/volume patterns that don't generalize\n"
                    f"  Fix (try in order):\n"
                    f"  1. Wrap the ENTIRE expression with group_zscore(..., subindustry) — forces signal to compete only within same industry group, making it work across all market caps\n"
                    f"  2. Add ts_zscore(signal, d) normalization BEFORE group_zscore — removes absolute-level dependency\n"
                    f"  3. Replace any ts_mean/ts_delta on price/close with rank()-based equivalent (rank is cap-size agnostic)\n"
                    f"  4. Use winsorize(signal, std=3) to suppress small-cap outliers that distort the signal\n"
                    f"  5. If using fundamental data, normalize by total_assets or market_cap to make it size-neutral\n"
                    f"  DO NOT just change universe — fix the signal structure to work across all sizes"
                )
            if 'CONCENTRATED_WEIGHT' in failed_set:
                fail_lines.append(
                    f"- CONCENTRATED_WEIGHT: positions too concentrated in a few stocks\n"
                    f"  Fix: wrap the final expression with rank() or zscore() to spread weights evenly; "
                    f"use winsorize(x, std=3) to cap outliers; avoid log() or power() on raw values without normalization"
                )
            if not fail_lines:
                fail_lines.append(f"- failed_checks: {failed_str} (see above for context)")

            fail_text = "\n".join(fail_lines)

            prompt = f"""\
Near-miss alpha — passed {7 - len(failed_set)}/7 WQ Brain checks, failing only:
Code: {parent['code']}
Metrics: Sharpe={sharpe}, Fitness={fitness}, Turnover={turnover}%
{f'Previous LLM analysis: {analysis}' if analysis else ''}

=== FAILING CHECKS — fix ONLY these, leave passing parts untouched ===
{fail_text}

{"Focus: " + self.user_directive if self.user_directive else ""}
Return ONLY the corrected raw FASTEXPR expression.
{fields_context}"""

        elif self.user_directive and not parent:
            prompt = f"""\
Research Topic: {self.user_directive}

Create a high-quality WorldQuant Alpha factor using FASTEXPR based on this topic.
Combine the most relevant data fields in a non-trivial, statistically motivated way.
Return ONLY the raw FASTEXPR expression.
{fields_context}"""

        elif parent:
            keys = parent.keys()
            sharpe   = parent['sharpe']       if 'sharpe'       in keys else '?'
            fitness  = parent['fitness']      if 'fitness'      in keys else '?'
            turnover = parent['turnover']     if 'turnover'     in keys else '?'
            analysis = parent['llm_analysis'] if 'llm_analysis' in keys and parent['llm_analysis'] else None
            analysis_section = f"\nPrevious analysis:\n{analysis}\n" if analysis else ""

            prompt = f"""\
Successful alpha to evolve:
Code: {parent['code']}
Sharpe: {sharpe}, Fitness: {fitness}, Turnover: {turnover}
{analysis_section}
Evolve this alpha: change lookback windows, add sector neutralization,
combine with a complementary signal, or substitute higher-quality data fields.
{"Focus area: " + self.user_directive if self.user_directive else ""}
Return ONLY the evolved raw FASTEXPR expression.
{fields_context}{diversity_hint}"""

        else:
            themes = [
                # Price/Volume — momentum & reversion
                "Short-term price reversal: stocks that dropped hardest last week tend to bounce",
                "Volume-price divergence: price up but volume shrinking signals weakness",
                "Bollinger Band mean reversion: buy oversold, sell overbought relative to 20d std dev",
                "MACD-style momentum: fast EMA minus slow EMA as directional signal",
                "High-low range contraction as volatility breakout precursor",
                # Fundamental — quality & value
                "Accruals anomaly (Sloan 1996): firms with low accruals (cash earnings > book earnings) outperform",
                "Earnings quality: operating cash flow minus net income normalized by assets",
                "ROA momentum: change in return-on-assets as profitability improvement signal",
                "Gross margin stability: consistent gross margin over time signals pricing power",
                "Debt reduction signal: firms paying down debt outperform levered peers",
                # Options-implied
                "Implied volatility skew: high put/call IV ratio signals informed bearish positioning",
                "IV minus realized volatility spread: overpriced options predict reversal",
                "Option volume surge: unusual call volume relative to open interest as bullish signal",
                # News sentiment
                "News sentiment momentum: stocks with improving news sentiment outperform",
                "Sentiment novelty: high-novelty positive news has stronger price impact than repeated news",
                "No-news premium: stocks with low news coverage have lower uncertainty, stable returns",
                # Risk factor orthogonalization
                "Sector-neutral momentum: rank returns within subindustry to remove sector beta",
                "Size-adjusted value: P/B ratio neutralized by market cap to isolate pure value signal",
                "Liquidity-adjusted growth: revenue growth weighted by trading volume stability",
            ]
            theme = random.choice(themes)
            prompt = f"""\
Create a WorldQuant Alpha factor based on this research idea:
{theme}

Guidelines:
- Base the signal on a clear economic rationale, not data mining
- Keep the expression simple and elegant (avoid over-engineering)
- Use rank() or zscore() to handle outliers and ensure robustness
- Neutralize sector/industry bias with group_zscore or group_neutralize when relevant
- Use 2-3 data fields maximum
Return ONLY the raw FASTEXPR expression.
{fields_context}{diversity_hint}{corr_hint}"""

        if is_fix:
            mode = 'fix'
        elif is_nearmiss:
            mode = 'near-miss'
        elif parent:
            mode = 'evolve'
        elif self.user_directive:
            mode = 'directed'
        else:
            mode = 'explore'

        remaining = self.ai.daily_remaining
        logging.info(
            f"Generating [bold]{mode}[/] — "
            f"quota gen={remaining['gemini-2.5-flash']} "
            f"fix={remaining['gemini-2.5-flash-lite']}"
        )
        result = self.ai.generate_alpha(prompt, is_fix=is_fix)  # (code, settings) | None
        if result:
            code, settings = result
            unknown = self.ai.unknown_fields(code)
            if unknown:
                logging.warning(f"Unknown fields detected {unknown} — auto-fixing...")
                fix_prompt = (
                    f"FASTEXPR code uses unknown field names that do not exist in WQ Brain: {', '.join(unknown)}\n"
                    f"Code: {code}\n"
                    f"Replace each unknown field with the most semantically similar valid field "
                    f"from the list below. Return ONLY the corrected raw FASTEXPR expression.\n"
                    f"{fields_context}"
                )
                fixed = self.ai.generate_alpha(fix_prompt, is_fix=True)
                if fixed:
                    result = fixed
        if result:
            self._recent_codes.append(result[0])
            self._recent_codes = self._recent_codes[-15:]  # 최근 15개만 유지
        return result

    def _save_alpha(self, code, parent_id):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        user_hypo = 1 if self.user_directive else 0
        cursor.execute(
            "INSERT INTO alphas (code, parent_id, user_hypothesis, hypothesis_text) "
            "VALUES (?, ?, ?, ?)",
            (code, parent_id, user_hypo, self.user_directive)
        )
        alpha_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return alpha_id

    def _save_sim_url(self, alpha_id, sim_url):
        """Store simulation URL so results can be recovered after restart."""
        conn = sqlite3.connect(self.db.db_path)
        conn.execute(
            "UPDATE alphas SET sim_url = ? WHERE id = ?", (sim_url, alpha_id)
        )
        conn.commit()
        conn.close()

    def _update_alpha_status(self, alpha_id, status):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE alphas SET status = ? WHERE id = ?", (status, alpha_id))
        conn.commit()
        conn.close()

    CRITICAL_CHECKS = {
        'LOW_SHARPE', 'LOW_FITNESS', 'LOW_TURNOVER', 'HIGH_TURNOVER',
        'LOW_SUB_UNIVERSE_SHARPE', 'SELF_CORRELATION', 'CONCENTRATED_WEIGHT',
    }

    def _process_results(self, alpha_id, results):
        if results.get('status') in ['FAILED', 'ERROR']:
            error_msg = results.get('message', 'Unknown error')
            logging.error(f"Alpha #{alpha_id} simulation error: {error_msg}")
            self._update_alpha_status(alpha_id, f"FAILED: {error_msg[:50]}")
            return False

        is_stats = results.get('is', {})
        sharpe   = is_stats.get('sharpe', 0)
        fitness  = is_stats.get('fitness', 0)
        turnover = (is_stats.get('turnover') or 0) * 100  # API: 소수 → %
        margin   = is_stats.get('margin', 0) or 0
        returns  = is_stats.get('returns') or is_stats.get('annualizedReturns')

        # WQ Brain /check 결과를 직접 사용 (7개 기준 전부 반영)
        detailed   = results.get('detailed') or {}
        check_list = (detailed.get('is') or {}).get('checks', [])
        check_map  = {c['name']: c for c in check_list}

        if check_map:
            success = all(
                check_map.get(name, {}).get('result', 'PASS') == 'PASS'
                for name in self.CRITICAL_CHECKS
            )
            failed_keys = [
                c['name'] for c in check_list
                if c.get('result') == 'FAIL' and c['name'] in self.CRITICAL_CHECKS
            ]
            max_corr  = check_map.get('SELF_CORRELATION', {}).get('value') or 0
            sub_sharpe = check_map.get('LOW_SUB_UNIVERSE_SHARPE', {}).get('value')
        else:
            # 폴백: 수동 기준 (Sharpe/Fitness/Turnover/MaxCorr)
            correlations = is_stats.get('correlations', [])
            max_corr = max((c.get('value', 0) for c in correlations), default=0)
            sub_sharpe = None
            success = (
                sharpe  >= self.criteria['sharpe']
                and fitness  >= self.criteria['fitness']
                and self.criteria['turnover_min'] <= turnover <= self.criteria['turnover_max']
                and max_corr < self.criteria['correlation_max']
            )
            failed_keys = [k for k, v in {
                'LOW_SHARPE':    sharpe  >= self.criteria['sharpe'],
                'LOW_FITNESS':   fitness >= self.criteria['fitness'],
                'LOW_TURNOVER':  turnover >= self.criteria['turnover_min'],
                'HIGH_TURNOVER': turnover <= self.criteria['turnover_max'],
                'SELF_CORRELATION': max_corr < self.criteria['correlation_max'],
            }.items() if not v]

        quality_score = round(
            (sharpe * fitness) / (1.0 + abs(turnover - 25) / 25.0), 4
        ) if (sharpe > 0 and fitness > 0) else 0.0

        if success:
            status = "PASSED"
        elif 'SELF_CORRELATION' in failed_keys:
            status = "REJECTED_BY_CORR"
        else:
            status = "REJECTED"

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO metrics "
            "(alpha_id, sharpe, turnover, fitness, margin, returns, sub_sharpe, max_corr, "
            " quality_score, failed_checks, success_flag) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alpha_id, sharpe, turnover, fitness, margin,
             returns, sub_sharpe, max_corr,
             quality_score,
             ",".join(failed_keys) if failed_keys else None,
             1 if success else 0)
        )
        cursor.execute("UPDATE alphas SET status = ? WHERE id = ?", (status, alpha_id))
        cursor.execute("INSERT OR IGNORE INTO feedback (alpha_id) VALUES (?)", (alpha_id,))
        self._store_yearly_metrics(alpha_id, detailed, cursor)
        conn.commit()
        conn.close()

        metrics_line = (
            f"Sharpe=[bold]{sharpe:.3f}[/]  "
            f"Fitness={fitness:.3f}  "
            f"Turnover={turnover:.1f}%  "
            f"MaxCorr={max_corr:.3f}"
        )

        if success:
            console.print(Panel(
                metrics_line,
                title=f"[bold green]✅ Alpha #{alpha_id} → {status}[/]",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"{metrics_line}\n[red]실패 기준:[/] {', '.join(failed_keys)}",
                title=f"[bold red]❌ Alpha #{alpha_id} → {status}[/]",
                border_style="red",
            ))

        self._store_llm_analysis(
            alpha_id, results.get('_code', ''),
            sharpe, fitness, turnover, margin, max_corr, failed_keys, status
        )
        return success

    @staticmethod
    def _store_yearly_metrics(alpha_id: int, detailed: dict, cursor):
        """WQ Brain /check 응답의 연도별 데이터를 yearly_metrics 테이블에 저장."""
        is_data = (detailed or {}).get('is') or {}
        yearly = []
        for key in ('stats', 'yearlyStats', 'annualStats', 'yearly', 'performance'):
            candidate = is_data.get(key)
            if isinstance(candidate, list) and candidate:
                yearly = candidate
                break
        for y in yearly:
            year = y.get('year') or y.get('yr')
            if not year:
                continue
            turnover_raw = y.get('turnover')
            turnover_pct = turnover_raw * 100 if turnover_raw is not None and turnover_raw < 2 else turnover_raw
            cursor.execute(
                """INSERT OR IGNORE INTO yearly_metrics
                   (alpha_id, year, sharpe, turnover, fitness, returns, drawdown, margin, long_count, short_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alpha_id, int(year),
                 y.get('sharpe'), turnover_pct,
                 y.get('fitness'),
                 y.get('returns') or y.get('annualizedReturns'),
                 y.get('drawdown') or y.get('maxDrawdown'),
                 y.get('margin'),
                 y.get('longCount') or y.get('long_count'),
                 y.get('shortCount') or y.get('short_count'))
            )

    def _store_llm_analysis(self, alpha_id, code, sharpe, fitness,
                            turnover, margin, max_corr, failed_keys, status):
        failed_section = (
            "Failed criteria:\n" + "\n".join(f"  - {k}" for k in failed_keys)
        ) if failed_keys else "All criteria passed."

        prompt = f"""\
Alpha FASTEXPR Code:
{code if code else '(not recorded)'}

Result: {status}
Sharpe={sharpe:.3f}, Fitness={fitness:.3f}, Turnover={turnover:.1f}%, Margin={margin:.4f}, MaxCorr={max_corr:.3f}
{failed_section}

Analyze: What structural feature drove this result?
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
            logging.info(f"Alpha #{alpha_id} LLM analysis stored.")

    def _get_best_parent(self):
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

        weights = list(range(len(rows), 0, -1))
        total = sum(weights)
        r = random.random() * total
        cumulative = 0
        for row, w in zip(rows, weights):
            cumulative += w
            if r <= cumulative:
                return row
        return rows[0]

    def _get_nearmiss_parent(self):
        """합격 기준에 가장 근접한 REJECTED 전략을 부모로 반환.

        정렬 우선순위:
          1. 실패 체크 수 오름차순 (1개 탈락 > 2개 탈락 > ...)
          2. 수치 기준 갭 합산 오름차순 (sharpe/fitness/turnover 기준 근접도)
        """
        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.id, a.code, m.sharpe, m.fitness, m.turnover,
                   m.failed_checks, f.llm_analysis,
                   CASE
                     WHEN m.failed_checks IS NULL THEN 0
                     ELSE (LENGTH(m.failed_checks) - LENGTH(REPLACE(m.failed_checks, ',', ''))) + 1
                   END AS failed_count,
                   (
                     MAX(0.0, 1.25 - COALESCE(m.sharpe,   0)) / 1.25 +
                     MAX(0.0, 1.0  - COALESCE(m.fitness,  0)) / 1.0  +
                     CASE
                       WHEN m.turnover BETWEEN 1 AND 70 THEN 0.0
                       WHEN m.turnover < 1  THEN (1  - m.turnover) / 1.0
                       ELSE                      (m.turnover - 70) / 70.0
                     END
                   ) AS numeric_gap
            FROM alphas a
            JOIN metrics m ON a.id = m.alpha_id
            LEFT JOIN feedback f ON a.id = f.alpha_id
            WHERE a.status = 'REJECTED'
              AND m.sharpe   > 0.6
              AND m.fitness  > 0.3
              AND m.turnover BETWEEN 0.1 AND 200
            ORDER BY failed_count ASC, numeric_gap ASC
            LIMIT 10
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None
        # 상위 5개 중 랜덤 선택 (과도한 집중 방지)
        return random.choice(rows[:5])

    def _get_corr_rejected(self, limit: int = 4) -> list[str]:
        """SELF_CORRELATION 탈락 전략 코드 목록 반환 (B: anti-example용)."""
        conn = sqlite3.connect(self.db.db_path)
        rows = conn.execute(
            "SELECT a.code FROM alphas a JOIN metrics m ON a.id = m.alpha_id "
            "WHERE a.status = 'REJECTED_BY_CORR' ORDER BY m.sharpe DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]


if __name__ == "__main__":
    email = os.getenv("WQ_EMAIL")
    password = os.getenv("WQ_PASSWORD")

    if not email or not password:
        console.print("[bold red]Error:[/] WQ_EMAIL or WQ_PASSWORD not found in .env file.")
    else:
        miner = ElequantMiner(email, password)
        miner.run()
