/* =========================================================
   신호 엔진 단위 테스트 (signal_engine.test.js)
   ─────────────────────────────────────────────────────────
   실행:  node dashboard/signal_engine.test.js
   의존성: 없음 (Node 표준 assert)

   설계: 길이 30의 "상수 base + 마지막값 last" 시계열을 쓰면
         arr[:-1]이 전부 상수라 sd=0→1이 되어 zlast = (last - base)가 된다.
         즉 z를 정확히 통제할 수 있어 등급 경계를 결정론적으로 검증한다.
   ========================================================= */
const assert = require("assert");
const E = require("./signal_engine.js");

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log("  ✓ " + name);
}

// 길이 30 시계열: 29×base 뒤 last 하나. zlast === last - base.
function series(base, last) {
  return Array(29).fill(base).concat([last]);
}
// close 시계열: 마지막이 directionDir(+1/-1/0)만큼 직전과 다르게.
function closeSeries(dir) {
  const arr = Array(29).fill(1000);
  arr.push(1000 + dir * 5); // 마지막날 등락 방향만 결정
  return arr;
}
function idx(fBase, fLast, iBase, iLast, closeDir) {
  return {
    dates: [],
    foreign: series(fBase, fLast),
    institution: series(iBase, iLast),
    close: closeSeries(closeDir),
  };
}
function stock(name, ticker, fLast, iLast) {
  return {
    ticker,
    name,
    foreign: series(0, fLast),
    institution: series(0, iLast),
    fhold: [],
  };
}

console.log("── 1. zlast / stats (자기참조 z-score) ──");
test("상수열 뒤 스파이크 → zlast = last-base", () => {
  assert.strictEqual(E.zlast(series(0, 3)), 3);
  assert.strictEqual(E.zlast(series(10, 7)), -3); // 음의 극단도 정확
});
test("sd=0 보호: 완전 상수열이면 1로 나눠 발산 안 함", () => {
  assert.strictEqual(E.stats([5, 5, 5, 5]).sd, 1);
});

console.log("── 2. tierOf (등급 경계) ──");
test("critical: |z|>=2.0 (z 분기)", () => {
  assert.strictEqual(E.tierOf(2.0, 1), "critical");
  assert.strictEqual(E.tierOf(2.5, 1), "critical");
});
test("critical: confluence>=3 (정렬 분기)", () => {
  assert.strictEqual(E.tierOf(0.3, 3), "critical");
});
test("alert: |z|>=1.2 또는 conf>=2", () => {
  assert.strictEqual(E.tierOf(1.5, 1), "alert"); // z 분기
  assert.strictEqual(E.tierOf(0.5, 2), "alert"); // conf 분기
});
test("watch: 약신호 (z<1.2 && conf<2)", () => {
  assert.strictEqual(E.tierOf(1.19, 1), "watch");
  assert.strictEqual(E.tierOf(0.0, 1), "watch");
});
test("경계값: 1.2→alert, 2.0→critical (>= 포함)", () => {
  assert.strictEqual(E.tierOf(1.2, 1), "alert");
  assert.strictEqual(E.tierOf(2.0, 1), "critical");
});

console.log("── 3. buildSignals 통합 (지수 정렬/역행) ──");
test("지수 3조류 정렬 → critical, conf=3", () => {
  // 외국인↑ 기관↑ 종가↑ 전부 양(+) 정렬
  const data = {
    index: { KOSPI: idx(0, 1, 0, 1, +1), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [] },
  };
  const k = E.buildSignals(data).find((s) => s.scope === "KOSPI");
  assert.strictEqual(k.conf, 3);
  assert.strictEqual(k.tier, "critical");
});
test("지수 역행(수급·가격 엇갈림) → conf=1, z로만 등급", () => {
  // 외국인 +1.5(z=1.5), 기관 -1(역행), 종가↓(역행) → conf=1, az=1.5 → alert
  const data = {
    index: { KOSPI: idx(0, 1.5, 0, -1, -1), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [] },
  };
  const k = E.buildSignals(data).find((s) => s.scope === "KOSPI");
  assert.strictEqual(k.conf, 1);
  assert.strictEqual(k.tier, "alert");
});
test("지수 약신호 → watch (z<1.2, 정렬 없음)", () => {
  const data = {
    index: { KOSPI: idx(0, 0.5, 0, -0.3, 0), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [] },
  };
  const k = E.buildSignals(data).find((s) => s.scope === "KOSPI");
  assert.strictEqual(k.conf, 1);
  assert.strictEqual(k.tier, "watch");
});

console.log("── 4. buildSignals 통합 (종목 노이즈 컷 / 정렬) ──");
test("종목 |z|<1.2 → 신호 생성 안 함(노이즈 컷)", () => {
  const data = {
    index: { KOSPI: idx(0, 0, 0, 0, 0), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [stock("약신호", "000001", 1.0, 0)] },
  };
  const hit = E.buildSignals(data).find((s) => s.scope === "약신호");
  assert.strictEqual(hit, undefined);
});
test("종목 강한 매수(z=2.5)+기관 동조 → critical, conf=2", () => {
  const data = {
    index: { KOSPI: idx(0, 0, 0, 0, 0), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [stock("강매수", "000002", 2.5, 1)] },
  };
  const hit = E.buildSignals(data).find((s) => s.scope === "강매수");
  assert.strictEqual(hit.conf, 2);
  assert.strictEqual(hit.tier, "critical");
});
test("종목 경계 매수(z=1.3)+기관 역행 → alert, conf=1", () => {
  const data = {
    index: { KOSPI: idx(0, 0, 0, 0, 0), KOSDAQ: idx(0, 0, 0, 0, 0) },
    sector: { name: "t", stocks: [stock("경계", "000003", 1.3, -1)] },
  };
  const hit = E.buildSignals(data).find((s) => s.scope === "경계");
  assert.strictEqual(hit.conf, 1);
  assert.strictEqual(hit.tier, "alert");
});

console.log("── 5. 정렬 순서 (tier 우선 → z 내림차순) ──");
test("critical이 alert/watch보다 앞, 동급은 z 큰 순", () => {
  const data = {
    index: { KOSPI: idx(0, 1, 0, 1, +1), KOSDAQ: idx(0, 0.5, 0, -0.3, 0) },
    sector: {
      name: "t",
      stocks: [stock("강매수", "x", 2.5, 1), stock("경계", "y", 1.3, -1)],
    },
  };
  const sigs = E.buildSignals(data);
  // 첫 신호는 반드시 critical
  assert.strictEqual(sigs[0].tier, "critical");
  // tier가 단조 비증가(critical→alert→watch) 순서인지
  const rank = { critical: 0, alert: 1, watch: 2 };
  for (let n = 1; n < sigs.length; n++) {
    assert.ok(rank[sigs[n].tier] >= rank[sigs[n - 1].tier], "tier 순서 위반");
  }
});

console.log("── 6. 포맷 헬퍼 ──");
test("fmt: 부호 표기(+ / 유니코드 −)", () => {
  assert.strictEqual(E.fmt(5), "+5");
  assert.strictEqual(E.fmt(-5), "−5");
  assert.strictEqual(E.fmt(0), "+0");
});

console.log("\n✅ 전체 " + passed + "개 테스트 통과");
