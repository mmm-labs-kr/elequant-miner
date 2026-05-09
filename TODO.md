# Elequant Miner — 작업 목록

완료 기준을 명시. 기준 미충족 시 완료 처리 금지.

---

## 완료

- Parallel Coordinates 시각화 (dashboard.py)
- near-miss 부모 선택 + 3-mode 로테이션 (miner.py)
  - 검증: REJECTED 105개 near-miss 후보, top5 sharpe>1.4 / fitness>1.0 / turnover 정상
- quality_score 저장, PASSED 단일 상태 (A/B/C 제거)
- yearly_metrics 테이블 생성 (데이터는 다음 시뮬레이션부터)
- Criteria Reference 패널, Passed Strategies expander

---

## 미완료

### [1] researcher 기존 데이터 업데이트 — 구현 완료, 실행 필요

python tools/sync_brain_history.py --fix-researcher 이름
  -> UPDATE alphas SET researcher=이름 WHERE researcher='unknown'
  -> 출력: "researcher 업데이트: N개"

Done 기준:

현재: DB 175개 전부 researcher='unknown'.
원인: sync 스크립트가 기존 wq_alpha_id를 skip해서 --researcher 줘도 기존 항목 업데이트 안 됨.

구현: sync_brain_history.py에 --fix-researcher NAME 옵션 추가
  동작: UPDATE alphas SET researcher=NAME WHERE source='brain_web' AND researcher='unknown'
  출력: "researcher 업데이트: N개"

Done 기준:
  python tools/sync_brain_history.py --fix-researcher kjs001791 실행 후
  SELECT researcher, COUNT(*) FROM alphas GROUP BY researcher 에 이름 나옴

---

### [2] 대시보드 researcher 필터 안내 보완

현재: sidebar에 All만 보임. RESEARCHER_NAME이 .env에 있어도 DB에 없으면 선택지 없음.

구현:
  - MY_RESEARCHER 설정됐지만 DB에 없음 -> sidebar 경고 메시지 + 실행 명령어 안내
  - MY_RESEARCHER 설정 + DB에 있음 -> radio 선택 (이미 구현됨)
  - MY_RESEARCHER 미설정 -> .env 설정 안내

Done 기준:
  .env RESEARCHER_NAME 설정 + --fix-researcher 실행 후
  sidebar에 이름 선택지 표시, 선택 시 Overview 수치 = WQ Brain 웹사이트 수

---

### [3] yearly_metrics 필드명 확인

현재: _store_yearly_metrics()가 stats/yearlyStats/annualStats/yearly/performance 키 순서로 시도.
Done: 다음 시뮬레이션 후 SELECT COUNT(*) FROM yearly_metrics > 0

---

## 실행 순서

1. [1] sync_brain_history.py --fix-researcher 옵션 추가 (5줄)
2. [2] dashboard sidebar 안내 메시지 (10줄)
3. [3] 다음 시뮬레이션 결과 확인 후 판단

---

## [4] 파라미터 스윕 + 연도별 컨텍스트 (신규 설계)

### 배경
- near-miss가 LLM에만 의존 → Sharpe 0.05 차이는 파라미터 튜닝으로 해결 가능
- yearly_metrics 데이터가 쌓여있는데 LLM 프롬프트에 활용 안 함

### 동작 흐름 (슬롯 채우기 우선순위)

```
① 진행 중인 스윕 있음?       → 다음 스윕 변형 제출 (LLM 없음)
② 스윕 미실시 near-miss 있음? → 스윕 시작 (LLM 없음)
   (failed_count ≤ 2, numeric_gap < 0.3, sweep_done = 0)
③ 스윕 완료된 near-miss 있음? → LLM 호출 (스윕 결과 + 연도별 컨텍스트)
④ 위 해당 없음                → LLM 신규 탐색 (연도별 컨텍스트 포함)
```

### 스윕 파라미터

| 파라미터 | 탐색 범위 | 조기 종료 |
|---------|----------|---------|
| `decay` | 현재값 기준 [×0.5, ×0.7, ×0.8, ×1, ×1.5, ×2, ×3, ×4] 정수 | 연속 2회 개선 없음 |
| `truncation` | [0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20] | 연속 2회 개선 없음 |
| `universe` | TOP3000 → TOP2000 → TOP1000 → TOP500 | 악화 즉시 종료 |
| `delay` | ⚠ CLAUDE.md에 고정=1로 기록됨 — IQC 규정 확인 후 결정 |

각 파라미터 단독 스윕 → 최적값 조합 → 최종 1회 제출

### Phase 2: 연도별 컨텍스트

- `utils/yearly_context.py`: `build_yearly_context(db_path)` 함수 신규
- PASSED + REJECTED 모두 포함해서 연도별 패턴 추출
  - "2022년: 전체 평균 Sharpe 0.4, 모멘텀 전략 부진"
  - "특정 연도에만 약한" 전략 유형 식별
- LLM 프롬프트 (explore/near-miss 코드 개선) 에 요약 주입

### DB 변경 사항

- `alphas.sweep_done INT DEFAULT 0` — 스윕 완료 여부
- `alphas.settings_json TEXT` — 제출 시 사용한 설정값 JSON 기록
- 스윕 제출 alpha: `source='sweep'`, `parent_id` = 기준 alpha

### 구현 파일

- `core/db_manager.py` — 컬럼 추가
- `miner.py` — `_get_sweep_next()`, `_start_sweep()`, `_finish_sweep()`
- `utils/yearly_context.py` — 연도별 패턴 추출 신규
- `miner.py` — 슬롯 채우기 로직에 스윕 우선순위 삽입

### Done 기준

- near-miss 후보에서 LLM 호출 없이 스윕이 3슬롯 채움
- 스윕 완료 후 LLM 프롬프트에 스윕 결과가 포함됨
- explore 프롬프트에 연도별 요약이 포함됨

### 구현 완료 (Phase 1)

- `_save_alpha()` — source, settings_json 인자 추가
- `_get_sweep_candidate()` — failed_count ≤ 2, numeric_gap < 0.5, sweep_done=0 필터
- `_init_sweep()` — base settings 파싱, decay 변형값 생성
- `_sweep_next()` — phase 소진 시 자동 다음 phase, combo 단계 처리
- `_sweep_combo()` — 각 phase 최적값 조합 1회 제출
- `_on_sweep_result()` — best 업데이트, 조기 종료 (decay/truncation/delay: 2연속, universe: 즉시)
- `_finish_sweep()` — sweep_done=1 마킹
- 슬롯 채우기 루프에 ① 활성 sweep, ② sweep 시작 우선순위 삽입

### 미결 확인 사항

- [x] `delay` 고정 해제 완료 — 1~2 범위로 LLM이 결정
- [x] 기존 alpha의 settings_json 없음 → _init_sweep()에서 default(decay=6, truncation=0.08) 사용
- [ ] Phase 2: yearly_context.py 구현 (build_yearly_context → LLM 프롬프트 주입)
