# Elequant-Miner

WorldQuant Brain 플랫폼에서 퀀트 알파 전략을 자동으로 생성하고 시뮬레이션하는 자동화 도구입니다.

---

## 개요

Elequant-Miner는 Google Gemini LLM을 활용하여 FASTEXPR 알파 팩터를 자동 생성하고, WorldQuant Brain API를 통해 시뮬레이션을 실행합니다.

시뮬레이션 결과를 분석하여 합격 전략을 SQLite DB에 저장하고, 3가지 모드를 순환하며 전략을 진화시킵니다.

1. **합격 전략 진화**: quality\_score 기반으로 우수 전략을 선택하여 개선
2. **Near-Miss 개선**: 간당간당하게 탈락한 전략을 집중 분석하여 재시도
3. **신규 탐색**: LLM이 새로운 테마로 다양한 전략을 생성

---

## 주요 기능

- **3슬롯 병렬 시뮬레이션**: 동시 3개 슬롯으로 대기 시간 최소화
- **Near-Miss 부모 선택**: 기준에 아깝게 실패한 전략을 gap 수치와 함께 LLM에 전달하여 집중 개선
- **Quality Score**: `(Sharpe × Fitness) / (1 + |Turnover−25| / 25)` — 단순 합격 여부 대신 종합 품질 점수로 진화 우선순위 결정
- **연도별 성과 추적**: Yearly Sharpe, Fitness, Returns, Drawdown, Turnover 저장
- **자동 오류 수정**: 시뮬레이션 실패 시 오류 메시지를 LLM에 전달하여 코드 자동 수정 (최대 3회)
- **LLM 피드백 루프**: 시뮬레이션 완료마다 실패 원인 분석 및 개선 방향 저장
- **WQ Brain 기록 동기화**: `tools/sync_brain_history.py`로 WQ Brain 웹에서 만든 전략도 로컬 DB에 임포트
- **Streamlit 대시보드**: Parallel Coordinates 성과 맵, Lineage Tree 시각화, 연도별 breakdown

---

## 합격 기준

WorldQuant Brain `/alphas/{id}/check` 엔드포인트의 7개 체크를 모두 통과해야 합격입니다.

| Check | 기준 |
|-------|------|
| LOW_SHARPE | Sharpe ≥ 1.25 |
| LOW_FITNESS | Fitness ≥ 1.0 |
| LOW_TURNOVER | Turnover ≥ 1% |
| HIGH_TURNOVER | Turnover ≤ 70% |
| LOW_SUB_UNIVERSE_SHARPE | WQ 동적 결정 (약 0.7) |
| SELF_CORRELATION | 기존 제출 알파 대비 상관계수 < 0.7 |
| CONCENTRATED_WEIGHT | 포지션 분산 여부 (WQ 자동 판단) |

---

## 설치

```bash
git clone https://github.com/your-repo/elequant-miner.git
cd elequant-miner

python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS / Linux

pip install -r requirements.txt
```

---

## 설정

`.env.example`을 복사하여 `.env` 파일을 생성하고 실제 값을 입력합니다.

```
GEMINI_API_KEY=your_gemini_api_key
WQ_EMAIL=your_worldquant_email
WQ_PASSWORD=your_worldquant_password
```

---

## 실행

```bash
# 메인 마이너
python miner.py

# 대시보드
streamlit run dashboard.py

# WQ Brain 기존 전략 동기화
python tools/sync_brain_history.py
```

`miner.py` 실행 시 연구 테마 입력 프롬프트가 표시됩니다. 엔터 입력 시 Full Auto 모드로 다양한 테마를 자동 선택합니다.

---

## 디렉토리 구조

```
elequant-miner/
├── miner.py                    # 메인 루프, 3슬롯 병렬 관리
├── dashboard.py                # Streamlit 대시보드
├── core/
│   ├── ai_engine.py            # Gemini API (생성, 수정, 분석, 할당량 관리)
│   ├── api_client.py           # WorldQuant Brain API 클라이언트
│   └── db_manager.py           # SQLite 스키마 초기화
├── tools/
│   └── sync_brain_history.py   # WQ Brain 기존 전략 로컬 DB 동기화
├── utils/
│   ├── paths.py                # 프로젝트 경로 상수
│   ├── dedup_manager.py        # shared_tried.json 중복 방지 관리
│   ├── report_generator.py     # 성과 보고서 생성
│   └── system_monitor.py       # 시스템 모니터링
├── data/
│   ├── operators.json          # FASTEXPR 연산자 지식 베이스
│   ├── datafields.json         # WQ Brain 데이터 필드 목록
│   ├── quota_state.json        # Gemini API 일일 사용량 (자동 갱신)
│   └── shared_tried.json       # 시도한 전략 코드 dedup (git 공유 가능)
├── research/
│   └── elequant.db             # 전략 이력 SQLite DB (자동 생성, git 제외)
├── .env.example
└── requirements.txt
```

---

## API 할당량

| 용도 | 모델 | RPM | RPD |
|------|------|-----|-----|
| 신규 전략 생성 | gemini-2.5-flash | 10 | 500 |
| 오류 수정 / 결과 분석 | gemini-2.5-flash-lite | 30 | 1,500 |

일일 사용량은 `data/quota_state.json`에 저장되며 재시작 후에도 누적됩니다. 한도 소진 시 자정까지 자동 대기합니다.

---

## 주의 사항

- WorldQuant Brain API 서비스 이용 약관을 준수하십시오.
- `.env` 파일은 절대 버전 관리에 포함시키지 않습니다.
- `research/elequant.db`는 로컬에만 존재합니다. `data/shared_tried.json`만 git으로 공유됩니다.
