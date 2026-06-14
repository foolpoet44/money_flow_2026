/* =========================================================
   조직 조류 · HR 신호 엔진 테스트 (hr/hr_engine.test.js)
   실행:  node hr/hr_engine.test.js
   기법: 길이 12의 '상수×11 + 마지막값' 시계열 → zlast = last - base (통제 가능)
   ========================================================= */
const assert = require("assert");
const H = require("./hr_engine.js");

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log("  ✓ " + name);
}

// zlast(series(base,last)) === last - base
const series = (base, last) => Array(11).fill(base).concat([last]);
const team = (over, collab, enps) => ({
  name: "T",
  size: 5,
  overtime: series(over[0], over[1]),
  collab: series(collab[0], collab[1]),
  enps: series(enps[0], enps[1]),
});

console.log("── 1. 팀 약신호 (위험 방향 정규화) ──");
test("초과근무↑ 협업↓ eNPS↓ 3중 정렬 → critical, conf=3", () => {
  // over z=+3, collab risk=+2, enps risk=+1.5 → 모두 위험 정렬
  const s = H.teamSignal(team([5, 8], [80, 78], [20, 18.5]));
  assert.strictEqual(s.conf, 3);
  assert.strictEqual(s.tier, "critical");
  assert.strictEqual(s.drivers.length, 3);
});
test("건강한 팀(약신호 없음) → null", () => {
  const s = H.teamSignal(team([5, 5], [80, 80], [20, 20]));
  assert.strictEqual(s, null);
});
test("단일 약신호(초과근무만 +1.5) → alert, conf=1", () => {
  const s = H.teamSignal(team([5, 6.5], [80, 80], [20, 20]));
  assert.strictEqual(s.conf, 1);
  assert.strictEqual(s.tier, "alert");
});
test("협업 개선(위험 음수)은 신호 아님 → null", () => {
  // collab 상승(위험 -), 나머지 평탄 → 위험 정렬 0
  const s = H.teamSignal(team([5, 5], [80, 85], [20, 20]));
  assert.strictEqual(s, null);
});

console.log("── 2. 조직 tide 신호 ──");
test("이탈압력↑ + 몰입↓ + 순유출 → critical, conf=3", () => {
  const org = {
    attrition_pressure: series(40, 44), // z=+4
    engagement: series(60, 58), // 몰입 위험 +2
    headcount_net: series(0, -3), // 마지막 순유출
  };
  const s = H.orgSignal(org);
  assert.strictEqual(s.conf, 3);
  assert.strictEqual(s.tier, "critical");
});
test("압력만 약하게(+1.2), 몰입 평탄 → conf=1", () => {
  const org = {
    attrition_pressure: series(40, 41.2),
    engagement: series(60, 60),
    headcount_net: series(0, 2), // 순유입
  };
  const s = H.orgSignal(org);
  assert.strictEqual(s.conf, 1);
});

console.log("── 3. buildHRSignals 통합 ──");
test("조직 신호 항상 포함 + critical 우선 정렬", () => {
  const hr = {
    org: {
      attrition_pressure: series(40, 41),
      engagement: series(60, 60),
      headcount_net: series(0, 1),
    },
    teams: [
      team([5, 8], [80, 78], [20, 18.5]), // critical
      team([5, 5], [80, 80], [20, 20]), // null(제외)
    ],
  };
  const sigs = H.buildHRSignals(hr);
  assert.ok(sigs.some((s) => s.scope === "전사"));
  const rank = { critical: 0, alert: 1, watch: 2 };
  for (let i = 1; i < sigs.length; i++) {
    assert.ok(rank[sigs[i].tier] >= rank[sigs[i - 1].tier], "정렬 위반");
  }
});

console.log("\n✅ 전체 " + passed + "개 테스트 통과");
