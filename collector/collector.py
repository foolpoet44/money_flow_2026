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

import os
import re
import json
import time
import datetime
import collections
import urllib.request
import urllib.error

# ── 설정 ─────────────────────────────────────────────
N_TRADING_DAYS = 30
FETCH_SLACK = 8    # 미확정 캔들을 버려도 30개가 남도록 넉넉히 받아온다

# 추적 유니버스는 universe.json 단일 출처에서 읽는다(리포 루트).
# 예전엔 같은 5종목이 네 파일에 각각 박혀 있어, 하나를 빠뜨리면 조용히 어긋났다.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "universe.json"), encoding="utf-8") as _fp:
    _UNIVERSE = json.load(_fp)
SECTOR_NAME = _UNIVERSE["sector"]
SECTOR = [(s["ticker"], s["name"]) for s in _UNIVERSE["stocks"]]

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


def _norm_date(v):
    """'20260805' / '2026-08-05' / '2026.08.05' → '2026-08-05'. 못 읽으면 ''."""
    s = re.sub(r"[^0-9]", "", str(v or ""))
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


# 종목 trend 응답에서 거래일을 담고 있을 법한 키 후보(네이버 버전차 흡수)
_DATE_KEYS = ("localTradedAt", "localDate", "bizdate", "tradeDate", "dt", "date")


def _row_date(row):
    """종목 trend 행에서 거래일 ISO를 뽑는다. 어떤 후보 키도 없으면 ''."""
    for k in _DATE_KEYS:
        if k in row:
            d = _norm_date(row[k])
            if d:
                return d
    return ""


# ── 장중(미확정) 캔들 가드 ───────────────────────────
# 왜 필요한가 (2026-08-07 사고 기록):
#   네이버 dayCandle/trend 는 장이 열려 있는 동안에도 '오늘' 행을 내려준다. 그 값은
#   그 시점까지의 부분 집계일 뿐인데 collector 는 이를 완결된 거래일로 취급했다.
#   스케줄러 지연으로 수집이 개장(09:00) 뒤로 밀린 날마다 장중 스냅샷이 종가로 둔갑했고
#   (외국인 순매수 최대 19.6배 차이, 2026-08-04 은 부호 반전), 그 값이 OOS 원장에
#   선착순으로 박혀 영구히 정정되지 않았다.
#   → 마감이 확정되기 전이면 '오늘' 행을 아예 계약에 들이지 않는다. 늦게 갱신될지언정
#     틀린 수치를 내보내지 않는다(§1.5 거짓 정밀도 금지).
KST = datetime.timezone(datetime.timedelta(hours=9))
SESSION_CLOSE_KST = datetime.time(15, 40)  # 정규장 마감 15:30 + 집계 확정 여유 10분


def _pending_session_date(now=None):
    """아직 확정되지 않은 거래일(ISO). 마감 확정 이후이거나 판단 불가면 None.

    비거래일에 호출돼도 무해하다 — 그 날짜를 가진 행이 애초에 없으므로 아무것도 버려지지 않는다.
    """
    now = now or datetime.datetime.now(KST)
    return now.date().isoformat() if now.time() < SESSION_CLOSE_KST else None


# ── 수집기: 지수 ─────────────────────────────────────
def index_calendar(market, pending=None):
    """지수 차트에서 거래일→종가 맵을 얻는다. 미확정 세션은 제외.

    반환: {ISO날짜: 종가}. 30일로 자르지 않는다 — 창(window) 결정은 main이 한다.
    """
    chart_url = (f"https://api.stock.naver.com/chart/domestic/index/{market}"
                 f"?periodType=dayCandle&count={N_TRADING_DAYS + 15}")
    chart = _get_json(chart_url)
    out = {}
    for r in chart["priceInfos"]:          # 과거→최신 순
        d = _iso(r["localDate"])
        if pending and d == pending:       # 진행 중인 장중 캔들은 들이지 않는다
            continue
        out[d] = round(_num(r["closePrice"]))
    return out


def index_trend(market, dates):
    """지수의 지정 거래일들에 대한 외국인·기관 순매수(억원).

    모바일 trend는 하루치만 주므로 거래일별로 루프한다(30일 이력을 주는 단일 엔드포인트 없음).
    """
    foreign, institution = [], []
    for d in dates:
        url = f"https://m.stock.naver.com/api/index/{market}/trend?bizdate={d.replace('-', '')}"
        t = _get_json(url)
        foreign.append(_signed_int(t.get("foreignValue", 0)))
        institution.append(_signed_int(t.get("institutionalValue", 0)))
        time.sleep(SLEEP)
    return foreign, institution


# ── 수집기: 종목 ─────────────────────────────────────
def stock_rows(ticker, pending=None):
    """종목 trend를 {ISO날짜: {foreign, institution, fhold}} 맵으로 반환.

    왜 '맵'인가 (2026-08-07 사고 기록):
        예전 구현은 종목 trend의 마지막 30행을 지수 날짜 배열과 '위치로' 짝지었다.
        그런데 두 피드는 서로 다른 엔드포인트라 최신 행이 같은 세션이라는 보장이 없다.
        실제로 발행본 히스토리를 되짚어 보면 종목 시계열이 지수 날짜 대비 한 세션씩
        어긋나 있었다(같은 값이 스냅샷마다 다른 날짜에 붙었다 — 67거래일 중 64일 불일치).
        계약 §4는 모든 배열이 index.*.dates에 정렬된다고 못박고 있으므로, 위치가 아니라
        '날짜'로 맞춘다. 날짜 필드가 없는 응답 버전이면 맵을 비우고 호출자가 폴백한다.

    반환: (맵, 날짜없음여부). 날짜없음이면 맵은 {} 이고 legacy 위치 정렬로 폴백해야 한다.
    """
    url = f"https://m.stock.naver.com/api/stock/{ticker}/trend?pageSize={N_TRADING_DAYS + FETCH_SLACK}"
    rows = _get_json(url)
    out = {}
    undated = 0
    for r in rows:
        d = _row_date(r)
        if not d:
            undated += 1
            continue
        if pending and d == pending:   # 진행 중인 세션은 들이지 않는다
            continue
        close_won = _num(r["closePrice"])
        # 순매수 '수량(주)' × 종가(원) ÷ 1e8 = 억원 근사
        out[d] = {
            "foreign": round(_signed_int(r.get("foreignerPureBuyQuant", 0)) * close_won / EOK),
            "institution": round(_signed_int(r.get("organPureBuyQuant", 0)) * close_won / EOK),
            "fhold": _ratio_x100(r.get("foreignerHoldRatio", "0%")),
            # 종가(원). 예전엔 억원 환산에만 쓰고 버렸다 — 그래서 대시보드가 수급은 보여주면서
            # "그래서 올랐나"에는 답하지 못했다. 이미 매일 받는 값이라 추가 호출은 없다.
            "close": round(close_won),
        }
    if not out and undated:
        return {}, True
    return out, False


def stock_rows_legacy(ticker, pending=None, index_dropped=False):
    """날짜 필드가 없는 응답 버전용 폴백 — 예전처럼 위치로 정렬한다(정확도 낮음).

    이 경로로 들어오면 종목-지수 정렬을 보장할 수 없으므로 반드시 경고를 남긴다.
    """
    url = f"https://m.stock.naver.com/api/stock/{ticker}/trend?pageSize={N_TRADING_DAYS + FETCH_SLACK}"
    rows = list(reversed(_get_json(url)))          # 최신→과거 이므로 뒤집는다
    if pending and index_dropped and rows:
        rows = rows[:-1]
    rows = rows[-N_TRADING_DAYS:]
    if len(rows) < N_TRADING_DAYS:
        raise RuntimeError(f"{ticker}: 거래일 {len(rows)}개 < {N_TRADING_DAYS}개 — pageSize 상향 필요")
    foreign, institution, fhold, close = [], [], [], []
    for r in rows:
        close_won = _num(r["closePrice"])
        foreign.append(round(_signed_int(r.get("foreignerPureBuyQuant", 0)) * close_won / EOK))
        institution.append(round(_signed_int(r.get("organPureBuyQuant", 0)) * close_won / EOK))
        fhold.append(_ratio_x100(r.get("foreignerHoldRatio", "0%")))
        close.append(round(close_won))
    return {"foreign": foreign, "institution": institution, "fhold": fhold, "close": close}


# ── 공통 창 결정 ─────────────────────────────────────
def common_window(cal, smaps, dated_tickers, n=N_TRADING_DAYS):
    """모든 피드가 공통으로 관측한 최근 n거래일. 부족하면 예외.

    계약 §4는 모든 배열이 index.*.dates 에 정렬됨을 요구한다. 어느 한 피드라도 그 날짜를
    갖고 있지 않다면 그 날은 '온전히 관측된 세션'이 아니므로 창에 넣지 않는다.
    """
    common = set(cal["KOSPI"]) & set(cal["KOSDAQ"])
    for tk in dated_tickers:
        common &= set(smaps[tk])
    window = sorted(common)[-n:]
    if len(window) < n:
        raise RuntimeError(
            f"공통 확정 거래일 {len(window)}개 < {n}개 — "
            f"조회 구간(count/pageSize)을 늘리거나 피드 상태를 확인하세요."
        )
    return window


# ── 시계열 원장 (append-only) ────────────────────────
def series_rows(data):
    """계약(data.json)을 거래일 단위 행으로 뒤집는다. (날짜, 행) 을 순서대로 내놓는다."""
    idx, stocks = data["index"], data["sector"]["stocks"]
    for i, d in enumerate(idx["KOSPI"]["dates"]):
        yield d, {
            "date": d,
            "index": {
                m: {
                    "foreign": idx[m]["foreign"][i],
                    "institution": idx[m]["institution"][i],
                    "close": idx[m]["close"][i],
                }
                for m in MARKETS
            },
            "stocks": {
                s["ticker"]: dict(
                    {"foreign": s["foreign"][i], "institution": s["institution"][i]},
                    # fhold·close 는 없을 수 있다(계약 허용/구버전) — 없으면 키 자체를 안 넣는다.
                    **({"fhold": s["fhold"][i]} if s.get("fhold") else {}),
                    **({"close": s["close"][i]} if s.get("close") else {}),
                )
                for s in stocks
            },
        }


def write_series(data, out_dir):
    """확정 거래일을 월별 JSONL 에 upsert 한다. 30일 창 밖 데이터의 유일한 보존처.

    왜 필요한가:
        data.json 은 매일 30일 창을 통째로 덮어쓴다. 창을 벗어난 수급은 git 커밋
        안에만 남아 조회가 불가능했다 — audit_ledger.js 와 replay.test.py 가
        `git show` 로 과거 발행본을 발굴하는 기괴한 코드를 쓰는 이유가 이것이다.
        git 히스토리를 데이터베이스로 쓰는 상태를 끝내고 시간축을 만든다.

    왜 오늘 하루가 아니라 창 '전체'를 매번 upsert 하는가:
        발행(push)이 실패한 날은 그날 줄이 통째로 유실된다. 다음 실행이 30일 창을
        전부 다시 써 넣으므로 결손이 스스로 메워진다 — 사람이 알아채지 않아도 낫는다.
        미확정 세션은 애초에 창에 없으므로(_pending_session_date 가드) 장중 값이
        섞여 들어올 경로는 없다.

    반환: 새로 추가된 거래일 수(기존 값 갱신은 세지 않는다).
    """
    by_month = collections.defaultdict(dict)
    for d, row in series_rows(data):
        by_month[d[:7]][d] = row          # 'YYYY-MM' 파일로 분할

    os.makedirs(out_dir, exist_ok=True)
    added = 0
    for month, rows in sorted(by_month.items()):
        path = os.path.join(out_dir, f"{month}.jsonl")
        merged = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue          # 깨진 줄은 버린다 — 재수집이 메운다
                    merged[r["date"]] = r
        before = set(merged)
        merged.update(rows)               # 확정본이 항상 이긴다
        added += len(set(merged) - before)
        with open(path, "w", encoding="utf-8") as fp:
            for d in sorted(merged):
                fp.write(json.dumps(merged[d], ensure_ascii=False) + "\n")
    return added


def read_series(out_dir):
    """원장 전체를 날짜 오름차순 리스트로 읽는다. 없으면 빈 리스트."""
    rows = {}
    if not os.path.isdir(out_dir):
        return []
    for fn in sorted(os.listdir(out_dir)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(out_dir, fn), encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows[r["date"]] = r
    return [rows[d] for d in sorted(rows)]


def write_series_js(out_dir, js_path):
    """원장 → dashboard/series.js (window.SERIES). 방식 B(§6)와 같은 배선.

    왜 JSONL 을 그대로 안 읽히나: 대시보드는 file:// 에서도 열려야 하고(§6), fetch 는
    file:// 에서 막힌다. data.js·research.js 와 똑같이 <script src> 로 넘긴다.
    indent 없이 쓴다 — 사람이 읽을 파일이 아니라 원장(JSONL)이 정본이다.
    """
    rows = read_series(out_dir)
    with open(js_path, "w", encoding="utf-8") as fp:
        fp.write("window.SERIES = " + json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + ";\n")
    return len(rows)


# ── 스키마 검증 (계약 §4 불변 규칙) ──────────────────
def _validate(data, pending=None):
    """data.json이 계약을 만족하는지 최소 검증. 실패 시 예외로 중단."""
    errs = []
    # 미확정 세션이 계약에 새어 들어왔는가 — 장중 스냅샷 재발 방지의 최종 관문.
    if pending and data["as_of"] == pending:
        errs.append(f"as_of={data['as_of']}는 아직 마감 미확정 세션 — 장중 데이터 유출")
    # 신선도 판정의 근거이므로 비어 있으면 안 된다(계약 §4).
    if not data.get("collected_at"):
        errs.append("collected_at 누락 — 대시보드가 신선도를 판정할 수 없다")
    # sessions 는 '확정 거래일 달력'이다. 창(as_of)이 그 안에 없으면 둘 중 하나가 틀렸다.
    if data.get("sessions"):
        if pending and pending in data["sessions"]:
            errs.append(f"sessions 에 미확정 세션 {pending} 포함 — 장중 데이터 유출")
        if data["as_of"] not in data["sessions"]:
            errs.append(f"as_of={data['as_of']} 가 sessions 에 없다 — 달력·창 불일치")
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
        for key in ("foreign", "institution", "close"):
            if len(s.get(key, [])) != N_TRADING_DAYS:
                errs.append(f"{s['name']}.{key} 길이 {len(s.get(key, []))} ≠ {N_TRADING_DAYS}")
        # 종가는 양수여야 한다 — 0/음수는 파싱 실패의 신호다(수익률 계산이 조용히 망가진다)
        if any(c <= 0 for c in s.get("close", [])):
            errs.append(f"{s['name']}.close 에 0 이하 값 — 종가 파싱 실패 의심")
        # fhold는 비어 있을 수 있다(계약 허용). 있으면 길이만 본다.
        if s["fhold"] and len(s["fhold"]) != N_TRADING_DAYS:
            errs.append(f"{s['name']}.fhold 길이 {len(s['fhold'])} ≠ {N_TRADING_DAYS}")
    if errs:
        raise AssertionError("스키마 검증 실패:\n  - " + "\n  - ".join(errs))


# ── 엔트리포인트 ─────────────────────────────────────
def main():
    pending = _pending_session_date()
    now_kst = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    if pending:
        print(f"· 현재 {now_kst} — 마감({SESSION_CLOSE_KST:%H:%M}) 전이므로 "
              f"{pending} 세션은 미확정으로 보고 수집에서 제외한다")
    else:
        print(f"· 현재 {now_kst} — 마감 확정 이후. 당일 세션 포함")

    # 1) 지수 달력(거래일→종가). 수급 호출은 창이 정해진 뒤에 한다 — 불필요한 호출 절약.
    print("· 지수 달력 수집 중…")
    cal = {}
    for mkt in MARKETS:
        print(f"   - {mkt}")
        cal[mkt] = index_calendar(mkt, pending)

    # 2) 종목 수급(거래일→값 맵)
    print("· 반도체 섹터 수집 중…")
    smaps, legacy = {}, {}
    for tk, nm in SECTOR:
        print(f"   - {nm} ({tk})")
        smaps[tk], legacy[tk] = stock_rows(tk, pending)
        time.sleep(SLEEP)

    # 3) 창 결정 — 모든 피드가 '공통으로' 관측한 거래일만 쓴다.
    #    계약 §4는 모든 배열이 index.*.dates 에 정렬됨을 요구한다. 한 피드라도 그 날짜를
    #    갖고 있지 않으면 그 날은 온전히 관측된 세션이 아니다 — 지어내지 않고 뒤로 물린다.
    #    (부수효과: 종목 수급이 지수보다 늦게 게시되는 날엔 as_of 가 하루 뒤로 물러난다.
    #     늦게 갱신될지언정 어긋난 값을 내보내지 않는다.)
    window = common_window(cal, smaps, [tk for tk, _ in SECTOR if not legacy[tk]])
    print(f"· 공통 확정 거래일 {len(window)}일 · {window[0]} → {window[-1]}")

    # 4) 지수 수급 — 확정된 창에 대해서만 호출
    print("· 지수 수급 수집 중…")
    index = {}
    for mkt in MARKETS:
        print(f"   - {mkt}")
        f, i = index_trend(mkt, window)
        index[mkt] = {
            "dates": window[:],
            "foreign": f,
            "institution": i,
            "close": [cal[mkt][d] for d in window],
        }

    # 5) 종목 배열을 창에 맞춰 조립 (날짜 기준 — 위치 기준 아님)
    stocks = []
    for tk, nm in SECTOR:
        if legacy[tk]:
            print(f"   ⚠ {nm}({tk}): trend 응답에 거래일 필드 없음 — 위치 기반 폴백 사용"
                  f" (지수와의 정렬 보장 불가)")
            sf = stock_rows_legacy(tk, pending, bool(pending))
        else:
            m = smaps[tk]
            sf = {
                "foreign": [m[d]["foreign"] for d in window],
                "institution": [m[d]["institution"] for d in window],
                "fhold": [m[d]["fhold"] for d in window],
                "close": [m[d]["close"] for d in window],
            }
        stocks.append({"ticker": tk, "name": nm, **sf})

    data = {
        "as_of": window[-1],
        # 수집이 실제로 돈 시각(계약 §4). as_of 만으로는 '기준일이 어제'가 정상(피드 게시 지연)인지
        # 비정상(파이프라인 중단)인지 구분할 수 없어, 대시보드가 원인을 추측해 단정하는 사고가 있었다.
        "collected_at": datetime.datetime.now(KST).isoformat(timespec="seconds"),
        # 확정 거래일 달력(창보다 넓다). 지수 차트가 곧 거래일 목록이므로 공짜로 얻는다.
        # 대시보드의 '장 상태' 표시가 이걸 보고 휴장일을 판정한다 — 예전엔 주말만 알아서
        # 공휴일에도 '장중'이라고 표시했다. 공휴일 달력 라이브러리를 들이지 않고 푼다.
        "sessions": sorted(set(cal["KOSPI"]) & set(cal["KOSDAQ"])),
        "index": index,
        "sector": {"name": SECTOR_NAME, "stocks": stocks},
    }

    _validate(data, pending)  # 계약 위반이면 여기서 중단 — 깨진 출력물을 내보내지 않는다

    # 출력 경로는 collector.py 위치 기준으로 잡아 CWD에 흔들리지 않게 한다.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)                              # 리포 루트
    json_path = os.path.join(root, "data.json")              # 계약 정본(디버깅·방식 A용)
    js_path = os.path.join(root, "dashboard", "data.js")     # 대시보드 입력(방식 B)

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(json_path, "w", encoding="utf-8") as fp:
        fp.write(payload)
    # 방식 B(§6): 대시보드가 <script src="data.js">로 바로 읽는다. file://에서도 동작.
    with open(js_path, "w", encoding="utf-8") as fp:
        fp.write("window.DATA = " + payload + ";\n")
    print(f"✓ data.json + dashboard/data.js 생성 완료 · 기준일 {data['as_of']} · 거래일 {N_TRADING_DAYS}개")

    # 시계열 원장 — docs/ 아래에 두어 publish.sh 의 `git add docs` 가 그대로 영속화한다.
    # (스냅샷이 아니라 누적물이라 dashboard/ → docs/ 복사 경로를 타지 않는다)
    series_dir = os.path.join(root, "docs", "series")
    added = write_series(data, series_dir)
    n = write_series_js(series_dir, os.path.join(root, "dashboard", "series.js"))
    print(f"✓ 시계열 원장 갱신 · docs/series/ · 신규 거래일 {added}일 · 누적 {n}일 → dashboard/series.js")


if __name__ == "__main__":
    main()
