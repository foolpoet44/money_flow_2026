"""
자금 조류 · 시계열 원장 과거 백필 (collector/backfill_series.py)
────────────────────────────────────────────────────────────────
docs/series/ 는 오늘부터 쌓인다. 그런데 이 리포에는 이미 발행본(docs/data.js) 스냅샷이
수십 개 커밋돼 있고, 그 안에 과거 수급이 들어 있다. 그걸 되짚어 원장을 과거로 늘린다.

무엇을 백필하고 무엇을 하지 않는가 (2026-08-16 결정):
    · 지수(KOSPI/KOSDAQ) 수급·종가 — 백필한다.
    · 종목 시계열 — 2026-08-07 이전은 **백필하지 않는다.**
      그 구간은 종목 trend 를 지수 날짜와 '위치로' 짝지어 한 세션씩 어긋나 있었고
      (67거래일 중 64일 불일치), 오프셋이 상수가 아니라 복원 불가로 판정해 OOS 원장에서
      격리한 바로 그 데이터다(backtest/audit_ledger.js). 격리해 놓고 다른 파일에 같은
      값을 되살리면, 같은 데이터가 다른 문으로 들어와 증거 행세를 한다.
      2026-08-07 이후 종목분은 어차피 다음 실수집이 창(30일)에 담아 채운다.

정직성 장치 두 가지:
    ① 마감 확정 관측만 쓴다. 스냅샷 커밋 시각(KST)이 그 거래일 마감(15:40) 이후일 때만
       그 행을 신뢰한다 — 장중에 수집된 스냅샷은 부분 집계이므로 버린다(2026-08-07 사고).
    ② 관측이 갈리면 다수결. 같은 (시장, 날짜)를 여러 스냅샷이 서로 다르게 적어 뒀다면
       최빈값을 쓴다. 갈린 조합은 리포트로 보여 준다.

기존 행은 절대 건드리지 않는다. 실수집이 쓴 행이 언제나 이긴다 — 이 스크립트는
'빠진 날짜만' 채운다. 그래서 몇 번을 돌려도 안전하다(멱등).

실행:  python collector/backfill_series.py            진단만 (파일 변경 없음)
       python collector/backfill_series.py --write    실제 기입
"""

import os
import sys
import json
import datetime
import subprocess
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERIES_DIR = os.path.join(ROOT, "docs", "series")
CLOSE_MIN = 15 * 60 + 40      # 15:40 KST — 이 이후 관측이면 그 세션은 확정
STOCK_TRUST_FROM = "2026-08-07"   # 이 날짜 '이후'의 종목분만 신뢰 가능(그 이전은 격리 구간)
WRITE = "--write" in sys.argv


def git(*args):
    """UTF-8 명시 — 발행본은 한글이 섞여 있어 로케일 디코드에 맡기면 깨진다."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout


def snapshots():
    """docs/data.js 의 모든 커밋을 (관측시각KST, 파싱된 계약) 으로 내놓는다."""
    for line in git("log", "--reverse", "--format=%H %cI", "--", "docs/data.js").strip().split("\n"):
        if not line.strip():
            continue
        sha, iso = line.split()
        raw = git("show", f"{sha}:docs/data.js")
        if not raw or "{" not in raw:
            continue
        try:
            data = json.loads(raw[raw.index("{"):].strip().rstrip(";").strip())
        except ValueError:
            continue          # 포맷이 다른 스냅샷은 조용히 건너뛴다
        kst = datetime.datetime.fromisoformat(iso) + datetime.timedelta(hours=9)
        yield kst, data


def collect():
    """확정 관측만 모아 (시장,날짜) → [관측값...] 을 만든다."""
    obs = collections.defaultdict(lambda: collections.defaultdict(list))
    n_snap = n_drop = 0
    for kst, data in snapshots():
        n_snap += 1
        od, om = kst.date().isoformat(), kst.hour * 60 + kst.minute
        for mkt in ("KOSPI", "KOSDAQ"):
            d = data.get("index", {}).get(mkt)
            if not d:
                continue
            for i, dt in enumerate(d["dates"]):
                # 마감 확정 이후 관측인가 — 장중 스냅샷은 부분 집계라 버린다
                if not (od > dt or (od == dt and om >= CLOSE_MIN)):
                    n_drop += 1
                    continue
                obs[mkt][dt].append((d["foreign"][i], d["institution"][i], d["close"][i]))
    return obs, n_snap, n_drop


def existing_dates():
    """이미 원장에 있는 거래일. 이 날짜는 건드리지 않는다."""
    have = set()
    if not os.path.isdir(SERIES_DIR):
        return have
    for fn in os.listdir(SERIES_DIR):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(SERIES_DIR, fn), encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    have.add(json.loads(line)["date"])
                except ValueError:
                    continue
    return have


def main():
    obs, n_snap, n_drop = collect()
    if not obs:
        print("✗ docs/data.js 스냅샷에서 지수 수급을 한 건도 읽지 못했습니다.")
        sys.exit(1)

    # 두 시장 모두 확정 관측이 있는 날짜만 — 한쪽만 있는 날은 반쪽짜리 행이 된다
    days = sorted(set(obs["KOSPI"]) & set(obs["KOSDAQ"]))
    have = existing_dates()
    todo = [d for d in days if d not in have]

    split = 0
    rows = {}
    for d in todo:
        row = {"date": d, "index": {}, "stocks": {},
               # 출처 표식 — 실수집이 아니라 발행본 스냅샷에서 복원했음을 계약에 남긴다.
               # 종목이 비어 있는 이유도 이 표식으로 설명된다(§4.1).
               "source": "backfill:docs/data.js"}
        for mkt in ("KOSPI", "KOSDAQ"):
            counts = collections.Counter(obs[mkt][d])
            if len(counts) > 1:
                split += 1
            f, i, c = counts.most_common(1)[0][0]      # 갈리면 다수결
            row["index"][mkt] = {"foreign": f, "institution": i, "close": c}
        rows[d] = row

    print("════════════════════════════════════════════════════")
    print(" 시계열 원장 과거 백필 · 지수 전용")
    print("════════════════════════════════════════════════════")
    print(f"발행본 스냅샷      : {n_snap}개")
    print(f"장중 관측 폐기     : {n_drop}행 (마감 {CLOSE_MIN // 60}:{CLOSE_MIN % 60:02d} 이전 관측)")
    print(f"확정 거래일        : {len(days)}일  {days[0]} → {days[-1]}")
    print(f"이미 원장에 있음   : {len(days) - len(todo)}일 (건드리지 않음)")
    print(f"백필 대상          : {len(todo)}일")
    print(f"관측이 갈린 조합   : {split}건 (다수결로 확정)")
    print(f"종목 시계열        : 백필하지 않음 — {STOCK_TRUST_FROM} 이전은 격리 구간이고,")
    print(f"                     이후분은 다음 실수집이 30일 창에 담아 채운다")

    if not todo:
        print("\n· 채울 날짜가 없습니다.")
        return
    if not WRITE:
        print("\n· 진단만 수행했습니다. 기입하려면 --write 를 붙이세요.")
        return

    os.makedirs(SERIES_DIR, exist_ok=True)
    by_month = collections.defaultdict(dict)
    for d, row in rows.items():
        by_month[d[:7]][d] = row

    added = 0
    for month, new_rows in sorted(by_month.items()):
        path = os.path.join(SERIES_DIR, f"{month}.jsonl")
        merged = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if line:
                        try:
                            r = json.loads(line)
                            merged[r["date"]] = r
                        except ValueError:
                            continue
        for d, row in new_rows.items():
            if d in merged:        # 기존 행은 언제나 이긴다
                continue
            merged[d] = row
            added += 1
        with open(path, "w", encoding="utf-8") as fp:
            for d in sorted(merged):
                fp.write(json.dumps(merged[d], ensure_ascii=False) + "\n")

    print(f"\n✓ {added}거래일 기입 · {SERIES_DIR}")

    # 대시보드 입력(방식 B)도 함께 갱신 — collector 의 생성기를 그대로 쓴다(두 벌 금지)
    import importlib.util
    spec = importlib.util.spec_from_file_location("collector", os.path.join(HERE, "collector.py"))
    C = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(C)
    n = C.write_series_js(SERIES_DIR, os.path.join(ROOT, "dashboard", "series.js"))
    print(f"✓ dashboard/series.js 갱신 · 누적 {n}거래일")
    print("· 실수집이 같은 날짜를 다시 쓰면 그 값이 이 행을 대체한다(확정본 우선).")


if __name__ == "__main__":
    main()
