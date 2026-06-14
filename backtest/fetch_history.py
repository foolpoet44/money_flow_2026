"""
자금 조류 · 백테스트용 장기 이력 수집 (backtest/fetch_history.py)
────────────────────────────────────────────────────────
5개 추적 종목의 가용 최대 이력(네이버 trend, ~90거래일)을 긁어
backtest/history.json 을 만든다. 백테스트(backtest.js)가 이 파일을 시점불변으로
재생해 신호의 예측력을 측정한다.

왜 별도 수집인가: 일일 collector는 30거래일만 모은다. 엣지 측정에는 가능한 한
긴 이력이 필요하다. 네이버 trend의 pageSize 상한(~90)까지 받는다.

단위는 collector.py와 동일하게 맞춘다(억원 = 순매수수량 × 종가 ÷ 1e8).
신호 엔진이 보는 시계열과 '같은 숫자'를 재생해야 백테스트가 정직하다.

실행:  python backtest/fetch_history.py   →  backtest/history.json
"""

import os
import json
import time
import urllib.request
import urllib.error

SECTOR = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("009150", "삼성전기"),
    ("402340", "SK스퀘어"),
    ("042700", "한미반도체"),
]
PAGE_SIZE = 60          # 네이버 trend 실측 상한(70부터 HTTP 400). ~3개월치가 천장.
EOK = 1e8
RETRY = 3
SLEEP = 0.25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Referer": "https://m.stock.naver.com/",
}


def _get_json(url):
    last = None
    for attempt in range(1, RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last = e
            time.sleep(SLEEP * attempt * 2)
    raise RuntimeError(f"네이버 호출 실패({RETRY}회): {url}\n  {last!r}")


def _signed_int(s):
    s = str(s).replace(",", "").replace("+", "").strip()
    return int(float(s)) if s and s not in ("-", "") else 0


def _num(s):
    return float(str(s).replace(",", "").strip())


def _iso(yyyymmdd):
    s = str(yyyymmdd)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def stock_history(ticker):
    url = f"https://m.stock.naver.com/api/stock/{ticker}/trend?pageSize={PAGE_SIZE}"
    rows = list(reversed(_get_json(url)))  # 최신→과거 응답을 과거→최신으로
    dates, foreign, institution, close = [], [], [], []
    for r in rows:
        c = _num(r["closePrice"])
        dates.append(_iso(r["bizdate"]))
        close.append(round(c))
        # collector.py와 동일: 순매수 수량 × 종가 ÷ 1e8 = 억원
        foreign.append(round(_signed_int(r.get("foreignerPureBuyQuant", 0)) * c / EOK))
        institution.append(round(_signed_int(r.get("organPureBuyQuant", 0)) * c / EOK))
    return {"dates": dates, "foreign": foreign, "institution": institution, "close": close}


def main():
    print(f"· 장기 이력 수집 중 (목표 {PAGE_SIZE}거래일)…")
    stocks = []
    for tk, nm in SECTOR:
        h = stock_history(tk)
        print(f"   - {nm}({tk}): {len(h['dates'])}거래일  {h['dates'][0]}~{h['dates'][-1]}")
        stocks.append({"ticker": tk, "name": nm, **h})
        time.sleep(SLEEP)

    data = {"generated_for": stocks[0]["dates"][-1], "stocks": stocks}
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "history.json"), "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    print(f"✓ backtest/history.json 생성 · {len(stocks)}종목")


if __name__ == "__main__":
    main()
