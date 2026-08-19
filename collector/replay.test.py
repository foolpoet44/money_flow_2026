"""
자금 조류 · 데일리 사이클 리플레이 테스트 (collector/replay.test.py)
────────────────────────────────────────────────────────────────
네트워크 없이 collector 를 '실제로' 한 사이클 돌린다. 네이버 HTTP 계층만 대체하고
파싱·가드·창 결정·계약 검증은 전부 프로덕션 코드가 그대로 탄다.

픽스처는 지어낸 값이 아니라 git 히스토리의 실제 수급이다 — docs/data.js 스냅샷 39개에서
'그 세션 마감 이후에 관측된 값'만 골라 다수결로 확정 시계열을 복원해 쓴다. 즉 진짜 데이터로
진짜 코드를 돌리되, 네트워크 의존만 걷어낸 검증이다.

왜 필요한가: guard.test.py 는 순수 함수 단위를 못박지만, "수집기 전체가 장중에 돌면
무슨 일이 벌어지는가"는 단위 테스트로 표현되지 않는다. 2026-08-07 사고가 정확히 그
틈에서 났다 — 개별 함수는 다 정상이었고 조합과 실행 시각이 문제였다.

검증 시나리오:
  A. 마감 후(16:05) 실행        → 당일 세션 포함
  B. 장중(09:10) 실행           → 당일 세션 제외 (사고 재현)
  C. 종목 피드 1세션 지연        → 창이 물러나고 계약 길이 유지
  D. 날짜 정렬                  → 지수·종목 값이 날짜별 원본과 일치 (위치 정렬 사고 재발 여부)

실행:  python3 collector/replay.test.py     (git 히스토리 필요 · 네트워크 불필요)
"""

import os
import sys
import json
import datetime
import subprocess
import importlib.util
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLOSE_MIN = 15 * 60 + 40  # 15:40 KST — 이 이후 관측이면 그 세션은 확정


# ── 1. git 히스토리 → 확정 시계열 픽스처 ─────────────────
def load_fixture():
    # encoding 을 명시하지 않으면 로케일 인코딩으로 디코드한다. 발행본은 한글이 섞인
    # UTF-8 이라 cp949 로케일(한국어 Windows)에서 UnicodeDecodeError 로 죽는다.
    # ubuntu 러너가 UTF-8 이라 CI 에서만 우연히 통과하던 자리다.
    git = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
    ).stdout

    log = git("log", "--reverse", "--format=%H %cI", "--", "docs/data.js").strip().split("\n")

    obs_i = collections.defaultdict(lambda: collections.defaultdict(list))
    obs_s = collections.defaultdict(lambda: collections.defaultdict(list))
    for line in log:
        if not line.strip():
            continue
        sha, iso = line.split()
        raw = git("show", f"{sha}:docs/data.js")
        if not raw or "{" not in raw:
            continue
        d = json.loads(raw[raw.index("{"):].strip().rstrip(";"))
        t = datetime.datetime.fromisoformat(iso) + datetime.timedelta(hours=9)
        od, om = t.date().isoformat(), t.hour * 60 + t.minute
        confirmed = lambda dt: od > dt or (od == dt and om >= CLOSE_MIN)  # noqa: E731

        dates = d["index"]["KOSPI"]["dates"]
        for mkt in ("KOSPI", "KOSDAQ"):
            k = d["index"][mkt]
            for i, dt in enumerate(k["dates"]):
                if confirmed(dt):
                    obs_i[mkt][dt].append((k["foreign"][i], k["institution"][i], k["close"][i]))
        for s in d["sector"]["stocks"]:
            for i, dt in enumerate(dates):
                if confirmed(dt):
                    obs_s[s["ticker"]][dt].append(
                        (s["foreign"][i], s["institution"][i],
                         s["fhold"][i] if s["fhold"] else 0))

    # 관측이 갈리면 다수결 — 2026-06-14 시드의 어긋난 정렬을 자동 regime 이 이긴다.
    pick = lambda v: collections.Counter(v).most_common(1)[0][0]  # noqa: E731
    idx = {m: {dt: pick(v) for dt, v in obs_i[m].items()} for m in obs_i}
    stk = {tk: {dt: pick(v) for dt, v in obs_s[tk].items()} for tk in obs_s}
    days = sorted(set(idx["KOSPI"]) & set(idx["KOSDAQ"]))
    for tk in stk:
        days = [d for d in days if d in stk[tk]]
    return idx, stk, days


# 테스트 시작 시점의 산출물을 '내용째' 기억해 둔다.
#   파일 존재 여부만 기억하면 부족하다 — 리플레이는 collector.main() 을 실제로 돌리므로
#   기존 파일을 픽스처 출력으로 덮어쓴다. 그러면 지우는 것보다 나쁘다: 진짜처럼 보이는
#   가짜가 남고, 대시보드가 그걸 실수급으로 렌더한다(§1.5).
_OUTPUTS = ("data.json", os.path.join("dashboard", "data.js"))
_PRE_CONTENT = {}
for _f in _OUTPUTS:
    _p = os.path.join(ROOT, _f)
    if os.path.exists(_p):
        with open(_p, "rb") as _fp:
            _PRE_CONTENT[_f] = _fp.read()

IDX, STK, ALL_DAYS = load_fixture()
if len(ALL_DAYS) < 32:
    print(f"✗ 픽스처 거래일 {len(ALL_DAYS)}일 — 리플레이에 부족합니다(32일 이상 필요)")
    sys.exit(1)
print(f"· 픽스처: 확정 거래일 {len(ALL_DAYS)}일 ({ALL_DAYS[0]} → {ALL_DAYS[-1]}) · 종목 {len(STK)}개")

# ── 2. 네이버 응답 재현 ──────────────────────────────────
CFG = {"settled": [], "today": None, "stock_lag": 0}
FIXED_CLOSE = 70000  # 종목 순매수 수량 역산용 기준가(계약상 억원만 맞으면 된다)


def _index_days():
    return CFG["settled"] + ([CFG["today"]] if CFG["today"] else [])


def _stock_days():
    ds = _index_days()
    return ds[: -CFG["stock_lag"]] if CFG["stock_lag"] else ds


def fake_get_json(url):
    if "/chart/domestic/index/" in url:
        mkt = "KOSPI" if "KOSPI" in url else "KOSDAQ"
        return {"priceInfos": [
            {"localDate": d.replace("-", ""), "closePrice": f"{IDX[mkt][d][2]:,}"}
            for d in _index_days()
        ]}
    if "/api/index/" in url:
        mkt = "KOSPI" if "KOSPI" in url else "KOSDAQ"
        ymd = url.split("bizdate=")[1]
        d = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        f, i, _ = IDX[mkt].get(d, (0, 0, 0))
        return {"foreignValue": f"{f:+,}", "institutionalValue": f"{i:+,}"}
    if "/api/stock/" in url:
        tk = url.split("/api/stock/")[1].split("/")[0]
        rows = []
        for d in _stock_days():
            f_eok, i_eok, fh = STK[tk][d]
            rows.append({
                "localTradedAt": d,
                "closePrice": f"{FIXED_CLOSE:,}",
                "foreignerPureBuyQuant": f"{round(f_eok * 1e8 / FIXED_CLOSE):+,}",
                "organPureBuyQuant": f"{round(i_eok * 1e8 / FIXED_CLOSE):+,}",
                "foreignerHoldRatio": f"{fh / 100:.2f}%",
            })
        return list(reversed(rows))  # 네이버는 최신→과거 순
    raise RuntimeError("알 수 없는 URL: " + url)


# ── 3. collector 로드 후 HTTP 계층만 교체 ────────────────
spec = importlib.util.spec_from_file_location("collector", os.path.join(HERE, "collector.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
C._get_json = fake_get_json
C.SLEEP = 0

# 픽스처는 발행본 히스토리에서 복원한다 — 그 시점 유니버스에 있던 종목만 들어 있다.
# 유니버스를 넓히면(2026-08-19: 5 → 10종목) 새 종목은 과거 발행본에 없으므로,
# 이 테스트는 '픽스처가 실제로 가진 종목'으로만 사이클을 돌린다.
# 이 테스트가 검증하는 건 유니버스 크기가 아니라 사이클 역학(장중 가드·날짜 정렬)이다.
_universe_size = len(C.SECTOR)
C.SECTOR = [t for t in C.SECTOR if t[0] in set(STK)]
if len(C.SECTOR) < 2:
    print(f"✗ 픽스처와 겹치는 종목 {len(C.SECTOR)}개 — 리플레이 불가")
    sys.exit(1)
print(f"· 픽스처 보유 {len(C.SECTOR)}종목으로 리플레이 (현재 유니버스 {_universe_size}종목)")
# 시계열 원장(docs/series/)에는 절대 쓰지 않는다. 리플레이는 창을 인위적으로 흔들어
# 돌리므로(장중·피드지연 시나리오) 그 산출물이 누적 원장에 섞이면 테스트가 증거를
# 오염시킨다. upsert 로직 자체는 guard.test.py 가 임시 디렉터리에서 못박는다.
C.write_series = lambda *a, **k: 0
_real_pending = C._pending_session_date

fails = []


def expect(label, got, want):
    ok = got == want
    print(f"   {'✓' if ok else '✗'} {label}: {got}" + ("" if ok else f"   (기대 {want})"))
    if not ok:
        fails.append(label)


def run(label, hh, mm, settled, today=None, stock_lag=0):
    CFG.update(settled=settled, today=today, stock_lag=stock_lag)
    # 모의 시계의 날짜는 시뮬레이션상의 '오늘'과 반드시 같아야 한다.
    day = today or settled[-1]
    y, mo, dd = map(int, day.split("-"))
    fixed = datetime.datetime(y, mo, dd, hh, mm, tzinfo=C.KST)
    C._pending_session_date = lambda now=None: _real_pending(fixed)
    print(f"\n▶ {label}  ({hh:02d}:{mm:02d} KST)")
    try:
        C.main()
    finally:
        C._pending_session_date = _real_pending
    with open(os.path.join(ROOT, "data.json"), encoding="utf-8") as fp:
        return json.load(fp)


TODAY = ALL_DAYS[-1]
PAST = ALL_DAYS[:-1]

d = run("A. 마감 후 — 당일 세션이 확정됐으므로 포함", 16, 5, PAST, today=TODAY)
expect("as_of 는 당일", d["as_of"], TODAY)
expect("거래일 30", len(d["index"]["KOSPI"]["dates"]), 30)

d = run("B. 장중 — 예전이라면 장중 캔들을 종가로 수집했을 시나리오", 9, 10, PAST, today=TODAY)
expect("as_of 는 전 거래일", d["as_of"], PAST[-1])
expect("당일이 창에 없음", TODAY not in d["index"]["KOSPI"]["dates"], True)
expect("거래일 30", len(d["index"]["KOSPI"]["dates"]), 30)

d = run("C. 마감 후 + 종목 피드 1세션 지연 — 창이 물러나야", 16, 5, PAST, today=TODAY, stock_lag=1)
expect("as_of 가 하루 물러남", d["as_of"], PAST[-1])
expect("거래일 30", len(d["index"]["KOSPI"]["dates"]), 30)

d = run("D. 날짜 정렬 — 지수·종목이 같은 날짜에 같은 값인가", 16, 5, ALL_DAYS)
w = d["index"]["KOSPI"]["dates"]
bad_s = [f"{s['ticker']} {dt}" for s in d["sector"]["stocks"]
         for i, dt in enumerate(w) if STK[s["ticker"]][dt][0] != s["foreign"][i]]
bad_i = [dt for i, dt in enumerate(w) if IDX["KOSPI"][dt][0] != d["index"]["KOSPI"]["foreign"][i]]
expect("종목 foreign 이 날짜별 원본과 일치(불일치 건수)", len(bad_s), 0)
expect("지수 foreign 이 날짜별 원본과 일치(불일치 건수)", len(bad_i), 0)
expect("KOSPI·KOSDAQ 날짜 동일", d["index"]["KOSPI"]["dates"] == d["index"]["KOSDAQ"]["dates"], True)

# 리플레이 산출물은 실수집이 아니므로 남기지 않는다 — 원장·발행본을 오염시키지 않기 위함.
#
# 단 '자기가 만든 것만' 치운다. 예전엔 무조건 지워서, 로컬에서 실수집을 돌려 둔 뒤
# 이 테스트를 돌리면 진짜 data.json 이 조용히 사라졌다. 파이프라인은 리플레이를 수집보다
# 먼저 돌리므로 안 물리지만, 손으로 쓰는 순서까지 그렇지는 않다.
for f in _OUTPUTS:
    p = os.path.join(ROOT, f)
    if f in _PRE_CONTENT:
        with open(p, "wb") as fp:      # 원래 내용으로 되돌린다
            fp.write(_PRE_CONTENT[f])
    else:
        try:
            os.remove(p)               # 이 테스트가 만든 것만 지운다
        except OSError:
            pass
if _PRE_CONTENT:
    print(f"· 기존 수집 산출물 {len(_PRE_CONTENT)}개를 원래 내용으로 복원했습니다.")

print()
if fails:
    print(f"✗ 리플레이 실패 {len(fails)}건: {fails}")
    sys.exit(1)
print("✓ 데일리 사이클 리플레이 전 시나리오 통과 (산출물은 정리됨)")
