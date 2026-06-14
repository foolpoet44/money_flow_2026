/* =========================================================
   조직 조류 · HR 신호 엔진 (hr/hr_engine.js)
   ─────────────────────────────────────────────────────────
   money flow의 signal_engine.js 커널(zlast·tierOf)을 '그대로' 재사용한다.
   동형(isomorphic)의 증명: 약신호를 읽는 수학은 도메인 불변이고, 도메인마다
   다른 건 '어떤 지표를 위험 방향으로 정렬시키는가'(어댑터)뿐이다.

   판정 철학(money flow와 동일):
   · 자기참조 z: 각 팀을 '자기 자신의 과거'와 비교(ergodicity). 내향적인 팀의
     낮은 협업은 신호가 아니다 — 그 팀 '자신의 기준선'에서의 하락이 신호다.
     (횡단면 비교의 편향을 피한다 — 사람을 서로 비교하지 않는다.)
   · confluence: 단일 지표는 노이즈. 초과근무↑ + 협업↓ + eNPS↓ 가 '함께' 위험을
     가리킬 때 비로소 신호다.

   윤리: 분석 단위는 팀. 개인 워치리스트가 아니라 경영진의 지원적 주의를 위한 약신호.
   ========================================================= */
(function (global) {
  "use strict";

  // 커널 재사용 — Node는 require, 브라우저는 먼저 로드된 signal_engine.js의 전역
  const SE =
    typeof module !== "undefined" && module.exports
      ? require("../dashboard/signal_engine.js")
      : global;
  const zlast = SE.zlast;
  const tierOf = SE.tierOf; // Z_ALERT=1.2 / Z_CRITICAL=2.0 그대로 사용

  const HR_MIN_Z = 1.0; // 약신호 노이즈 컷(주간·소표본이라 시장보다 살짝 완화)
  const ALIGN_Z = 1.0; // 이 이상이면 '위험 방향으로 정렬됐다'

  // 한 팀의 위험 약신호 — 각 지표를 '위험=양(+)' 방향으로 정규화
  function teamSignal(team) {
    const drivers = [
      { key: "초과근무↑", z: zlast(team.overtime) }, // 상승 = 번아웃 위험(+)
      { key: "협업↓", z: -zlast(team.collab) }, // 하락 = 몰입저하 위험(+, 부호반전)
      { key: "eNPS↓", z: -zlast(team.enps) }, // 하락 = 정서약화 위험(+)
    ];
    const peak = Math.max(...drivers.map((d) => d.z)); // 가장 강한 위험 약신호
    const aligned = drivers.filter((d) => d.z >= ALIGN_Z); // 위험 정렬 지표
    if (peak < HR_MIN_Z && aligned.length < 2) return null; // 노이즈 컷
    const conf = Math.max(1, aligned.length);
    return {
      scope: team.name,
      level: "team",
      tier: tierOf(peak, conf),
      z: peak,
      conf,
      drivers: aligned.map((d) => d.key),
      detail: drivers,
    };
  }

  // 조직 전체 tide — 이탈압력↑ + 몰입↓ + 순유출 confluence
  function orgSignal(org) {
    const zPressure = zlast(org.attrition_pressure); // 압력 상승 = 위험(+)
    const zEng = -zlast(org.engagement); // 몰입 하락 = 위험(+)
    const net = org.headcount_net[org.headcount_net.length - 1];
    const peak = Math.max(zPressure, zEng);
    let conf = 1;
    const drivers = ["이탈압력↑"];
    if (zPressure >= ALIGN_Z && zEng >= ALIGN_Z) {
      conf++;
      drivers.push("몰입↓");
    }
    if (net < 0 && peak >= ALIGN_Z) {
      conf++;
      drivers.push("순유출");
    }
    return {
      scope: "전사",
      level: "org",
      tier: tierOf(peak, conf),
      z: peak,
      conf,
      drivers,
    };
  }

  function buildHRSignals(hr) {
    const sigs = [orgSignal(hr.org)];
    hr.teams.forEach((t) => {
      const s = teamSignal(t);
      if (s) sigs.push(s);
    });
    const order = { critical: 0, alert: 1, watch: 2 };
    sigs.sort((a, b) => order[a.tier] - order[b.tier] || b.z - a.z);
    return sigs;
  }

  const api = { HR_MIN_Z, ALIGN_Z, teamSignal, orgSignal, buildHRSignals };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(global, api);
})(typeof window !== "undefined" ? window : globalThis);
