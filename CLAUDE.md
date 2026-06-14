# 자금 조류 · Money Flow Intelligence — 개발문서 (CLAUDE.md)

> Claude Code 실행 컨텍스트 문서. 이 파일을 리포지토리 루트에 두고 작업 세션의 기준 문서로 삼는다.
> 두 개의 시드 파일(`money-flow-dashboard.html`, `collector.py`)을 출발점으로, 정적 MVP → 실데이터 연동 → 자동화의 순서로 빌드한다.

---

## 0. 한 문단 요약

이 프로젝트는 **한국 주식시장의 자금 흐름(외국인·기관 수급)을 일 단위로 읽어 행동 가능한 신호로 바꾸는 모니터링 시스템**이다. 추적 대상은 KOSPI/KOSDAQ 지수 수급과 반도체 섹터(삼성전자·SK하이닉스·삼성전기·SK스퀘어·한미반도체) 종목별 수급. 데이터는 pykrx(API 키 불필요)로 긁고, 신호는 각 지표 자신의 시계열 분포 기준 z-score로 등급화하며, 대시보드가 본체이고 텔레그램 알림은 후속 단계다. 설계 골격은 EX Intelligence의 `Signal Generation → Verification → Actionable Intelligence`를 자본 도메인으로 번역한 것이다 — 조직의 약신호를 읽는 문법과 시장의 약신호를 읽는 문법은 동형(isomorphic)이라는 전제 위에 서 있다.

---

## 1. 설계 원칙 (변경 시 합의 필요)

1. **단일 지표를 믿지 않는다.** 모든 신호는 교차 확인(confluence)을 거친다. 외국인 단독 순매도는 노이즈일 수 있다. 외국인+기관+지수종가가 정렬되면 신호다.
2. **판정은 횡단면이 아니라 시계열 분포 기준이다.** "남들 대비"가 아니라 "그 지표 자신의 30거래일 역사 대비" 얼마나 극단인가로 본다. (ergodicity: 흐름의 비정상성은 그 자산 자신의 시간 경로에 대해서만 정의된다.)
3. **실시간을 흉내내지 않는다.** 일 1회 장 마감 후 배치로 충분하다. 틱/장중 인프라는 의도적으로 배제한다.
4. **데이터 계약이 인터페이스다.** collector와 dashboard는 `data.json` 스키마(§4)로만 결합한다. 한쪽을 바꿔도 스키마가 유지되면 다른 쪽은 영향받지 않는다.
5. **합성 데이터는 항상 명시한다.** 실데이터가 아닐 때 UI에 배너로 표기한다. 거짓 정밀도를 만들지 않는다.

---

## 2. 시스템 아키텍처

```
┌─ Layer 1 · Signal Generation ──────────────────────────┐
│  collector.py (pykrx)                                   │
│  · 지수 수급 (KOSPI/KOSDAQ 외국인·기관 순매수, 종가)      │
│  · 섹터 종목 수급 (외국인·기관 순매수, 외인지분율)        │
│  → data.json                                            │
└─────────────────────────────────────────────────────────┘
                         ↓ (data.json 스키마)
┌─ Layer 2 · Confluence Verification ────────────────────┐
│  signal_engine (현재 dashboard 내장 JS, 추후 분리)       │
│  · zlast(): 각 시계열 자신의 분포 기준 z-score           │
│  · confluence(): 외국인·기관·종가 방향 정렬 카운트        │
│  · 교차검증 로그 (지수 vs 섹터 정렬 여부)                 │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─ Layer 3 · Actionable Intelligence ────────────────────┐
│  dashboard (money-flow-dashboard.html)                  │
│  · 오늘의 조류 게이지 + 신뢰등급(A~C)                    │
│  · Watch / Alert / Critical 신호 카드                   │
│  · 지수 수급 차트 + 섹터 종목 그리드                     │
│  [후속] 텔레그램 다이제스트 (Alert 이상)                 │
└─────────────────────────────────────────────────────────┘
```

3-Layer 매핑: **Experience**(원천 수급 데이터) → **Semantic**(z-score·confluence로 흐름에 의미 부여) → **Intelligence**(자산배분 함의·알림).

---

## 3. 리포지토리 구조 (목표)

```
money-flow/
├── CLAUDE.md                 # 이 문서
├── README.md                 # 실행 빠른 시작
├── collector/
│   ├── collector.py          # [시드] pykrx 수집기
│   ├── requirements.txt       # pykrx, pandas
│   └── data.json             # 수집 출력 (gitignore)
├── dashboard/
│   ├── index.html            # [시드] money-flow-dashboard.html 이전
│   ├── data.js               # 생성물: window.DATA = {...} (gitignore)
│   └── signal_engine.js      # 신호 로직 분리 (Sprint 2)
├── scripts/
│   └── run_daily.sh          # 수집→배포 1회 실행 (tmux cron 진입점)
└── .gitignore                # data.json, data.js, __pycache__
```

> **시드 파일 이전:** `money-flow-dashboard.html` → `dashboard/index.html`, `collector.py` → `collector/collector.py`. 이전 후 시드의 인라인 `DATA`는 §6의 데이터 로딩 방식으로 대체한다.

---

## 4. 데이터 계약 (THE CONTRACT — 함부로 바꾸지 말 것)

`collector.py`의 출력이자 `dashboard`의 입력. 금액 단위는 **억원**(1억 KRW), 모든 배열 길이는 30(거래일), `index.*.dates`에 정렬된다.

```jsonc
{
  "as_of": "2026-06-12",                  // 기준일 (가장 최근 거래일)
  "index": {
    "KOSPI": {
      "dates":       ["2026-05-02", ...],  // length 30, ISO date, 오름차순
      "foreign":     [-380, 210, ...],     // 외국인 일일 순매수 (억원, 정수)
      "institution": [150, -90, ...],      // 기관합계 일일 순매수 (억원, 정수)
      "close":       [2710, 2698, ...]     // 지수 종가 (정수)
    },
    "KOSDAQ": { /* 동일 구조 */ }
  },
  "sector": {
    "name": "반도체",
    "stocks": [
      {
        "ticker":      "005930",
        "name":        "삼성전자",
        "foreign":     [-380, ...],        // length 30, 억원
        "institution": [210, ...],         // length 30, 억원
        "fhold":       [5310, 5308, ...]   // 외인지분율 × 100 (정수). 5310 = 53.10%
      }
      // ... 5종목
    ]
  }
}
```

**불변 규칙**

- 배열 길이는 30으로 통일. 거래일 부족 시 collector가 앞을 자른다(`tail(30)`).
- `foreign`/`institution` 부호: 양수=순매수(유입), 음수=순매도(유출).
- `fhold`는 정수(`pct × 100`). 대시보드에서 `Δfhold/100`으로 %p 환산.
- `fhold`가 비어 있을 수 있다(`[]`). 대시보드는 빈 배열을 graceful하게 처리해야 한다.

---

## 5. 신호 엔진 명세 (재구현 가능 수준)

> 현재 dashboard JS에 내장. Sprint 2에서 `signal_engine.js`로 분리하고, 추후 TS로 포팅 시 아래 명세를 정본으로 삼는다.

### 5.1 z-score (시계열 자기참조)

```
stats(arr):                       # population mean/sd
    m  = mean(arr)
    sd = sqrt(mean((x - m)^2))  or 1 if 0

zlast(arr):                       # 오늘값이 자기 과거 분포에서 얼마나 극단인가
    m, sd = stats(arr[:-1])       # 오늘(마지막)을 제외한 trailing window
    return (arr[-1] - m) / sd
```

### 5.2 confluence (교차 확인)

```
confluence(foreign, institution, closeChange):
    c = 1                                   # 기본 1조류
    if sign(foreign[-1]) == sign(institution[-1]) != 0: c += 1   # 외국인·기관 정렬
    if sign(foreign[-1]) == sign(closeChange) != 0:      c += 1   # 수급·가격 정렬 (지수 한정)
    return c                                 # 1 ~ 3
```

### 5.3 등급 판정

```
tier(absZ, confluence):
    if absZ >= 2.0 or confluence >= 3: return CRITICAL
    if absZ >= 1.2 or confluence >= 2: return ALERT
    return WATCH

grade:  CRITICAL → "A" (3 독립신호 정렬, 노이즈 최소)
        ALERT    → "B" (2 신호 정렬, 방향성 신뢰 양호)
        WATCH    → "C" (단일 약신호, 관찰 단계)
```

### 5.4 신호 생성 규칙

- **지수 신호**: KOSPI/KOSDAQ 각각에 대해 `zf=zlast(foreign)`, `zi=zlast(institution)`, `closeChange=close[-1]-close[-2]`로 confluence·tier 산출. `absZ = max(|zf|,|zi|)`.
- **종목 신호**: 각 종목의 `|zlast(foreign)| >= 1.2`일 때만 생성(노이즈 컷). confluence는 외국인·기관 정렬만(가격 미반영, 최대 2).
- **정렬**: tier 우선(CRITICAL→ALERT→WATCH), 동급 내 absZ 내림차순. 헤로는 1위, 그리드는 상위 3건.
- **임계치 상수**는 한 곳에서 관리(`Z_ALERT=1.2`, `Z_CRITICAL=2.0`, `STOCK_MIN_Z=1.2`). 튜닝 대상.

### 5.5 교차검증 로그 (Verification)

오늘 KOSPI 지수 순매수 부호와 반도체 섹터 외국인 합산 부호가 **정렬되면** 신뢰 가산, **갈리면** 등급을 한 단계 보수적으로 해석한다는 서술을 노출한다.

---

## 6. 데이터 로딩 방식 (Sprint 3 결정 사항)

시드 대시보드는 `DATA`를 인라인 보유. 실데이터 전환 시 둘 중 택1:

- **방식 B (권장·무서버):** collector가 `data.js`도 출력 → `<script src="data.js"></script>` (내용: `window.DATA = {...}`). dashboard는 `window.DATA ?? FALLBACK_SYNTHETIC` 순으로 사용. `file://`에서 바로 열림.
- **방식 A (서버):** `python -m http.server`로 폴더 서빙 후 dashboard가 `fetch('data.json')`. 프로덕션/Next.js 이행 시 자연스러움.

MVP는 **B**로 간다. 합성 데이터는 `FALLBACK_SYNTHETIC`로 남겨 두고, `window.DATA` 존재 시 배너를 숨긴다.

---

## 7. 데이터 소스 레퍼런스 (pykrx)

| 목적                 | 함수                                                                   | 비고                                      |
| -------------------- | ---------------------------------------------------------------------- | ----------------------------------------- |
| 시장 투자자별 순매수 | `get_market_trading_value_by_date(from, to, "KOSPI")`                  | 컬럼명 버전별 상이 → §collector COLS 조정 |
| 종목 투자자별 순매수 | `get_market_trading_value_by_date(from, to, ticker)`                   | 단위 원 → `/1e8` 억원                     |
| 지수 종가            | `get_index_ohlcv_by_date(from, to, "1001")`                            | KOSPI=1001, KOSDAQ=2001                   |
| 외인지분율           | `get_exhaustion_rates_of_foreign_investment_by_date(from, to, ticker)` | `지분율` 컬럼, 실패 시 `[]`               |

**알려진 함정**

- pykrx는 스크래핑이라 호출이 느리다. 종목당 sleep 권장, 실패 재시도 래핑.
- 반환 컬럼명이 한글이고 버전마다 미세하게 다르다. `_pick()`로 후보 매칭하되 첫 실행 시 `print(df.columns)` 검증.
- 휴장일·신규상장 종목은 길이가 30 미만일 수 있다 → 패딩 또는 가용분만 사용 결정 필요(현재는 `tail`).

---

## 8. Sprint 계획 (각 Sprint는 독립 PR, 수용 기준 충족 시 종료)

### Sprint 0 — 스캐폴드 & 계약 고정

- [ ] §3 디렉터리 구조 생성, 시드 2파일 이전
- [ ] `requirements.txt`(pykrx, pandas), `.gitignore`, `README.md`
- [ ] `data.json` 스키마(§4)를 주석/타입으로 코드에 박제
- **수용 기준:** 합성 데이터로 dashboard가 그대로 렌더된다(기능 회귀 없음).

### Sprint 1 — 실데이터 수집 경화

- [ ] collector 컬럼 매칭 검증(실제 pykrx 버전 기준), 재시도·sleep 추가
- [ ] `data.json` 실 생성 1회 성공, 30거래일 정렬 확인
- [ ] 휴장일/길이부족 엣지 케이스 처리
- **수용 기준:** `python collector/collector.py` → 유효한 `data.json` 생성, 스키마 검증 통과.

### Sprint 2 — 신호 엔진 분리 & 테스트

- [ ] `signal_engine.js`로 §5 로직 추출(순수 함수)
- [ ] 단위 테스트: 알려진 입력 → 예상 z/tier/confluence (최소 6 케이스: 각 tier × 정렬/역행)
- [ ] 임계치 상수 단일 출처화
- **수용 기준:** 테스트 그린, dashboard가 분리된 엔진을 import해 동일 출력.

### Sprint 3 — 대시보드 데이터 구동

- [ ] §6 방식 B 배선, `window.DATA` 우선 + 합성 fallback
- [ ] 실데이터 배너 토글(실데이터 시 숨김), `fhold` 빈 배열 graceful
- **수용 기준:** collector 출력 교체만으로 화면이 실수급으로 전환된다.

### Sprint 4 — 자동화 & 알림 (대시보드 안정화 후)

- [ ] `scripts/run_daily.sh`: 수집→`data.js` 배포 1회 실행
- [ ] tmux cron(또는 GCP Cloud Scheduler) 등록, 한국시간 아침 1회
- [ ] hermes-telegram에 `money_flow_digest` intent 추가 — Alert 이상만 푸시, Watch는 무음
- **수용 기준:** 무인 1일 사이클이 돌고, Critical 발생 시 텔레그램 수신.

### 프로덕션 트랙 (별도, 검증 후)

- Supabase: 시계열 테이블 + 신호 등급 테이블(일별 적재, 히스토리 축적)
- Next.js/TS 포팅: 신호 엔진 TS화(§5 명세 정본), 대시보드 컴포넌트화
- ETF 자금흐름 프록시·환율(ECOS)·글로벌 유동성(FRED) 조류 확장

---

## 9. Claude Code 실행 가이드 (가드레일)

- **빌드·실행 전 항상 확인받는다.** 백그라운드 자동 빌드 금지. 각 Sprint 착수 전 계획을 제시하고 승인 후 진행.
- **데이터 계약(§4)·설계 원칙(§1)은 임의 변경 금지.** 변경 필요 시 먼저 제안하고 합의.
- **한 Sprint = 한 PR.** 수용 기준을 만족하지 못하면 다음으로 넘어가지 않는다.
- **합성/실데이터 경계를 흐리지 않는다.** 테스트용 더미를 실데이터처럼 표기하지 않는다.
- pykrx 실행은 네트워크 의존이므로, CI/샌드박스에서 실패하면 합성 데이터로 폴백하되 그 사실을 로그로 남긴다.
- 한글 컬럼·라벨 인코딩(UTF-8) 유지.

---

## 10. 빠른 시작 (현재 상태)

```bash
# 수집기 (로컬, 네트워크 필요)
pip install pykrx pandas
python collector/collector.py          # → data.json

# 대시보드 (현재는 합성 데이터 내장)
open dashboard/index.html              # 브라우저로 바로 열기
```

> 현재 시드 대시보드는 합성 데이터로 동작한다. 실데이터 전환은 Sprint 3 완료 후.

---

## 11. 리서치 다이제스트 (별도 모듈 · 추가 기능)

수급 대시보드가 "무엇이 움직였나"라면, 리서치 다이제스트는 "왜"를 읽을 단서를 붙인다. 증권사 리포트(종목분석·산업분석·시황)를 자동으로 읽어 데일리 요약 + 원본 링크를 제공한다. 기존 데이터 계약(§4)·신호 엔진(§5)과 독립이며, 별도 수집기·별도 데이터 파일·대시보드 하단 별도 섹션으로 결합한다.

### 11.1 설계 원칙

- **요약은 추출형이다.** AI로 지어내지 않는다(§1.5 거짓 정밀도 금지). 리포트 자신의 제목(애널리스트 핵심 논지), 상세 페이지 리드 문단, 목표가·투자의견을 '있는 그대로' 가져온다. 목표가·투자의견은 엄격 패턴으로 확실할 때만 채우고, 불확실하면 비운다(fail closed).
- **표준 라이브러리만.** `urllib`·`re`·`json`·`html`. 외부 키·LLM 의존 없음.
- **money flow와 연결.** 추적 반도체 종목(005930 등) 또는 반도체 키워드 리포트를 `sector` 플래그로 우선 노출한다.

### 11.2 데이터 소스 (네이버 금융 리서치 · EUC-KR HTML)

| 카테고리 | URL                               | 행 구조                                                 |
| -------- | --------------------------------- | ------------------------------------------------------- |
| 종목분석 | `research/company_list.naver`     | 종목·제목·증권사·작성일·조회수·상세(`company_read`)·PDF |
| 산업분석 | `research/industry_list.naver`    | 분야·제목·증권사·작성일·조회수·상세·PDF                 |
| 시황정보 | `research/market_info_list.naver` | 제목·증권사·작성일·조회수·상세·PDF                      |

PDF 원본은 `stock.pstatic.net/stock-research/...` 직링크. 상세 리드 요약·목표가는 `*_read.naver?nid=` 페이지에서 추출.

### 11.3 데이터 계약 (research.json)

```jsonc
{
  "as_of": "2026-06-12", // 데일리 = 가장 최근 작성일의 리포트만
  "count": 47,
  "sector_count": 2,
  "reports": [
    {
      "category": "company", // company | industry | market
      "category_label": "종목", // 종목 | 산업 | 시황
      "title": "…", // 제목 = 요약 헤드라인
      "stock": "삼성전자",
      "ticker": "005930", // company만, 없으면 ""
      "broker": "…증권",
      "date": "2026-06-12",
      "views": 3945,
      "summary": "…", // 상세 리드 문단(추출형). 없으면 ""
      "target": "",
      "opinion": "", // 확실할 때만
      "url": "…company_read…",
      "pdf": "…/….pdf",
      "sector": true, // 반도체 연관 우선 노출 플래그
    },
  ],
}
```

방식 B(§6) 동일: `dashboard/research.js`(`window.RESEARCH = {...}`)도 출력하고, 대시보드는 있으면 섹션을 띄우고 없으면 숨긴다(graceful).

### 11.4 향후 확장 (옵션)

- 추출형을 넘어선 **LLM 요약**: Alert 이상 신호 종목의 리포트만 Claude API로 3줄 다이제스트(별도 키·비용 합의 필요).
- Sprint 4 자동화에 합류: 수급 collector와 함께 데일리 배치, 텔레그램 다이제스트에 핵심 리포트 링크 첨부.
