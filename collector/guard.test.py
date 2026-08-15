"""
자금 조류 · 장중 캔들 가드 테스트 (collector/guard.test.py)
────────────────────────────────────────────────────────
왜 이 테스트가 있는가 (2026-08-07 사고 기록):
    스케줄러 지연으로 수집이 개장(09:00) 뒤로 밀린 날, collector 는 '진행 중인 장중
    캔들'을 완결된 거래일로 오인해 수집했다. 실측 결과 외국인 순매수가 최대 19.6배
    차이났고 2026-08-04 은 부호까지 반전됐다. 그 값이 OOS 원장에 선착순으로 박혀
    영구히 정정되지 않았다.

    cron 을 장 마감 후로 옮긴 것이 1차 방어, 이 가드가 2차 방어다. 스케줄러 지터가
    다시 커지거나 누군가 수동으로 장중에 돌려도 틀린 수치가 계약에 새어들지 않는다.
    네트워크 없이 순수 로직만 검증하므로 CI/샌드박스에서도 항상 돌 수 있다.

실행:  python3 collector/guard.test.py    (run_daily.sh 가 수집 전에 호출)
"""

import os
import sys
import datetime
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("collector", os.path.join(HERE, "collector.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  ✗ {label} — got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")


def at(h, m):
    """2026-08-07(금) 특정 KST 시각."""
    return datetime.datetime(2026, 8, 7, h, m, tzinfo=C.KST)


print("· 미확정 세션 판정 (마감 15:30 + 확정 여유 10분 = 15:40 경계)")
check("08:55 개장 전 → 오늘은 미확정", C._pending_session_date(at(8, 55)), "2026-08-07")
check("09:10 장중 → 오늘은 미확정", C._pending_session_date(at(9, 10)), "2026-08-07")
check("15:29 마감 직전 → 미확정", C._pending_session_date(at(15, 29)), "2026-08-07")
check("15:39 경계 직전 → 미확정", C._pending_session_date(at(15, 39)), "2026-08-07")
check("15:40 경계 → 확정", C._pending_session_date(at(15, 40)), None)
check("16:05 실제 수집 시각 → 확정", C._pending_session_date(at(16, 5)), None)
check("23:59 → 확정", C._pending_session_date(at(23, 59)), None)

print("· 거래일 문자열 정규화 (네이버 버전차 흡수)")
check("YYYYMMDD", C._norm_date("20260805"), "2026-08-05")
check("ISO", C._norm_date("2026-08-05"), "2026-08-05")
check("점 구분", C._norm_date("2026.08.05"), "2026-08-05")
check("빈 값", C._norm_date(""), "")
check("None", C._norm_date(None), "")
check("길이 불일치는 거부", C._norm_date("202608"), "")

print("· 종목 trend 행에서 거래일 추출 (후보 키 탐색)")
check("localTradedAt", C._row_date({"localTradedAt": "2026-08-05"}), "2026-08-05")
check("localDate", C._row_date({"localDate": "20260805"}), "2026-08-05")
check("bizdate", C._row_date({"bizdate": "20260805"}), "2026-08-05")
check("날짜 키 없음 → 빈 문자열(폴백 유도)", C._row_date({"closePrice": "1"}), "")

print("· 지수 피드에서 미확정 캔들 제거")
rows = [{"localDate": f"2026080{i}"} for i in range(1, 7)]  # 08-01 ~ 08-06
kept = [r for r in rows if C._iso(r["localDate"]) != "2026-08-06"]
check("미확정 08-06 제거", [r["localDate"] for r in kept],
      ["20260801", "20260802", "20260803", "20260804", "20260805"])
kept_none = [r for r in rows if C._iso(r["localDate"]) != None]  # noqa: E711 — pending=None 상황
check("확정 시각에는 아무것도 안 버림", len(kept_none), 6)

print("· 공통 창 결정 — 종목·지수 날짜 정렬 (위치 정렬 사고 재발 방지)")
DAYS = [f"2026-07-{d:02d}" for d in range(1, 12)]  # 11일치 (테스트용 n=5)
cal = {"KOSPI": {d: 1 for d in DAYS}, "KOSDAQ": {d: 1 for d in DAYS}}
smaps = {"A": {d: {} for d in DAYS}, "B": {d: {} for d in DAYS}}
check("모든 피드가 같으면 최근 n일", C.common_window(cal, smaps, ["A", "B"], 5), DAYS[-5:])

# 종목 피드가 지수보다 한 세션 늦게 게시된 상황 — 예전 구현이 조용히 어긋났던 바로 그 케이스
lagged = {"A": {d: {} for d in DAYS[:-1]}, "B": {d: {} for d in DAYS}}
check("한 종목이 최신일 누락 → 창이 하루 뒤로 물러남",
      C.common_window(cal, lagged, ["A", "B"], 5), DAYS[-6:-1])
check("그 결과 as_of 도 하루 전", C.common_window(cal, lagged, ["A", "B"], 5)[-1], DAYS[-2])

# 지수 두 시장의 달력이 어긋나도 교집합만 쓴다
cal2 = {"KOSPI": {d: 1 for d in DAYS}, "KOSDAQ": {d: 1 for d in DAYS[:-1]}}
check("시장 간 달력 불일치도 교집합", C.common_window(cal2, smaps, ["A"], 5), DAYS[-6:-1])

# 날짜 필드가 없어 legacy 폴백인 종목은 교집합 계산에서 빠진다
check("legacy 종목은 교집합에서 제외", C.common_window(cal, lagged, ["B"], 5), DAYS[-5:])

try:
    C.common_window(cal, smaps, ["A", "B"], 50)
    fails.append("창 부족인데 통과시켰다")
    print("  ✗ 창이 부족한데 예외를 던지지 않았다")
except RuntimeError:
    print("  ✓ 창이 부족하면 예외로 중단(깨진 계약 미발행)")

print("· 계약 검증이 미확정 as_of 를 차단하는가 (최종 관문)")
skeleton = {
    "as_of": "2026-08-07",
    "collected_at": "2026-08-07T20:04:11+09:00",
    "index": {m: {"dates": [], "foreign": [], "institution": [], "close": []} for m in C.MARKETS},
    "sector": {"stocks": []},
}
try:
    C._validate(skeleton, "2026-08-07")
    fails.append("_validate 가 미확정 as_of 를 통과시켰다")
    print("  ✗ _validate 가 미확정 as_of 를 통과시켰다")
except AssertionError as e:
    leaked = [ln for ln in str(e).splitlines() if "미확정" in ln]
    check("미확정 as_of 차단", bool(leaked), True)

print("· 계약이 collected_at 을 요구하는가 (신선도 판정의 근거 · §4)")
no_ts = dict(skeleton, as_of="2026-08-06")
no_ts.pop("collected_at")
try:
    C._validate(no_ts, None)
    fails.append("_validate 가 collected_at 누락을 통과시켰다")
    print("  ✗ collected_at 이 없는데 통과시켰다")
except AssertionError as e:
    check("collected_at 누락 차단", any("collected_at" in ln for ln in str(e).splitlines()), True)

print("· 시계열 원장 upsert (30일 창 밖 데이터의 유일한 보존처)")
import json          # noqa: E402 — 이 섹션에서만 쓴다
import shutil        # noqa: E402
import tempfile      # noqa: E402


def _mini(dates, kospi_foreign, samsung_foreign):
    """계약 모양의 최소 데이터. 길이 검증은 여기서 하지 않는다(write_series 만 본다)."""
    n = len(dates)
    return {
        "index": {
            m: {"dates": dates, "foreign": kospi_foreign,
                "institution": [0] * n, "close": [2700] * n}
            for m in C.MARKETS
        },
        "sector": {"stocks": [{"ticker": "005930", "foreign": samsung_foreign,
                               "institution": [0] * n, "fhold": [5310] * n}]},
    }


tmp = tempfile.mkdtemp()
try:
    d1 = ["2026-07-30", "2026-07-31", "2026-08-03"]      # 월 경계를 걸친다
    added = C.write_series(_mini(d1, [10, 20, 30], [1, 2, 3]), tmp)
    check("첫 적재는 3거래일 신규", added, 3)
    check("월별로 파일이 갈린다", sorted(os.listdir(tmp)), ["2026-07.jsonl", "2026-08.jsonl"])

    def rows(month):
        with open(os.path.join(tmp, f"{month}.jsonl"), encoding="utf-8") as fp:
            return [json.loads(ln) for ln in fp if ln.strip()]

    check("7월 파일에 2거래일", [r["date"] for r in rows("2026-07")], d1[:2])
    check("값이 날짜별로 실린다", rows("2026-07")[0]["index"]["KOSPI"]["foreign"], 10)
    check("종목은 티커 키로", rows("2026-07")[0]["stocks"]["005930"]["foreign"], 1)

    # 같은 실행을 두 번 — 멱등이어야 한다(발행 재시도·수동 재실행 안전)
    check("재실행은 신규 0건(멱등)", C.write_series(_mini(d1, [10, 20, 30], [1, 2, 3]), tmp), 0)
    check("줄 수도 그대로", len(rows("2026-07")), 2)

    # 창이 하루 전진 — 겹치는 날은 확정본으로 갱신되고, 새 날만 신규로 센다
    d2 = ["2026-07-31", "2026-08-03", "2026-08-04"]
    check("창 전진 시 신규는 1건", C.write_series(_mini(d2, [99, 30, 40], [9, 3, 4]), tmp), 1)
    aug = rows("2026-08")
    check("8월이 날짜 오름차순", [r["date"] for r in aug], ["2026-08-03", "2026-08-04"])
    check("겹친 날은 최신 확정값으로 갱신", rows("2026-07")[1]["index"]["KOSPI"]["foreign"], 99)

    # fhold 는 계약상 비어 있을 수 있다 — 그때는 키를 넣지 않는다
    empty_fhold = _mini(["2026-08-05"], [1], [1])
    empty_fhold["sector"]["stocks"][0]["fhold"] = []
    C.write_series(empty_fhold, tmp)
    check("fhold 빈 배열이면 키 없음",
          "fhold" in rows("2026-08")[-1]["stocks"]["005930"], False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fails:
    print(f"✗ 가드 테스트 실패 {len(fails)}건")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("✓ 장중 캔들 가드 테스트 통과")
