"""
자금 조류 · Money Flow Intelligence — 수집기 (collector)
────────────────────────────────────────────────────────
pykrx로 KOSPI/KOSDAQ 지수 수급 + 반도체 섹터 종목별 수급을 긁어
대시보드(money-flow-dashboard.html)가 읽는 data.json 을 생성한다.

· API 키 불필요 (pykrx는 KRX/네이버 스크래핑)
· 일 단위 배치용. tmux cron 또는 GCP Cloud Scheduler에 얹으면 됨.
· 금액 단위: 억원 (1억 KRW), 외인지분율: pct*100 정수

설치:  pip install pykrx
실행:  python collector.py
출력:  ./data.json  (대시보드와 같은 폴더에 두면 됨)

주의: pykrx 버전에 따라 반환 컬럼명(한글)이 미세하게 다를 수 있다.
     컬럼이 안 맞으면 print(df.columns) 로 확인 후 COLS 상수를 조정할 것.
"""

import json
import datetime as dt
from pykrx import stock

# ── 설정 ─────────────────────────────────────────────
LOOKBACK_DAYS = 45          # 영업일 30개 확보용 여유
N_TRADING_DAYS = 30
SECTOR_NAME = "반도체"
SECTOR = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("009150", "삼성전기"),
    ("402340", "SK스퀘어"),
    ("042700", "한미반도체"),
]
# 순매수 거래대금 컬럼 후보 (버전별 차이 흡수)
FOREIGN_COLS = ["외국인", "외국인합계"]
INSTI_COLS   = ["기관합계", "기관"]

EOK = 1e8  # 억원 환산


def _today():
    return dt.datetime.now().strftime("%Y%m%d")

def _from(days):
    return (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y%m%d")

def _pick(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(f"컬럼 없음. 후보={candidates} / 실제={list(df.columns)}")


def index_flow(market):
    """지수 레벨 투자자별 일일 순매수(억원) + 종가."""
    f, t = _from(LOOKBACK_DAYS), _today()

    # 투자자별 순매수 거래대금 (시장 전체)
    val = stock.get_market_trading_value_by_date(f, t, market)
    fcol, icol = _pick(val, FOREIGN_COLS), _pick(val, INSTI_COLS)

    # 지수 종가 (KOSPI=1001, KOSDAQ=2001)
    idx_code = {"KOSPI": "1001", "KOSDAQ": "2001"}[market]
    ohlcv = stock.get_index_ohlcv_by_date(f, t, idx_code)

    val = val.tail(N_TRADING_DAYS)
    ohlcv = ohlcv.tail(N_TRADING_DAYS)

    dates = [d.strftime("%Y-%m-%d") for d in val.index]
    return {
        "dates": dates,
        "foreign":     [round(v / EOK) for v in val[fcol]],
        "institution": [round(v / EOK) for v in val[icol]],
        "close":       [round(c) for c in ohlcv["종가"]],
    }


def stock_flow(ticker):
    """종목별 외국인/기관 순매수(억원) + 외인지분율(pct*100)."""
    f, t = _from(LOOKBACK_DAYS), _today()

    val = stock.get_market_trading_value_by_date(f, t, ticker)
    fcol, icol = _pick(val, FOREIGN_COLS), _pick(val, INSTI_COLS)

    # 외국인 보유 지분율
    try:
        ex = stock.get_exhaustion_rates_of_foreign_investment_by_date(f, t, ticker)
        hold_col = "지분율" if "지분율" in ex.columns else ex.columns[-1]
        ex = ex.tail(N_TRADING_DAYS)
        fhold = [round(float(x) * 100) for x in ex[hold_col]]
    except Exception:
        fhold = []

    val = val.tail(N_TRADING_DAYS)
    return {
        "foreign":     [round(v / EOK) for v in val[fcol]],
        "institution": [round(v / EOK) for v in val[icol]],
        "fhold":       fhold,
    }


def main():
    print("· 지수 수급 수집 중…")
    kospi = index_flow("KOSPI")
    kosdaq = index_flow("KOSDAQ")

    print("· 반도체 섹터 수집 중…")
    stocks = []
    for tk, nm in SECTOR:
        print(f"   - {nm} ({tk})")
        sf = stock_flow(tk)
        stocks.append({"ticker": tk, "name": nm, **sf})

    data = {
        "as_of": kospi["dates"][-1],
        "index": {"KOSPI": kospi, "KOSDAQ": kosdaq},
        "sector": {"name": SECTOR_NAME, "stocks": stocks},
    }

    with open("data.json", "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    print(f"✓ data.json 생성 완료 · 기준일 {data['as_of']}")


if __name__ == "__main__":
    main()
