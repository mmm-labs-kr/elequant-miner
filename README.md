# Elequant-Miner

WorldQuant Brain 플랫폼에서 퀀트 알파 전략을 자동으로 생성하고 시뮬레이션하는 자동화 도구입니다.

---

## 개요

Elequant-Miner는 Google Gemini LLM을 활용하여 FASTEXPR 알파 팩터를 자동 생성하고, WorldQuant Brain API를 통해 시뮬레이션을 실행합니다. 시뮬레이션 결과를 분석하여 합격 기준을 통과한 전략을 SQLite 데이터베이스에 저장하고, 성과가 좋은 전략을 기반으로 새로운 전략을 진화시키는 폐쇄 루프(Closed-Loop) 구조로 동작합니다.

단순한 기준 통과 여부 확인을 넘어, 결과마다 LLM이 구체적인 원인을 분석하고 그 분석을 다음 세대 전략 생성에 반영합니다. 시간이 지날수록 축적된 피드백을 바탕으로 점진적으로 더 나은 전략을 생성합니다.

---

## 주요 기능

- **자동 전략 생성**: Gemini LLM이 FASTEXPR 문법에 맞는 알파 팩터를 자동 생성. 18개 데이터 필드 카테고리를 순환하여 다양한 전략을 유도합니다.
- **병렬 시뮬레이션**: 최대 3개 시뮬레이션 슬롯을 동시에 관리하여 대기 시간을 최소화합니다.
- **자동 오류 수정**: 시뮬레이션 실패 시 오류 메시지를 분석하여 코드를 자동 수정합니다. (최대 3회 재시도)
- **결과 분석 및 피드백**: 시뮬레이션 완료마다 LLM이 결과를 분석하여 실패 원인과 개선 방향을 도출하고 데이터베이스에 저장합니다.
- **방향성 있는 진화**: 부모 전략의 LLM 분석 결과를 다음 세대 생성 프롬프트에 포함시켜, 단순 변형이 아닌 약점을 겨냥한 개선이 이루어집니다.
- **합격 전략 품질 등급**: 기준 통과 여부 외에 A/B/C 등급을 부여하여 어떤 전략이 진화 원본으로 적합한지 우선순위를 부여합니다.
- **API 할당량 관리**: RPM(분당) 및 RPD(일일) 기반 지능형 속도 조절. 일일 사용량을 파일로 영속화하여 재시작 후에도 한도 초과를 방지합니다.
- **사전 검증**: WQ Brain 전송 전 괄호 균형 및 함수명 검증으로 불필요한 API 호출을 절감합니다.
- **상세 실패 로그**: 어떤 기준이 왜 실패했는지 수치와 함께 즉시 확인할 수 있습니다.
- **리서치 보고서**: SQLite 기반 성과 분석 보고서를 자동 생성합니다.

---

## 시스템 요구사항

- Python 3.12 이상
- Google Gemini API 키 (무료 티어 사용 가능)
- WorldQuant Brain 계정

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

`.env` 파일에는 실제 인증 정보가 포함되므로 절대 버전 관리에 포함시키지 않습니다. `.gitignore`에 `.env`가 등록되어 있는지 확인하십시오.

---

## 실행

```bash
python miner.py
```

실행 시 연구 테마 입력 프롬프트가 표시됩니다.

- **테마 입력 예시**: `배당수익률과 부채비율을 엮어줘`, `RSI 리버전 전략 집중 연구`
- **엔터 입력**: Full Auto 모드로 동작하며 다양한 테마를 자동으로 선택합니다.

WorldQuant Brain 계정에 Persona 생체인증이 설정되어 있는 경우, 최초 실행 시 브라우저가 자동으로 열립니다. 인증 완료 후 터미널에서 엔터를 누르면 실행이 재개됩니다.

---

## 동작 구조

```
[사용자 테마 입력]
        |
        v
[Gemini LLM: FASTEXPR 전략 생성]  <-- 부모 전략의 LLM 분석 결과 반영
        |
        v
[FASTEXPR 사전 검증: 괄호 균형, 함수명]
        |
        v
[WQ Brain API: 시뮬레이션 요청] --- 최대 3개 병렬 슬롯
        |
        v
[결과 수집: Sharpe, Fitness, Turnover, 상관계수, 연도별 통계]
        |
        v
[합격 기준 검증 + 실패 기준 상세 로그]
        |
       / \
    통과   실패
      |       |
  등급 부여   오류 수정 (Gemini LLM, flash-lite, 최대 3회)
  A / B / C
      |
  DB 저장
      |
[Gemini LLM: 결과 분석] --> "20일 모멘텀이 과도 스무딩. lookback 축소 권장"
      |
  feedback DB 저장
      |
[_get_best_parent: quality_score 기반 가중 선택]
      |
      +--> [루프 반복]
```

---

## 합격 기준 및 품질 등급

### 합격 기준

WorldQuant IQC 기준을 바탕으로 설정됩니다. 아래 기준을 모두 충족해야 합격입니다.

| 지표 | 기준값 |
|------|--------|
| Sharpe | 1.25 이상 |
| Fitness | 1.0 이상 |
| Turnover | 1% ~ 70% |
| 최대 상관계수 | 0.7 미만 |
| 연도별 Sharpe | 0.1 이상 (모든 연도) |

### 품질 등급

합격 전략에는 지표 수준에 따라 A/B/C 등급이 부여됩니다. 등급이 높을수록 다음 세대 진화의 원본으로 더 자주 선택됩니다.

| 등급 | 조건 | 의미 |
|------|------|------|
| PASSED_A | Sharpe ≥ 1.6 이고 Turnover 10~50% | 우수. 제출 우선 검토 대상 |
| PASSED_B | Sharpe ≥ 1.4 | 양호. 진화 원본으로 적합 |
| PASSED_C | 기준 충족 | 기준 통과. 추가 개선 필요 |

### 실패 로그 예시

실패 시 어떤 기준이 왜 실패했는지 수치와 함께 즉시 확인할 수 있습니다.

```
Alpha 12 REJECTED — failed: Sharpe, Fitness | Sharpe=0.850, Fitness=0.730, Turnover=42.1%, MaxCorr=0.45
Alpha 13 → PASSED_B | Sharpe=1.43, Fitness=1.18, Turnover=31.2%, MaxCorr=0.38
```

---

## LLM 결과 분석 및 방향성 진화

시뮬레이션이 완료될 때마다 LLM(gemini-2.5-flash-lite)이 결과를 분석하여 원인과 개선 방향을 도출합니다.

**분석 예시 (Sharpe=0.85, Fitness=0.73 실패 전략):**

```
The alpha's structure relies on a simple 20-day mean of daily returns. This smooths out
noisy single-day price changes but likely over-smooths, reducing predictive power.

The weakest metric is Sharpe (0.85). To improve:
1. Reduce lookback: change 20 to 10 or 5 to capture shorter-term momentum.
2. Introduce volatility weighting to make recent returns more influential.
```

이 분석은 `research/elequant.db`의 `feedback.llm_analysis` 컬럼에 저장됩니다.

다음 세대 진화 시 부모 전략의 분석 결과가 프롬프트에 포함됩니다.

```
Successful alpha to evolve:
Code: rank(ts_mean(close / ts_delay(close, 1) - 1, 20))
Sharpe: 1.35, Fitness: 1.12, Turnover: 42.0

Previous analysis of this strategy:
  The 20-day lookback captures medium-term momentum but lacks sector neutralization,
  leading to high correlation with market beta. Adding group_zscore(x, sector)
  would reduce MaxCorr significantly.

Evolve this alpha using the analysis above as a guide...
```

---

## API 할당량 관리

신규 전략 생성과 오류 수정 및 결과 분석에 서로 다른 모델을 사용하여 고품질 유지와 할당량 효율을 동시에 달성합니다.

| 용도 | 모델 | RPM 한도 | RPD 한도 |
|------|------|----------|----------|
| 신규 전략 생성 | gemini-2.5-flash | 10 | 500 |
| 오류 수정 / 결과 분석 | gemini-2.5-flash-lite | 30 | 1,500 |

일일 사용량은 `data/quota_state.json`에 저장됩니다. 프로세스를 재시작해도 이전 사용량을 불러와 한도 초과를 방지합니다. 한도 소진 시 자정까지 자동 대기합니다.

---

## 디렉토리 구조

```
elequant-miner/
├── miner.py                    # 메인 오케스트레이터 및 슬롯 관리
├── core/
│   ├── ai_engine.py            # Gemini API 엔진 (전략 생성, 수정, 결과 분석, 할당량 관리)
│   ├── api_client.py           # WorldQuant Brain API 클라이언트
│   └── db_manager.py           # SQLite 스키마 초기화 및 인덱스 관리
├── utils/
│   ├── paths.py                # 프로젝트 경로 상수
│   ├── process_knowledge.py    # CSV -> JSON 지식 베이스 변환 (초기 1회 실행)
│   ├── report_generator.py     # 성과 보고서 생성
│   └── system_monitor.py       # CPU / 메모리 모니터링
├── data/
│   ├── operators.json          # FASTEXPR 연산자 지식 베이스
│   ├── datafields.json         # WQ Brain 데이터 필드 지식 베이스
│   └── quota_state.json        # API 일일 사용량 영속 저장 (자동 생성 및 갱신)
├── research/
│   └── elequant.db             # 전략 이력 SQLite 데이터베이스 (자동 생성)
│       ├── alphas              # 전략 코드, 상태(PASSED_A/B/C, REJECTED 등), 계보
│       ├── metrics             # Sharpe, Fitness, Turnover, 상관계수 등
│       ├── feedback            # LLM 결과 분석 텍스트
│       └── lineage             # 부모-자식 전략 계보 추적
├── logs/
│   └── miner.log               # 실행 로그
├── .env.example                # 환경 변수 템플릿
├── requirements.txt
└── todo.md
```

---

## 보고서 생성

실행 중 또는 종료 후 언제든 아래 명령으로 성과 요약 보고서를 생성할 수 있습니다.

```bash
python utils/report_generator.py
```

`research/summary_report.md` 파일로 합격 전략 목록과 주요 지표가 출력됩니다.

---

## Gemini API 키 발급

1. [Google AI Studio](https://aistudio.google.com)에 접속합니다.
2. 상단 메뉴에서 **Get API key**를 선택합니다.
3. 발급된 키를 `.env` 파일의 `GEMINI_API_KEY` 항목에 입력합니다.

무료 티어로 운영 시 일 500회(신규 생성) / 일 1,500회(오류 수정 및 결과 분석) 한도 내에서 동작합니다.

---

## 주의 사항

- 본 도구는 WorldQuant Brain의 API를 사용합니다. 서비스 이용 약관을 반드시 확인하고 준수하십시오.
- 생성된 전략의 실제 제출 및 수익 보장에 대해 본 프로젝트는 어떠한 책임도 지지 않습니다.
- Gemini API 무료 티어 한도 내에서 운영하려면 연속 실행 시간을 조절하십시오.
