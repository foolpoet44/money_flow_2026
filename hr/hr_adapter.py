"""
조직 조류 · HR 실데이터 어댑터 (hr/hr_adapter.py)
────────────────────────────────────────────────────────
현실적인 원천(HRIS·근태·협업로그·펄스서베이의 CSV 내보내기)을 받아
팀 단위로 집계해 hr_data 계약(hr_synth.py와 동일 스키마)을 만든다.

요점: 합성 생성기(hr_synth.py) 자리에 이 어댑터를 끼우기만 하면 엔진·대시보드는
그대로 동작한다 — money flow가 collector만 갈아끼웠던 것과 동형. 데이터 계약이
인터페이스다.

윤리(코드로 강제):
  · k-익명성: 팀 인원이 MIN_TEAM_SIZE 미만이면 집계에서 제외(개인 식별 위험).
  · 출력에 person_id를 남기지 않는다 — 집계 후 개인 데이터는 버린다.
  · 개인 단위 점수·랭킹을 만들지 않는다.

입력 CSV 스키마 (hr/sample_data/ 에 샘플 동봉):
  roster.csv     : person_id, team
  overtime.csv   : person_id, week, overtime_hours
  collab.csv     : person_id, week, collab_score   (0~100)
  enps.csv       : person_id, week, enps           (-100~100)
  headcount.csv  : week, net                        (주간 순증감 +입사 -퇴사)

실행:
  python hr/hr_adapter.py --make-sample   # hr_data.json(synth)로부터 샘플 CSV 생성
  python hr/hr_adapter.py                 # 샘플 CSV → hr_data.json + hr/hr_data.js
"""

import os
import csv
import sys
import json
from collections import defaultdict

N = 12
MIN_TEAM_SIZE = 5  # k-익명성 임계: 이 미만 팀은 집계 제외
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(HERE, "sample_data")

AS_OF = "2026-W24"

INDICATORS = {
    "overtime": ("overtime.csv", "overtime_hours"),
    "collab": ("collab.csv", "collab_score"),
    "enps": ("enps.csv", "enps"),
}


# ── 로딩 ─────────────────────────────────────────────
def load_roster(path):
    """person_id → team, team → [members]."""
    person_team = {}
    members = defaultdict(list)
    with open(path, encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            person_team[row["person_id"]] = row["team"]
            members[row["team"]].append(row["person_id"])
    return person_team, members


def load_indicator(path, value_col):
    """(person_id, week) → value."""
    out = {}
    with open(path, encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            out[(row["person_id"], row["week"])] = float(row[value_col])
    return out


# ── 집계 (어댑터 본체) ───────────────────────────────
def aggregate():
    person_team, members = load_roster(os.path.join(SAMPLE, "roster.csv"))

    # 주차 목록은 overtime.csv에서 정렬 추출
    weeks = []
    seen = set()
    with open(os.path.join(SAMPLE, "overtime.csv"), encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            w = row["week"]
            if w not in seen:
                seen.add(w)
                weeks.append(w)
    weeks = sorted(weeks)[-N:]

    ind_data = {
        key: load_indicator(os.path.join(SAMPLE, fname), col)
        for key, (fname, col) in INDICATORS.items()
    }

    # k-익명성: 인원 미달 팀 제외
    included, excluded = [], []
    for team, mem in members.items():
        (included if len(mem) >= MIN_TEAM_SIZE else excluded).append(team)
    if excluded:
        print(f"⚠ k-익명성: 인원<{MIN_TEAM_SIZE} 팀 제외 → {', '.join(excluded)}")

    def team_week_mean(key, team, week):
        vals = [
            ind_data[key][(p, week)]
            for p in members[team]
            if (p, week) in ind_data[key]
        ]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    teams = []
    for team in sorted(included):
        teams.append({
            "id": team,
            "name": team,
            "size": len(members[team]),
            "overtime": [team_week_mean("overtime", team, w) for w in weeks],
            "collab": [team_week_mean("collab", team, w) for w in weeks],
            "enps": [team_week_mean("enps", team, w) for w in weeks],
        })

    # 조직 tide (hr_synth와 동일 공식 — 포함 팀 평균 기준)
    pressure, engagement = [], []
    for wi in range(len(weeks)):
        ot = sum(t["overtime"][wi] for t in teams) / len(teams)
        col = sum(t["collab"][wi] for t in teams) / len(teams)
        en = sum(t["enps"][wi] for t in teams) / len(teams)
        pressure.append(round(ot + (100 - col) + (50 - en) / 2, 1))
        engagement.append(round((col + (en + 100) / 2) / 2, 1))

    # 순증감
    headcount_net = [0] * len(weeks)
    hc_path = os.path.join(SAMPLE, "headcount.csv")
    if os.path.exists(hc_path):
        wmap = {w: i for i, w in enumerate(weeks)}
        with open(hc_path, encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                if row["week"] in wmap:
                    headcount_net[wmap[row["week"]]] = int(float(row["net"]))

    return {
        "as_of": AS_OF,
        "weeks": weeks,
        "org": {
            "attrition_pressure": pressure,
            "engagement": engagement,
            "headcount_net": headcount_net,
        },
        "teams": teams,
        "_meta": {
            "synthetic": False,
            "source": "csv_adapter",
            "unit": "team",
            "k_anonymity_min": MIN_TEAM_SIZE,
            "note": "팀 단위 집계 · 개인 식별자 미보존",
        },
    }


def write_contract(data):
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(os.path.join(ROOT, "hr_data.json"), "w", encoding="utf-8") as fp:
        fp.write(payload)
    with open(os.path.join(HERE, "hr_data.js"), "w", encoding="utf-8") as fp:
        fp.write("window.HR_DATA = " + payload + ";\n")


# ── 샘플 CSV 생성 (왕복 검증용) ──────────────────────
# hr_synth.py의 팀-주 시계열을 '개인 단위'로 역집계한다. 멤버 오프셋의 합을 0으로
# 맞춰 다시 평균하면 팀 값이 정확히 복원된다(어댑터 정합성 검증).
def make_sample():
    synth_path = os.path.join(ROOT, "hr_data.json")
    if not os.path.exists(synth_path):
        sys.exit("hr_data.json 없음 — 먼저 `python hr/hr_synth.py` 실행")
    data = json.load(open(synth_path, encoding="utf-8"))
    weeks = data["weeks"]
    os.makedirs(SAMPLE, exist_ok=True)

    # 결정론적 오프셋(합=0): 멤버 j에 (j-(m-1)/2)*spread 부여
    def offsets(m, spread):
        return [round((j - (m - 1) / 2) * spread, 1) for j in range(m)]

    roster_rows = []
    ind_rows = {k: [] for k in INDICATORS}
    pid = 1
    for t in data["teams"]:
        m = t["size"]
        members = [f"P{pid + j:03d}" for j in range(m)]
        pid += m
        for p in members:
            roster_rows.append({"person_id": p, "team": t["name"]})
        for wi, w in enumerate(weeks):
            for key, off in (
                ("overtime", offsets(m, 1.5)),
                ("collab", offsets(m, 4.0)),
                ("enps", offsets(m, 6.0)),
            ):
                base = t[key][wi]
                for j, p in enumerate(members):
                    val = round(base + off[j], 1)
                    ind_rows[key].append({"person_id": p, "week": w, "value": val})

    with open(os.path.join(SAMPLE, "roster.csv"), "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, ["person_id", "team"])
        w.writeheader()
        w.writerows(roster_rows)
    for key, (fname, col) in INDICATORS.items():
        with open(os.path.join(SAMPLE, fname), "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, ["person_id", "week", col])
            w.writeheader()
            for r in ind_rows[key]:
                w.writerow({"person_id": r["person_id"], "week": r["week"], col: r["value"]})
    with open(os.path.join(SAMPLE, "headcount.csv"), "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, ["week", "net"])
        w.writeheader()
        for wi, wk in enumerate(weeks):
            w.writerow({"week": wk, "net": data["org"]["headcount_net"][wi]})
    print(f"✓ 샘플 CSV 생성: hr/sample_data/ ({len(roster_rows)}명 × {len(weeks)}주)")


def main():
    if "--make-sample" in sys.argv:
        make_sample()
        return
    data = aggregate()
    write_contract(data)
    print(f"✓ 어댑터: CSV → hr_data.json + hr/hr_data.js · {len(data['teams'])}팀 "
          f"(k-익명성 ≥{MIN_TEAM_SIZE}) · 기준 {data['as_of']}")


if __name__ == "__main__":
    main()
