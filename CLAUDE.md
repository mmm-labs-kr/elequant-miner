# Elequant Miner — IQC Project Context

## 대회 정보
- **대회**: WorldQuant IQC (International Quant Championship)
- **플랫폼**: WorldQuant Brain (`api.worldquantbrain.com`)
- **목표**: FASTEXPR 언어로 알파 팩터를 작성하고 시뮬레이션을 통과시켜 제출

## 시뮬레이션 설정
- `region`: USA (고정)
- `delay`: 1 (고정)
- `unitHandling`: VERIFY (고정)
- `language`: FASTEXPR (고정)

아래 설정은 AI가 전략별로 판단해서 결정:
| 파라미터 | 기본값 | 범위 |
|---------|--------|------|
| `decay` | 6 | 0~512 (정수) |
| `truncation` | 0.08 | 0.00~1.00 |
| `universe` | TOP3000 | TOP3000/TOP2000/TOP1000/TOP500/TOP200/TOPSP500 |
| `neutralization` | SUBINDUSTRY | NONE/MARKET/SECTOR/INDUSTRY/SUBINDUSTRY |
| `pasteurization` | ON | ON/OFF |
| `nanHandling` | OFF | ON/OFF |

## 합격 기준 (WQ Brain /check 엔드포인트 기준)
| 체크명 | 기준 |
|--------|------|
| LOW_SHARPE | Sharpe ≥ 1.25 |
| LOW_FITNESS | Fitness ≥ 1.0 |
| LOW_TURNOVER | Turnover ≥ 1% |
| HIGH_TURNOVER | Turnover ≤ 70% |
| LOW_SUB_UNIVERSE_SHARPE | Sub-universe Sharpe ≥ ~0.7 (WQ가 동적으로 결정) |
| SELF_CORRELATION | 기존 제출 알파와 상관계수 < 0.7 |
| CONCENTRATED_WEIGHT | 포지션 분산 여부 (WQ 자동 판단) |

**주의**: miner.py는 WQ Brain `/alphas/{id}/check` 응답을 직접 파싱해서 합격 여부 판정. 우리가 기준을 직접 계산하지 않음.

## FASTEXPR 오퍼레이터 규칙 (CRITICAL)

### 자주 틀리는 실수 — 반드시 지켜야 함
- `stddev(x, d)` → **WRONG**, 존재하지 않는 함수
- `ts_stddev(x, d)` → **WRONG**, 존재하지 않는 함수
- **올바른 표준편차**: `ts_std_dev(x, d)` ← 이 이름만 사용
- `group_mean(x, group)` → **WRONG** (파라미터 2개)
- **올바른**: `group_mean(x, weight, group)` ← weight 파라미터 필수

### 전체 오퍼레이터 목록 (IQC 공식)
전체 시그니처는 `data/operators.json` 참조. 아래는 카테고리별 요약.

**Arithmetic**: `add(x,y)` `subtract(x,y)` `multiply(x,y)` `divide(x,y)` `power(x,y)` `signed_power(x,y)` `abs(x)` `log(x)` `sqrt(x)` `sign(x)` `inverse(x)` `reverse(x)` `max(x,y)` `min(x,y)` `densify(x)`

**Logical**: `and(x,y)` `or(x,y)` `not(x)` `is_nan(x)` `if_else(cond,x,y)` `equal` `less` `greater` `less_equal` `greater_equal` `not_equal`

**Time Series**: `ts_sum(x,d)` `ts_mean(x,d)` `ts_std_dev(x,d)` `ts_rank(x,d)` `ts_delta(x,d)` `ts_delay(x,d)` `ts_zscore(x,d)` `ts_corr(x,y,d)` `ts_covariance(y,x,d)` `ts_product(x,d)` `ts_decay_linear(x,d)` `ts_scale(x,d)` `ts_quantile(x,d)` `ts_backfill(x,d)` `ts_arg_max(x,d)` `ts_arg_min(x,d)` `ts_av_diff(x,d)` `ts_count_nans(x,d)` `ts_regression(y,x,d,lag=0,rettype=0)` `days_from_last_change(x)` `last_diff_value(x,d)` `ts_step(1)` `kth_element(x,d,k)` `hump(x)`

**Cross Sectional**: `rank(x)` `zscore(x)` `scale(x)` `normalize(x)` `quantile(x)` `winsorize(x,std=4)`

**Group**: `group_mean(x,weight,group)` `group_rank(x,group)` `group_zscore(x,group)` `group_scale(x,group)` `group_neutralize(x,group)` `group_backfill(x,group,d)`

**Vector**: `vec_sum(x)` `vec_avg(x)`

**Transformational**: `bucket(x,range="0,1,0.1")` `trade_when(cond,x,y)`

## 데이터 필드
- 전체 목록: `data/datafields.json`
- 원본 CSV: `D:\GitHub\IQC_brain_datafields.csv`
- 필드 타입: `MATRIX` (종목×날짜 행렬), `VECTOR` (종목당 벡터)
- 주요 그룹 필드: `sector`, `industry`, `subindustry`

## 코드 구조
- `miner.py` — 메인 루프 (3슬롯 병렬 시뮬레이션)
- `core/ai_engine.py` — Gemini API 연동, 알파 생성/수정
- `core/api_client.py` — WorldQuant Brain API 클라이언트
- `core/db_manager.py` — SQLite 전략 저장
