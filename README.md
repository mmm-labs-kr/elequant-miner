# Elequant-Miner

Elequant-Miner는 WorldQuant Brain 플랫폼을 위한 자동화된 퀀트 전략 발굴 시스템입니다. 대규모 언어 모델(LLM)을 활용하여 알파 팩터(FASTEXPR)를 생성, 시뮬레이션 및 최적화하며, 자기 교정 피드백 루프를 통해 전략을 지속적으로 진화시킵니다.

## 주요 기능

- **자동화된 마이닝 루프**: 알파 팩터의 생성 및 시뮬레이션을 중단 없이 자동으로 수행합니다.
- **전략 자기 진화**: 과거에 우수한 성과를 거둔 알파를 기반으로 새로운 전략을 파생 및 고도화합니다.
- **자기 교정 시스템**: 시뮬레이션 오류를 자동으로 탐지하고 LLM을 통해 구문 오류 및 형식 문제를 수정합니다.
- **데이터 기반 분석**: 생성된 전략, 성능 지표(Sharpe, Fitness, Turnover 등) 및 분석 피드백을 로컬 SQLite 데이터베이스에 통합 관리합니다.
- **인증 자동화 지원**: WorldQuant Brain의 생체 인증 및 브라우저 상호작용 과정을 처리합니다.
- **API 속도 제한 관리**: LLM 및 플랫폼 API의 할당량을 준수하기 위한 자체 Throttling 로직을 포함합니다.

## 프로젝트 구조

```text
elequant-miner/
├── miner.py                # 메인 마이닝 루프 및 실행 제어
├── core/
│   ├── ai_engine.py        # LLM 연동 및 전략 생성/수정 로직
│   ├── api_client.py       # WorldQuant Brain API 통신 및 결과 수집
│   └── db_manager.py       # SQLite 데이터베이스 이력 및 지표 관리
├── data/
│   ├── datafields.json     # 사용 가능한 데이터 필드 지식 베이스
│   └── operators.json      # FASTEXPR 연산자 지식 베이스
├── utils/
│   ├── paths.py            # 프로젝트 자원 경로 관리
│   ├── report_generator.py # 성능 보고서 생성 유틸리티
│   └── system_monitor.py   # 시스템 상태 및 자원 모니터링
├── research/
│   └── elequant.db         # 전략 이력 및 성능 데이터 저장소
└── logs/                   # 실행 로그 기록
```

## 사전 요구 사항

- Python 3.10 이상
- WorldQuant Brain 계정
- Google Gemini API 키

## 설치 방법

1. 저장소 복제:
   ```bash
   git clone https://github.com/your-repo/elequant-miner.git
   cd elequant-miner
   ```

2. 가상환경 구성 및 활성화:
   ```bash
   python -m venv venv
   source venv/Scripts/activate  # Windows: venv\Scripts\activate
   ```

3. 의존성 설치:
   ```bash
   pip install -r requirements.txt
   ```

4. 환경 변수 설정:
   루트 디렉토리에 `.env` 파일을 생성하고 아래 내용을 설정합니다.
   ```env
   WQ_EMAIL=your_email@example.com
   WQ_PASSWORD=your_password
   GEMINI_API_KEY=your_gemini_api_key
   ```

## 사용 방법

마이닝 프로세스 시작:
```bash
python miner.py
```

시스템 동작 순서:
1. WorldQuant Brain 로그인 수행 (필요 시 생체 인증 처리).
2. LLM을 통한 새로운 전략 또는 진화된 전략 생성.
3. 시뮬레이션 제출 및 실시간 상태 모니터링.
4. 결과 수집 및 성능 지표 분석.
5. 오류 발생 시 자동 수정 로직 실행 및 재시도.
6. 성과가 검증된 전략을 데이터베이스에 저장하고 향후 진화의 씨앗(Seed)으로 활용.

## 라이선스

본 프로젝트는 연구 및 교육 목적으로 제공됩니다. WorldQuant Brain의 서비스 이용 약관을 준수하여 사용하시기 바랍니다.
