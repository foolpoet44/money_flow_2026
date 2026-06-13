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
python collector/collector.py        # → data.json 생성 (외부 의존성 없음)
```

> **데이터 소스 = 네이버 금융 JSON API.** 원래 설계(pykrx)는 KRX 데이터포털이
> 로그인(KRX_ID/KRX_PW)을 의무화하면서 막혔다. 네이버 모바일/증권 API는 익명으로
> 같은 데이터를 주므로 전환했고, 부수효과로 외부 패키지가 전부 빠져 표준 라이브러리만 쓴다.
> 소스·단위 매핑은 `collector/collector.py` 상단 주석 참고.

## 진행 현황

- [x] **Sprint 0** — 스캐폴드 & 계약 고정 (독립 git, 폴더 구조, 시드 이전)
- [x] **Sprint 1** — 실데이터 수집 (네이버 JSON API 전환, 재시도·검증, `data.json` 실생성)
- [ ] **Sprint 2** — 신호 엔진 분리 & 단위 테스트 (`signal_engine.js`)
- [ ] **Sprint 3** — 대시보드 데이터 구동 (`window.DATA` 우선 + 합성 fallback)
- [ ] **Sprint 4** — 자동화 & 텔레그램 알림
