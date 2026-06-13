"""
자금 조류 · Money Flow Intelligence — 수집기 (collector)
────────────────────────────────────────────────────────
네이버 금융 JSON API로 KOSPI/KOSDAQ 지수 수급 + 반도체 섹터 종목별 수급을 긁어
대시보드(dashboard/index.html)가 읽는 data.json 을 생성한다.

왜 네이버인가 (설계 결정 기록):
    원래 설계는 pykrx(KRX 스크래핑)였다. 그러나 KRX 데이터포털이 로그인 기반으로
    잠기면서 투자자 수급·지수 엔드포인트가 KRX_ID/KRX_PW 자격증명을 요구하게 됐다.
    네이버 금융 모바일/증권 JSON API는 익명으로 같은 데이터를 주므로 이쪽으로 전환했다.
    부수효과로 외부 의존성이 사라졌다 — 표준 라이브러리(urllib, json)만 쓴다.

데이터 소스 지도:
    · 지수 종가 시계열  : api.stock.naver.com/chart/domestic/index/{KOSPI|KOSDAQ}
    · 지수 투자자 수급  : m.stock.naver.com/api/index/{market}/trend?bizdate=YYYYMMDD (일별 루프)
    · 종목 수급+보유율  : m.stock.naver.com/api/stock/{code}/trend?pageSize=30

단위 (data.json 계약 §4 준수):
    · foreign/institution = 억원 (1억 KRW), 정수
        - 지수: 네이버 trend의 foreignValue/institutionalValue가 이미 억원 단위
        - 종목: 네이버는 순매수 '수량(주)'을 주므로 수량 × 종가 ÷ 1e8 로 억원 근사
                (정확한 거래대금은 VWAP 기준이나, z-score 신호엔 부호·상대규모가 핵심이라 근사로 충분)
    · fhold = 외인보유율 × 100 정수 (47.63% → 4763)
    · 모든 배열 길이 30, 날짜 오름차순(과거→최신)

실행:  python collector/collector.py   →  ./data.json
"""

import json
import time
import urllib.request
import urllib.error

# ── 설정 ─────────────────────────────────────────────
N_TRADING_DAYS = 30
SECTOR_NAME = "반도체"
SECTOR = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("009150", "삼성전기"),
    ("402340", "SK스퀘어"),
    ("042700", "한미반도체"),
]
MARKETS = ["KOSPI", "KOSDAQ"]

EOK = 1e8  # 억원 환산 (원 → 억원)

# 네이버는 브라우저가 아닌 요청을 막으므로 UA/Referer를 흉내낸다.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Referer": "https://m.stock.naver.com/",
}

RETRY = 3          # 호출 실패 시 재시도 횟수
SLEEP = 0.25       # 호출 간 간격(초). 네이버 차단·레이트리밋 회피용


# ── HTTP 유틸 ────────────────────────────────────────
def _get_json(url):
    """네이버 JSON API 호출. 실패 시 RETRY회 재시도, 그래도 실패하면 예외 전파."""
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            # 점증 백오프: 일시적 차단/혼잡을 흡수
            time.sleep(SLEEP * attempt * 2)
    raise RuntimeError(f"네이버 호출 실패({RETRY}회): {url}\n  마지막 오류: {last_err!r}")


# ── 파싱 유틸 (네이버 문자열 → 숫자) ──────────────────
def _signed_int(s):
    """'+22,041' / '-14,640' → 22041 / -14640. 콤마·부호 제거."""
    s = str(s).replace(",", "").replace("+", "").strip()
    return int(float(s)) if s and s not in ("-", "") else 0

def _num(s):
    """'8,123.62' → 8123.62. 콤마 제거 후 실수."""
    return float(str(s).replace(",", "").strip())

def _ratio_x100(s):
    """'47.63%' → 4763. 보유율을 ×100 정수로(계약 §4의 fhold 규약)."""
    s = str(s).replace("%", "").replace(",", "").strip()
    return round(float(s) * 100) if s else 0

def _iso(yyyymmdd):
    """'20260612' → '2026-06-12'."""
    s = str(yyyymmdd)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


# ── 수집기: 지수 ─────────────────────────────────────
def index_flow(market):
    """지수(KOSPI/KOSDAQ)의 30거래일 종가 + 일별 외국인·기관 순매수(억원)."""
    # 1) 종가 시계열 — 차트 API 한 번으로 거래일·종가 확보 (여유분 count로 요청)
    chart_url = (f"https://api.stock.naver.com/chart/domestic/index/{market}"
                 f"?periodType=dayCandle&count={N_TRADING_DAYS + 15}")
    chart = _get_json(chart_url)
    # priceInfos는 과거→최신 순. 최신 30거래일만 취한다.
    rows = chart["priceInfos"][-N_TRADING_DAYS:]
    dates_yyyymmdd = [r["localDate"] for r in rows]
    dates = [_iso(d) for d in dates_yyyymmdd]
    close = [round(_num(r["closePrice"])) for r in rows]

    # 2) 투자자 수급 — 모바일 trend는 하루치만 주므로 거래일별로 루프
    #    (지수 전체 수급의 30일 이력을 주는 단일 엔드포인트가 없어 부득이 일별 호출)
    foreign, institution = [], []
    for ymd in dates_yyyymmdd:
        url = f"https://m.stock.naver.com/api/index/{market}/trend?bizdate={ymd}"
        t = _get_json(url)
        foreign.append(_signed_int(t.get("foreignValue", 0)))
        institution.append(_signed_int(t.get("institutionalValue", 0)))
        time.sleep(SLEEP)

    return {"dates": dates, "foreign": foreign, "institution": institution, "close": close}


# ── 수집기: 종목 ─────────────────────────────────────
def stock_flow(ticker):
    """종목의 30거래일 외국인·기관 순매수(억원) + 외인보유율(×100)."""
    url = f"https://m.stock.naver.com/api/stock/{ticker}/trend?pageSize={N_TRADING_DAYS}"
    rows = _get_json(url)
    # 응답은 최신→과거 순. 계약은 과거→최신 오름차순이므로 뒤집는다.
    rows = list(reversed(rows))[-N_TRADING_DAYS:]

    foreign, institution, fhold = [], [], []
    for r in rows:
        close_won = _num(r["closePrice"])
        # 순매수 '수량(주)' × 종가(원) ÷ 1e8 = 억원 근사
        f_qty = _signed_int(r.get("foreignerPureBuyQuant", 0))
        i_qty = _signed_int(r.get("organPureBuyQuant", 0))
        foreign.append(round(f_qty * close_won / EOK))
        institution.append(round(i_qty * close_won / EOK))
        fhold.append(_ratio_x100(r.get("foreignerHoldRatio", "0%")))

    return {"foreign": foreign, "institution": institution, "fhold": fhold}


# ── 스키마 검증 (계약 §4 불변 규칙) ──────────────────
def _validate(data):
    """data.json이 계약을 만족하는지 최소 검증. 실패 시 예외로 중단."""
    errs = []
    for mkt in MARKETS:
        d = data["index"][mkt]
        for key in ("dates", "foreign", "institution", "close"):
            if len(d[key]) != N_TRADING_DAYS:
                errs.append(f"index.{mkt}.{key} 길이 {len(d[key])} ≠ {N_TRADING_DAYS}")
        if d["dates"] != sorted(d["dates"]):
            errs.append(f"index.{mkt}.dates 오름차순 아님")
    stocks = data["sector"]["stocks"]
    if len(stocks) != len(SECTOR):
        errs.append(f"sector.stocks 수 {len(stocks)} ≠ {len(SECTOR)}")
    for s in stocks:
        for key in ("foreign", "institution"):
            if len(s[key]) != N_TRADING_DAYS:
                errs.append(f"{s['name']}.{key} 길이 {len(s[key])} ≠ {N_TRADING_DAYS}")
        # fhold는 비어 있을 수 있다(계약 허용). 있으면 길이만 본다.
        if s["fhold"] and len(s["fhold"]) != N_TRADING_DAYS:
            errs.append(f"{s['name']}.fhold 길이 {len(s['fhold'])} ≠ {N_TRADING_DAYS}")
    if errs:
        raise AssertionError("스키마 검증 실패:\n  - " + "\n  - ".join(errs))


# ── 엔트리포인트 ─────────────────────────────────────
def main():
    print("· 지수 수급 수집 중…")
    index = {}
    for mkt in MARKETS:
        print(f"   - {mkt}")
        index[mkt] = index_flow(mkt)

    print("· 반도체 섹터 수집 중…")
    stocks = []
    for tk, nm in SECTOR:
        print(f"   - {nm} ({tk})")
        sf = stock_flow(tk)
        stocks.append({"ticker": tk, "name": nm, **sf})
        time.sleep(SLEEP)

    data = {
        "as_of": index["KOSPI"]["dates"][-1],
        "index": index,
        "sector": {"name": SECTOR_NAME, "stocks": stocks},
    }

    _validate(data)  # 계약 위반이면 여기서 중단 — 깨진 data.json을 내보내지 않는다

    with open("data.json", "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    print(f"✓ data.json 생성 완료 · 기준일 {data['as_of']} · 거래일 {N_TRADING_DAYS}개")


if __name__ == "__main__":
    main()
