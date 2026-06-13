# 자금 조류 · Money Flow Intelligence

한국 주식시장의 자금 흐름(외국인·기관 수급)을 일 단위로 읽어 행동 가능한 신호로 바꾸는 모니터링 시스템.
추적 대상은 KOSPI/KOSDAQ 지수 수급과 반도체 섹터(삼성전자·SK하이닉스·삼성전기·SK스퀘어·한미반도체) 종목별 수급이다.

> 설계·신호 명세·데이터 계약의 정본은 [`CLAUDE.md`](./CLAUDE.md)다. 이 README는 실행 빠른 시작만 다룬다.

## 구조

```
money_flow_2606/
├── CLAUDE.md              # 설계 정본 (데이터 계약 §4, 신호 엔진 §5)
├── collector/
│   ├── collector.py       # pykrx 수집기 → data.json
│   └── requirements.txt   # pykrx, pandas
├── dashboard/
│   └── index.html         # 대시보드 (현재 합성 데이터 내장)
└── scripts/               # 자동화 진입점 (Sprint 4)
```

## 빠른 시작

### 대시보드 (지금 바로)

현재 대시보드는 합성 데이터로 동작한다. 브라우저로 바로 열면 된다.

```bash
open dashboard/index.html
```

화면 상단 **"합성 데이터"** 배너는 시연용 더미임을 명시한다. 실수급 전환은 Sprint 3 완료 후.

### 수집기 (로컬, 네트워크 필요)

```bash
pip install -r collector/requirements.txt
python collector/collector.py        # → data.json 생성
```

> pykrx는 KRX/네이버 스크래핑이라 호출이 느리고, 반환 컬럼명(한글)이 버전별로 미세하게 다를 수 있다.
> 컬럼이 안 맞으면 `collector.py`의 `FOREIGN_COLS` / `INSTI_COLS` 후보를 조정한다.

## 진행 현황

- [x] **Sprint 0** — 스캐폴드 & 계약 고정 (독립 git, 폴더 구조, 시드 이전)
- [ ] **Sprint 1** — 실데이터 수집 경화 (pykrx 컬럼 검증, 재시도, `data.json` 실생성)
- [ ] **Sprint 2** — 신호 엔진 분리 & 단위 테스트 (`signal_engine.js`)
- [ ] **Sprint 3** — 대시보드 데이터 구동 (`window.DATA` 우선 + 합성 fallback)
- [ ] **Sprint 4** — 자동화 & 텔레그램 알림
