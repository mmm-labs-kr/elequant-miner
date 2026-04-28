# Elequant-Miner Project TODO

## 1단계: 기초 환경 및 지식 베이스 구축 (Setup & Knowledge Base)
- [x] `elequant-miner` 기본 폴더 구조 및 가상환경 설정 <!-- id: 18 -->
- [x] `IQC_brain_operators.csv` 및 `IQC_brain_datafields.csv` 파싱 및 Gemini 최적화 지식 베이스(JSON) 변환 <!-- id: 19 -->
- [x] SQLite 데이터베이스 설계 (`alphas`, `metrics`, `lineage`, `feedback` 테이블) <!-- id: 20 -->

## 2단계: 핵심 엔진 개발 (Core Engines)
- [x] **Gemini API 연동 모듈**: 
    - 1분당 호출 제한(Throttling) 로직 포함
    - 시스템 프롬프트 및 동적 필드 검색(Dynamic Field Filter) 구현 <!-- id: 21 -->
- [x] **상관계수 검증 로직**: 기존 포트폴리오와의 상관도(0.7 미만) 필터링 구현 <!-- id: 30 -->
- [x] **WQ Brain API 클라이언트**:
    - `WQ-Brain-auto` 방식의 Persona 생체인증(webbrowser) 처리 로직 이식
    - 시뮬레이션 슬롯(5개) 및 상태 모니터링 관리 <!-- id: 22 -->

## 3단계: 지능형 자동화 루프 (Intelligent Autonomous Loop)
- [x] **Closed-Loop 오케스트레이터**:
    - 생성(LLM) -> 시뮬레이션(WQ) -> 결과 분석 -> 피드백(LLM) -> 진화(LLM) 무한 루프 구현 <!-- id: 23 -->
- [x] **에러 복구 시스템**: 시뮬레이션 에러 메시지 분석 및 자동 코드 수정 로직 <!-- id: 24 -->
- [x] 백그라운드 실행 지원 및 실시간 진행 상황 로깅 시스템 <!-- id: 25 -->

## 4단계: 전략 관리 및 보고 (Management & Reporting)
- [x] 전략 가계도 추적 및 성과 시각화 기능 <!-- id: 26 -->
- [x] 최종 합격 가능성 높은 전략 선별 및 상세 연구 보고서 자동 생성 <!-- id: 27 -->

## 5단계: 확장 및 고도화 (Optimization)
- [x] 노트북 환경 최적화 (CPU/Memory 리소스 모니터링) <!-- id: 28 -->
- [ ] 사용자 정의 가이드라인(PDF/Text) 추가 주입 기능 <!-- id: 29 -->
