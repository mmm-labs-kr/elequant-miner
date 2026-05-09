import sqlite3


def build_yearly_context(db_path: str) -> str:
    """연도별 성과 패턴과 전체 통계를 요약해 LLM 프롬프트용 문자열로 반환."""
    conn = sqlite3.connect(db_path)
    lines = []

    # 1. yearly_metrics 데이터 있으면 연도별 집계
    yearly_count = conn.execute("SELECT COUNT(*) FROM yearly_metrics").fetchone()[0]
    if yearly_count > 0:
        rows = conn.execute("""
            SELECT y.year,
                   COUNT(DISTINCT y.alpha_id) AS n,
                   AVG(y.sharpe)  AS avg_sharpe,
                   AVG(y.returns) AS avg_returns
            FROM yearly_metrics y
            JOIN metrics m ON y.alpha_id = m.alpha_id
            GROUP BY y.year
            ORDER BY y.year
        """).fetchall()
        if rows:
            valid = [(yr, n, s, r) for yr, n, s, r in rows if s is not None]
            if valid:
                overall_avg = sum(s for _, _, s, _ in valid) / len(valid)
                lines.append("=== Yearly Sharpe Pattern ===")
                for yr, n, avg_s, avg_r in valid:
                    tag = "↓ weak" if avg_s < overall_avg * 0.8 else (
                          "↑ strong" if avg_s > overall_avg * 1.2 else "→ avg")
                    ret_str = f"  returns={avg_r:.1%}" if avg_r is not None else ""
                    lines.append(f"  {yr}: Sharpe={avg_s:.2f}{ret_str} ({n} alphas) {tag}")

    # 2. 전체 합격/탈락 통계
    stat = conn.execute("""
        SELECT
            COUNT(CASE WHEN success_flag=1 THEN 1 END) AS passed,
            COUNT(CASE WHEN success_flag=0 THEN 1 END) AS failed,
            AVG(CASE WHEN success_flag=1 THEN sharpe   END) AS pass_sharpe,
            AVG(CASE WHEN success_flag=1 THEN turnover END) AS pass_turn,
            AVG(CASE WHEN success_flag=0 THEN sharpe   END) AS fail_sharpe,
            AVG(CASE WHEN success_flag=0 THEN turnover END) AS fail_turn
        FROM metrics WHERE sharpe IS NOT NULL
    """).fetchone()

    if stat and stat[0]:
        lines.append("=== Strategy Stats ===")
        lines.append(f"  Passed {stat[0]} / Failed {stat[1]}")
        if stat[2]:
            lines.append(f"  Passing avg: Sharpe={stat[2]:.2f}, Turnover={stat[3]:.1f}%")
        if stat[4]:
            lines.append(f"  Failing avg: Sharpe={stat[4]:.2f}, Turnover={stat[5]:.1f}%")

    # 3. 가장 흔한 탈락 원인 Top 5
    reasons = conn.execute("""
        SELECT failed_checks, COUNT(*) AS cnt
        FROM metrics
        WHERE success_flag=0
          AND failed_checks IS NOT NULL AND failed_checks != ''
        GROUP BY failed_checks
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()
    if reasons:
        lines.append("=== Top Failure Reasons ===")
        for reason, cnt in reasons:
            lines.append(f"  [{cnt}x] {reason}")

    conn.close()
    return "\n".join(lines)


def get_alpha_yearly_summary(db_path: str, alpha_id: int) -> str:
    """특정 alpha의 연도별 성과를 요약한 문자열 반환."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT year, sharpe, fitness, returns, turnover
        FROM yearly_metrics
        WHERE alpha_id = ?
        ORDER BY year
    """, (alpha_id,)).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = ["Yearly breakdown:"]
    for yr, s, f, r, t in rows:
        parts = [f"{yr}:"]
        if s is not None: parts.append(f"Sharpe={s:.2f}")
        if f is not None: parts.append(f"Fit={f:.2f}")
        if t is not None: parts.append(f"Turn={t:.1f}%")
        if r is not None: parts.append(f"Ret={r:.1%}")
        lines.append("  " + " ".join(parts))
    return "\n".join(lines)
