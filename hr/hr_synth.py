"""
조직 조류 · People Flow Intelligence — 합성 데이터 생성 (hr/hr_synth.py)
────────────────────────────────────────────────────────
자금 조류(money flow)의 동형(isomorphic) HR 버전. 시장의 약신호를 읽던 문법을
'조직의 약신호'(이탈·번아웃·몰입저하)로 옮긴다. 신호 판정 커널(zlast·tierOf)은
money flow의 signal_engine.js를 그대로 재사용한다 — 여기서는 입력 데이터만 만든다.

윤리 가드레일 (중요):
  · 분석 단위는 팀(team)이다. 개인 감시 도구가 아니다.
  · 데이터는 전부 합성(synthetic)이다. 실제 직원 PII를 쓰지 않는다.
  · 목적은 처벌용 워치리스트가 아니라, 경영진의 '지원적 주의'를 유도하는 약신호다.

데이터 계약 (THE CONTRACT — money flow §4의 HR 대응):
  · 주(week) 단위, 모든 배열 길이 12(주).
  · org.attrition_pressure = 조직 이탈압력 지수(높을수록 위험). 'tide'.
  · team.overtime  = 주당 평균 초과근무시간 (↑ = 번아웃 약신호)
  · team.collab    = 협업활동지수 0~100 (↓ = 몰입저하 약신호)
  · team.enps      = 펄스 eNPS −100~+100 (↓ = 정서 약화 약신호)

실행:  python hr/hr_synth.py   →  hr_data.json + hr/hr_data.js (window.HR_DATA)
"""

import os
import json

N = 12  # 12주
AS_OF = "2026-W24"


# ── 결정론적 의사난수 (재현 가능한 합성 데이터) ──
def lcg(seed):
    state = {"s": seed & 0xFFFFFFFF}

    def rnd():
        state["s"] = (1103515245 * state["s"] + 12345) & 0x7FFFFFFF
        return state["s"] / 0x7FFFFFFF

    return rnd


def series(seed, base, drift, vol, lo=None, hi=None):
    """base에서 출발해 매주 drift만큼 추세 + vol 노이즈. 선택적 클리핑."""
    r = lcg(seed)
    out = []
    v = base
    for i in range(N):
        v = v + drift + (r() - 0.5) * 2 * vol
        x = v
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        out.append(round(x, 1))
    return out


def weeks():
    # 2026-W13 ~ W24 (12주)
    return [f"2026-W{13 + i}" for i in range(N)]


# ── 팀 정의 (약신호 패턴을 의도적으로 심는다) ──
# drift 부호로 '위험으로 가는 추세'를 만든다.
# vol(노이즈)을 낮춰 심은 추세가 z를 지배하게 한다(데모 가독성).
# 깨끗한 램프는 z≈1.5~1.7 → confluence 수로 등급 그라데이션을 만든다:
#   3중 정렬=critical, 2중=alert, 단일=alert, 평탄=무신호.
# 추세 지표는 약한 노이즈, '평탄' 지표는 노이즈 0 → 의도한 약신호만 발화한다.
# 깨끗한 램프는 z≈1.5~1.7 → confluence 수가 등급을 가른다(critical=3, alert=2/1, 건강=무신호).
TEAMS = [
    # 백엔드팀: 초과근무↑ 협업↓ eNPS↓ 3중 정렬 → Critical(confluence가 등급을 올림)
    {"id": "T01", "name": "백엔드팀", "size": 8,
     "overtime": (101, 8, 0.8, 0.0), "collab": (102, 80, -1.4, 0.0), "enps": (103, 25, -1.6, 0.0)},
    # 디자인팀: 협업↓ eNPS↓ 2중 정렬(초과근무 평탄) → Alert
    {"id": "T02", "name": "디자인팀", "size": 6,
     "overtime": (201, 6, 0.0, 0.0), "collab": (202, 78, -1.4, 0.0), "enps": (203, 12, -1.6, 0.0)},
    # 프론트팀: 초과근무↑ 단독 → Alert(같은 z라도 단일이라 등급이 낮다)
    {"id": "T03", "name": "프론트팀", "size": 7,
     "overtime": (301, 7, 1.0, 0.0), "collab": (302, 75, 0.0, 0.0), "enps": (303, 15, 0.0, 0.0)},
    # 데이터팀: 건강(전 지표 안정·소폭 개선) → 무신호
    {"id": "T04", "name": "데이터팀", "size": 5,
     "overtime": (401, 5, 0.0, 0.0), "collab": (402, 85, 0.2, 0.0), "enps": (403, 30, 0.2, 0.0)},
    # QA팀: 협업만 약하게↓ 단독 → Alert(다른 드라이버의 단일 약신호)
    {"id": "T05", "name": "QA팀", "size": 6,
     "overtime": (501, 6, 0.0, 0.0), "collab": (502, 76, -0.9, 0.0), "enps": (503, 18, 0.0, 0.0)},
]


def build_team(t):
    ov = series(t["overtime"][0], *t["overtime"][1:], lo=0)
    co = series(t["collab"][0], *t["collab"][1:], lo=0, hi=100)
    en = series(t["enps"][0], *t["enps"][1:], lo=-100, hi=100)
    return {"id": t["id"], "name": t["name"], "size": t["size"],
            "overtime": ov, "collab": co, "enps": en}


def main():
    teams = [build_team(t) for t in TEAMS]

    # 조직 tide: 이탈압력 = 팀 위험요소의 거친 합성(데모용 지표).
    #   초과근무 평균 + (100-협업) 평균 + (50-eNPS) 평균 의 주별 합을 스케일.
    pressure = []
    engagement = []
    for w in range(N):
        ot = sum(t["overtime"][w] for t in teams) / len(teams)
        col = sum(t["collab"][w] for t in teams) / len(teams)
        en = sum(t["enps"][w] for t in teams) / len(teams)
        pressure.append(round(ot + (100 - col) + (50 - en) / 2, 1))
        engagement.append(round((col + (en + 100) / 2) / 2, 1))

    head = lcg(999)
    headcount_net = [round((head() - 0.55) * 4) for _ in range(N)]  # 약한 순유출 경향

    data = {
        "as_of": AS_OF,
        "weeks": weeks(),
        "org": {
            "attrition_pressure": pressure,
            "engagement": engagement,
            "headcount_net": headcount_net,
        },
        "teams": teams,
        "_meta": {"synthetic": True, "unit": "team", "note": "합성 데모 데이터 · 개인 감시 아님"},
    }

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(os.path.join(root, "hr_data.json"), "w", encoding="utf-8") as fp:
        fp.write(payload)
    with open(os.path.join(here, "hr_data.js"), "w", encoding="utf-8") as fp:
        fp.write("window.HR_DATA = " + payload + ";\n")
    print(f"✓ hr_data.json + hr/hr_data.js 생성 · 기준 {AS_OF} · {len(teams)}팀 × {N}주 (합성)")


if __name__ == "__main__":
    main()
