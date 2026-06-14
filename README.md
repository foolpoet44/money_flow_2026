# 자금 조류 · Money Flow Intelligence

한국 주식시장의 자금 흐름(외국인·기관 수급)을 일 단위로 읽어 행동 가능한 신호로 바꾸는 모니터링 시스템.
추적 대상은 KOSPI/KOSDAQ 지수 수급과 반도체 섹터(삼성전자·SK하이닉스·삼성전기·SK스퀘어·한미반도체) 종목별 수급이다.

> 설계·신호 명세·데이터 계약의 정본은 [`CLAUDE.md`](./CLAUDE.md)다. 이 README는 실행 빠른 시작만 다룬다.

## 구조

```
money_flow_2606/
├── CLAUDE.md                  # 설계 정본 (데이터 계약 §4, 신호 엔진 §5)
├── collector/
│   ├── collector.py           # 수급·지수 수집(네이버 JSON API) → data.json + data.js
│   ├── research_collector.py  # 증권사 리포트 수집(네이버 리서치) → research.json + research.js
│   └── requirements.txt       # 외부 의존성 없음(표준 라이브러리)
├── dashboard/
│   ├── index.html             # 대시보드 (실데이터 우선, 합성 fallback)
│   ├── signal_engine.js       # 신호 엔진(§5 정본, 순수함수)
│   └── signal_engine.test.js  # 단위 테스트 (node로 실행)
└── scripts/                   # 자동화 진입점 (Sprint 4)
```

## 빠른 시작

### 대시보드 (지금 바로)

```bash
open dashboard/index.html
```

`dashboard/data.js`가 있으면 실수급으로, 없으면 합성 데이터로 동작한다(실데이터 시 "합성 데이터" 배너 자동 숨김). `dashboard/research.js`가 있으면 하단에 리서치 다이제스트 섹션이 함께 뜬다.

### 수집기 (로컬, 네트워크 필요)

```bash
python collector/collector.py            # 수급·지수 → data.json + dashboard/data.js
python collector/research_collector.py   # 증권사 리포트 → research.json + dashboard/research.js
node dashboard/signal_engine.test.js     # (선택) 신호 엔진 단위 테스트
```

> **데이터 소스 = 네이버 금융(익명 스크래핑).** 원래 설계(pykrx)는 KRX 데이터포털이
> 로그인(KRX_ID/KRX_PW)을 의무화하면서 막혔다. 수급·지수는 네이버 JSON API, 리서치는
> 네이버 금융 리서치 HTML을 쓴다. 외부 패키지가 전부 빠져 표준 라이브러리만 사용한다.
> 소스·단위 매핑은 각 `collector/*.py` 상단 주석 참고.

### 리서치 다이제스트 (별도 기능)

증권사 리포트를 자동으로 읽어 **일단위 종합 + 정독 추천 + 원본 링크**를 제공한다.

- **요약은 추출형** — AI로 지어내지 않고 리포트 자신의 제목·리드 문단·목표가를 그대로 가져온다(§1.5 거짓 정밀도 금지).
- **오늘의 종합** — 카테고리 분포, 도메인 테마 빈도(오늘의 화두), 리포트 톤(매수/신중), 수급 신호와의 교차 서술을 집계로 생성.
- **정독 추천** — "전체 맥락에서 전문을 읽어볼 만한" 리포트를 설명 가능한 점수로 상위 3건 선별하고 *왜*를 배지로 노출. 최대 가중치는 **수급 신호 교차**(오늘 외국인·기관이 극단 매매한 종목에 마침 나온 리포트). 엔진은 순수함수 `dashboard/research_digest.js`(+ `research_digest.test.js`).

### 자동화 (데일리 무인 실행)

`scripts/run_daily.sh`가 수집→신호 테스트 게이트→다이제스트→텔레그램(Alert 이상)까지 1회로 묶는다. macOS launchd로 평일 08:00(KST) 자동 실행한다.

```bash
scripts/run_daily.sh                  # 수동 1회 실행(드라이런/전송 모두)
launchctl list | grep moneyflow       # 스케줄러 등록 확인
launchctl unload ~/Library/LaunchAgents/com.moneyflow.daily.plist   # 해제
```

텔레그램은 `scripts/run_daily.env`(gitignore·권한 600)의 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`로 켜진다. 비어 있으면 다이제스트는 출력만 하고 전송하지 않는다(§8: Alert 이상만 푸시, Watch 무음).

## 진행 현황

- [x] **Sprint 0** — 스캐폴드 & 계약 고정 (독립 git, 폴더 구조, 시드 이전)
- [x] **Sprint 1** — 실데이터 수집 (네이버 JSON API 전환, 재시도·검증, `data.json` 실생성)
- [x] **Sprint 2** — 신호 엔진 분리 & 단위 테스트 (`signal_engine.js`, `node dashboard/signal_engine.test.js`)
- [x] **Sprint 3** — 대시보드 데이터 구동 (`window.DATA` 우선 + 합성 fallback, 실데이터시 배너 숨김)
- [x] **Sprint 4** — 자동화 & 텔레그램 알림 (launchd 평일 08:00 KST, Alert↑ 다이제스트 푸시)
