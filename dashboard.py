import sqlite3
import pandas as pd
import plotly.graph_objects as go
import networkx as nx
import streamlit as st
from pathlib import Path

DB_PATH = Path(__file__).parent / "research" / "elequant.db"

st.set_page_config(page_title="Elequant Miner", layout="wide")

st.markdown("## Elequant Miner")
st.caption("30초마다 자동 새로고침 | `streamlit run dashboard.py`")

if not DB_PATH.exists():
    st.warning("DB 파일이 없습니다. miner.py를 먼저 실행하세요.")
    st.stop()


@st.cache_data(ttl=30)
def load_data():
    conn = sqlite3.connect(DB_PATH)
    alphas  = pd.read_sql("SELECT * FROM alphas",  conn)
    metrics = pd.read_sql("SELECT * FROM metrics", conn)
    feedback = pd.read_sql("SELECT * FROM feedback", conn)
    try:
        yearly = pd.read_sql(
            "SELECT * FROM yearly_metrics ORDER BY alpha_id, year", conn
        )
    except Exception:
        yearly = pd.DataFrame()
    conn.close()
    return alphas, metrics, feedback, yearly


alphas, metrics, feedback, yearly = load_data()
df = alphas.merge(metrics, left_on="id", right_on="alpha_id", how="left")
df = df.merge(feedback[["alpha_id", "llm_analysis"]], left_on="id", right_on="alpha_id", how="left")

pending_cnt = int((df["status"] == "PENDING").sum())
df = df[df["status"] != "PENDING"].copy()

# ── Overview ──────────────────────────────────────────────────────────
total      = len(df)
passed     = int((df["success_flag"] == 1).sum()) if "success_flag" in df.columns else 0
rejected   = int(df["status"].str.startswith("REJECTED", na=False).sum())
code_fail  = int(df["status"].str.startswith("FAILED",   na=False).sum())
best_sharpe = df["sharpe"].max() if total > 0 and not df["sharpe"].isna().all() else None

if pending_cnt > 0:
    st.info(f"시뮬레이션 진행 중: {pending_cnt}개 — miner 재시작 시 자동 복구")

st.markdown("**Overview**")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("전체",        total)
c2.metric("합격",        passed)
c3.metric("기준 미달",   rejected)
c4.metric("코드 오류",   code_fail)
c5.metric("합격률",      f"{passed/total*100:.1f}%" if total > 0 else "—")
c6.metric("최고 Sharpe", f"{best_sharpe:.3f}" if best_sharpe else "—")

st.divider()

# ── 탭: Performance Map / Lineage Tree ────────────────────────────────
tab_perf, tab_tree = st.tabs(["Performance Map", "Lineage Tree"])

# ── Performance Map (Parallel Coordinates) ────────────────────────────
with tab_perf:
    st.markdown("**Parallel Coordinates** — 모든 완료 전략 · 축을 드래그해 필터링")

    STATUS_NUM = {
        "PASSED":           3,
        "PASSED_A":         3,
        "PASSED_B":         3,
        "PASSED_C":         3,
        "REJECTED_BY_CORR": 2,
        "REJECTED":         1,
        "FAILED":           0,
    }
    PARCOORDS_SCALE = [
        [0.00, "#546e7a"],
        [0.34, "#ff5252"],
        [0.67, "#ff6d00"],
        [1.00, "#00c853"],
    ]

    pc = df.copy()
    pc["_color"] = pc["status"].map(STATUS_NUM).fillna(0)

    axis_defs = [
        ("sharpe",     "Sharpe",       [0, None], True),
        ("fitness",    "Fitness",       [0, None], True),
        ("turnover",   "Turnover (%)",  [0, None], True),
        ("returns",    "Returns",       None,      False),
        ("sub_sharpe", "Sub-Sharpe",    [0, None], False),
        ("max_corr",   "MaxCorr",       [0, 1],    False),
        ("drawdown",   "Drawdown",      None,      False),
    ]

    dims = []
    for col, label, rng, required in axis_defs:
        if col not in pc.columns:
            continue
        if not required and pc[col].notna().sum() < 5:
            continue
        vals = pc[col].fillna(0)
        d = dict(label=label, values=vals)
        lo = rng[0] if rng else float(vals.min())
        hi = rng[1] if (rng and rng[1] is not None) else float(vals.max())
        d["range"] = [lo, hi]

        crange = None
        if col == "sharpe":    crange = [1.25, hi]
        elif col == "fitness": crange = [1.0,  hi]
        elif col == "turnover":crange = [1.0,  70.0]
        elif col == "max_corr":crange = [0.0,  0.7]
        if crange:
            d["constraintrange"] = crange

        dims.append(d)

    if dims:
        fig_pc = go.Figure(go.Parcoords(
            line=dict(
                color=pc["_color"],
                colorscale=PARCOORDS_SCALE,
                showscale=False,
            ),
            dimensions=dims,
            labelside="bottom",
        ))
        fig_pc.update_layout(
            height=420,
            paper_bgcolor="#0e1117",
            plot_bgcolor="#0e1117",
            font=dict(color="#ccc", size=11),
            margin=dict(l=60, r=60, t=40, b=60),
        )
        st.plotly_chart(fig_pc, use_container_width=True)

        leg = st.columns(4)
        for i, (label, color) in enumerate([
            ("PASSED", "#00c853"), ("REJECTED_BY_CORR", "#ff6d00"),
            ("REJECTED", "#ff5252"), ("FAILED", "#546e7a"),
        ]):
            leg[i].markdown(
                f'<span style="background:{color};padding:2px 10px;border-radius:3px;'
                f'font-size:11px;color:#000">{label}</span>',
                unsafe_allow_html=True,
            )
    else:
        st.info("시뮬레이션 결과가 없습니다.")


# ── Lineage Tree ──────────────────────────────────────────────────────
with tab_tree:
    st.markdown("**Strategy Lineage Tree** — miner 생성 전략 및 직계 부모")

    STATUS_COLOR = {
        "PASSED":           "#00c853",
        "PASSED_A":         "#00c853",
        "PASSED_B":         "#69f0ae",
        "PASSED_C":         "#b9f6ca",
        "REJECTED":         "#ff5252",
        "REJECTED_BY_CORR": "#ff6d00",
        "PENDING":          "#90a4ae",
        "FAILED":           "#b0bec5",
    }

    def _tree_layout(G):
        if not G.nodes:
            return {}

        roots = [n for n in G.nodes if G.in_degree(n) == 0]
        connected_roots = sorted([r for r in roots if G.out_degree(r) > 0])
        isolated_roots  = sorted([r for r in roots if G.out_degree(r) == 0])

        _w: dict = {}
        def subtree_width(n):
            if n in _w:
                return _w[n]
            ch = list(G.successors(n))
            _w[n] = max(1, sum(subtree_width(c) for c in ch))
            return _w[n]

        pos = {}
        Y_STEP = 1.5

        def place(node, cx, depth):
            pos[node] = (cx, -depth * Y_STEP)
            children = sorted(G.successors(node))
            if not children:
                return
            total_w = sum(subtree_width(c) for c in children)
            x = cx - total_w / 2.0
            for child in children:
                cw = subtree_width(child)
                place(child, x + cw / 2.0, depth + 1)
                x += cw

        x_offset = 0.0
        for root in connected_roots:
            rw = subtree_width(root)
            place(root, x_offset + rw / 2.0, 0)
            x_offset += rw + 2.5

        COLS = min(8, max(1, len(isolated_roots)))
        iso_x0 = x_offset + (1.5 if connected_roots else 0)
        for i, node in enumerate(isolated_roots):
            col = i % COLS
            row = i // COLS
            pos[node] = (iso_x0 + col * 1.8, -row * Y_STEP)

        return pos

    if "source" in df.columns:
        miner_parent_ids = set(
            df[df["source"] == "miner"]["parent_id"].dropna().astype(int)
        )
        tree_df = df[
            (df["source"] == "miner") | (df["id"].isin(miner_parent_ids))
        ].copy()
    else:
        tree_df = df.copy()

    G = nx.DiGraph()
    for _, row in tree_df.iterrows():
        sharpe_val = row.get("sharpe")
        G.add_node(
            int(row["id"]),
            status=row.get("status", "PENDING"),
            sharpe=sharpe_val,
            quality_score=row.get("quality_score"),
            passed=row.get("success_flag", 0) == 1,
            code=str(row.get("code", ""))[:80],
        )
    for _, row in tree_df.iterrows():
        pid = row.get("parent_id")
        if pd.notna(pid) and int(pid) in G and int(row["id"]) in G:
            G.add_edge(int(pid), int(row["id"]))

    if len(G.nodes) == 0:
        st.info("아직 전략이 없습니다.")
    else:
        try:
            pos = nx.nx_agraph.graphviz_layout(G, prog="dot")
        except Exception:
            pos = _tree_layout(G)

        edge_x, edge_y = [], []
        for u, v in G.edges():
            x0, y0 = pos[u]; x1, y1 = pos[v]
            mid_y = (y0 + y1) / 2
            edge_x += [x0, x0, x1, x1, None]
            edge_y += [y0, mid_y, mid_y, y1, None]

        node_x, node_y, node_color, node_size, node_text, node_hover = [], [], [], [], [], []
        for node in G.nodes():
            attr  = G.nodes[node]
            x, y  = pos[node]
            node_x.append(x); node_y.append(y)
            status = attr.get("status", "PENDING")
            node_color.append(STATUS_COLOR.get(status, "#90a4ae"))
            passed = attr.get("passed", False)
            node_size.append(32 if passed else 18)
            sharpe = attr.get("sharpe")
            sharpe_ok = sharpe is not None and not (isinstance(sharpe, float) and pd.isna(sharpe))
            node_text.append(f"{sharpe:.2f}" if (passed and sharpe_ok) else str(node))
            qs = attr.get("quality_score")
            qs_str = f"{qs:.3f}" if qs is not None and not pd.isna(qs) else "—"
            sharpe_disp = f"{sharpe:.3f}" if sharpe_ok else "—"
            node_hover.append(
                f"<b>Alpha #{node}</b>  {status}<br>"
                f"Sharpe: {sharpe_disp} | QScore: {qs_str}<br>"
                f"{attr.get('code','')}"
            )

        fig_tree = go.Figure()
        fig_tree.add_trace(go.Scatter(
            x=edge_x, y=edge_y, mode="lines",
            line=dict(width=1, color="#546e7a"), hoverinfo="none",
        ))
        fig_tree.add_trace(go.Scatter(
            x=node_x, y=node_y, mode="markers+text",
            marker=dict(size=node_size, color=node_color, line=dict(width=1.5, color="#fff")),
            text=node_text, textposition="middle center",
            textfont=dict(size=9, color="#fff"),
            hovertext=node_hover, hoverinfo="text",
        ))
        fig_tree.update_layout(
            showlegend=False, height=460,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        )
        st.plotly_chart(fig_tree, use_container_width=True)

st.divider()

# ── Criteria Reference ────────────────────────────────────────────────
with st.expander("Criteria Reference — WQ Brain 합격 기준 7개"):
    criteria_rows = [
        ("LOW_SHARPE",              "≥ 1.25",          "리스크 대비 수익 안정성 (전체 기간 평균)",     "수치 기준, 명확"),
        ("LOW_FITNESS",             "≥ 1.0",            "연도별 Sharpe 일관성 (연간 성과의 안정성)",    "수치 기준, 명확"),
        ("LOW_TURNOVER",            "≥ 1%",             "전략이 실제 거래를 일으키는지 확인",           "수치 기준, 명확"),
        ("HIGH_TURNOVER",           "≤ 70%",            "과도한 거래비용·슬리피지 방지",                "수치 기준, 명확"),
        ("LOW_SUB_UNIVERSE_SHARPE", "WQ 동적 결정 (~0.7)", "샘플 외 소규모 종목군에서도 성과 유지",   "WQ 내부 로직, 값은 저장됨"),
        ("SELF_CORRELATION",        "< 0.7 (기존 제출 대비)", "기존 제출 알파와 중복 방지",           "MaxCorr 컬럼에 저장"),
        ("CONCENTRATED_WEIGHT",     "WQ 자동 판단",     "특정 종목에 포지션 집중 여부",                 "수치 없음, PASS/FAIL만"),
    ]
    crit_df = pd.DataFrame(criteria_rows,
                           columns=["Check", "기준값", "설명", "비고"])
    st.dataframe(crit_df, hide_index=True, use_container_width=True)
    st.caption(
        "합격 판정은 WQ Brain `/alphas/{id}/check` 응답을 직접 읽어 판단합니다. "
        "수동 계산 없이 WQ Brain의 7개 체크 결과를 그대로 사용합니다."
    )
    st.markdown(
        "**보조 지표** (합격 기준 아님, 성능 평가용): "
        "Returns — 연간 수익률 | Drawdown — 최대 낙폭 | Margin — 단위 수익 | "
        "Quality Score = (Sharpe × Fitness) / (1 + |Turnover−25| / 25)"
    )

st.divider()

# ── Passed Strategies ────────────────────────────────────────────────
st.markdown("### Passed Strategies")
passed_df = df[df["success_flag"] == 1].copy()

if "quality_score" in passed_df.columns:
    passed_df = passed_df.sort_values("quality_score", ascending=False)
else:
    passed_df = passed_df.sort_values("sharpe", ascending=False)

if passed_df.empty:
    st.info("아직 합격한 전략이 없습니다.")
else:
    st.caption("WQ Brain ID로 웹사이트에서 검색하여 제출하세요")
    for _, row in passed_df.iterrows():
        alpha_id  = int(row["id"])
        sharpe    = row.get("sharpe")
        qs        = row.get("quality_score")
        wq_id     = row.get("wq_alpha_id") or "—"
        status    = row.get("status", "PASSED")
        sharpe_s = f"{sharpe:.3f}" if sharpe is not None and pd.notna(sharpe) else "—"
        qs_s     = f"{qs:.3f}"    if qs     is not None and pd.notna(qs)     else "—"
        label = f"Alpha #{alpha_id} | {status} | Sharpe {sharpe_s} | QScore {qs_s} | WQ ID: {wq_id}"
        with st.expander(label):
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Sharpe",      f"{sharpe:.3f}" if pd.notna(sharpe) else "—")
            m2.metric("Fitness",     f"{row.get('fitness'):.3f}" if pd.notna(row.get('fitness')) else "—")
            m3.metric("Turnover",    f"{row.get('turnover'):.1f}%" if pd.notna(row.get('turnover')) else "—")
            m4.metric("Returns",     f"{row.get('returns'):.2%}" if pd.notna(row.get('returns')) else "—")
            m5.metric("Sub-Sharpe",  f"{row.get('sub_sharpe'):.3f}" if pd.notna(row.get('sub_sharpe')) else "—")
            m6.metric("MaxCorr",     f"{row.get('max_corr'):.3f}" if pd.notna(row.get('max_corr')) else "—")

            if not yearly.empty and "alpha_id" in yearly.columns:
                y_data = yearly[yearly["alpha_id"] == alpha_id].copy()
                if not y_data.empty:
                    st.markdown("**연도별 성과**")
                    display_cols = ["year", "sharpe", "fitness", "turnover",
                                    "returns", "drawdown", "margin", "long_count", "short_count"]
                    display_cols = [c for c in display_cols if c in y_data.columns]
                    st.dataframe(
                        y_data[display_cols].rename(columns={
                            "year": "Year", "sharpe": "Sharpe", "fitness": "Fitness",
                            "turnover": "Turnover (%)", "returns": "Returns",
                            "drawdown": "Drawdown", "margin": "Margin",
                            "long_count": "Long", "short_count": "Short",
                        }),
                        hide_index=True, use_container_width=True,
                    )

            analysis = row.get("llm_analysis")
            if analysis and str(analysis).strip():
                st.markdown("**LLM 분석**")
                st.markdown(str(analysis))

            st.markdown("**FASTEXPR**")
            st.code(str(row.get("code", "")), language="text")

st.divider()

# ── Recent Runs ───────────────────────────────────────────────────────
st.markdown("### Recent Runs (최신 30개)")

STATUS_COLOR_MAP = {
    "PASSED":           "#00c853",
    "PASSED_A":         "#00c853",
    "PASSED_B":         "#69f0ae",
    "PASSED_C":         "#b9f6ca",
    "REJECTED":         "#ff5252",
    "REJECTED_BY_CORR": "#ff6d00",
    "FAILED":           "#b0bec5",
    "PENDING":          "#546e7a",
}

recent = df.sort_values("created_at", ascending=False).head(30)


def _make_log_df(src):
    want = ["id", "status", "sharpe", "fitness", "turnover",
            "returns", "sub_sharpe", "max_corr", "quality_score",
            "failed_checks", "created_at"]
    cols = [c for c in want if c in src.columns]
    return src[cols].rename(columns={
        "id": "Alpha ID", "status": "Status",
        "sharpe": "Sharpe", "fitness": "Fitness", "turnover": "Turnover (%)",
        "returns": "Returns", "sub_sharpe": "Sub-Sharpe", "max_corr": "MaxCorr",
        "quality_score": "QScore", "failed_checks": "Failed Checks",
        "created_at": "Created",
    })


def _color_status(val):
    for key, color in STATUS_COLOR_MAP.items():
        if isinstance(val, str) and key in val:
            text = "#000" if color in ("#00c853", "#69f0ae", "#b9f6ca", "#b0bec5") else "#fff"
            return f"background-color:{color};color:{text}"
    return ""


tab_all, tab_rej, tab_fail = st.tabs(["All", "Rejected", "Failed"])

with tab_all:
    st.dataframe(
        _make_log_df(recent).style.map(_color_status, subset=["Status"]),
        use_container_width=True,
    )

with tab_rej:
    rej = recent[recent["status"].str.startswith("REJECTED", na=False)]
    if rej.empty:
        st.info("없음")
    else:
        st.caption("Failed Checks 컬럼에서 어느 기준을 통과 못 했는지 확인")
        st.dataframe(
            _make_log_df(rej).style.map(_color_status, subset=["Status"]),
            use_container_width=True,
        )

with tab_fail:
    fail = recent[recent["status"].str.startswith("FAILED", na=False)]
    if fail.empty:
        st.info("없음")
    else:
        want = ["id", "status", "created_at"]
        st.caption("코드 오류로 시뮬레이션 자체 실패 — 메트릭 없음")
        st.dataframe(
            fail[[c for c in want if c in fail.columns]].rename(
                columns={"id": "Alpha ID", "status": "Error", "created_at": "Created"}
            ),
            use_container_width=True,
        )
