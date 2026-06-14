/* =========================================================
   리서치 종합·추천 엔진 단위 테스트 (research_digest.test.js)
   실행:  node dashboard/research_digest.test.js
   의존성: 없음 (Node 표준 assert)
   ========================================================= */
const assert = require("assert");
const D = require("./research_digest.js");

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log("  ✓ " + name);
}

// 리포트 팩토리
function rep(o) {
  return Object.assign(
    {
      category: "company",
      category_label: "종목",
      title: "",
      stock: "",
      ticker: "",
      broker: "X증권",
      date: "2026-06-12",
      views: 0,
      summary: "",
      target: "",
      opinion: "",
      sector: false,
    },
    o,
  );
}

console.log("── 1. 테마 빈도 집계 ──");
test("제목+요약에서 키워드 빈도를 세고 내림차순 정렬", () => {
  const reports = [
    rep({ title: "반도체 HBM 호황", summary: "반도체 HBM 수요 급증" }), // 반도체×2, HBM×2
    rep({ title: "반도체 업황", summary: "" }), // 반도체×1
  ];
  const themes = D.countKeywords(reports, ["반도체", "HBM", "배터리"]);
  assert.strictEqual(themes[0].k, "반도체"); // 3회
  assert.strictEqual(themes[0].n, 3);
  assert.strictEqual(themes[1].k, "HBM"); // 2회
  assert.strictEqual(themes[1].n, 2);
  assert.ok(!themes.find((t) => t.k === "배터리")); // 0회는 제외
});

console.log("── 2. 톤(매수/신중) 근사 ──");
test("긍정 키워드 우세 → 매수 우위", () => {
  const dg = D.buildDigest(
    {
      as_of: "2026-06-12",
      reports: [rep({ title: "수혜 성장 매수", summary: "개선 반등" })],
    },
    [],
  );
  assert.strictEqual(dg.tone.label, "매수 우위");
  assert.ok(dg.tone.bull > dg.tone.bear);
});
test("부정 키워드 우세 → 신중 우위", () => {
  const dg = D.buildDigest(
    { reports: [rep({ title: "부진 우려 하향", summary: "둔화 리스크" })] },
    [],
  );
  assert.strictEqual(dg.tone.label, "신중 우위");
});

console.log("── 3. 수급 신호 교차 추천 (핵심) ──");
test("Critical 신호 종목 리포트가 최상위 추천 + 이유 명시", () => {
  const reports = [
    rep({ stock: "평범종목", title: "그냥 리포트", views: 9000 }), // 조회수만 높음
    rep({
      stock: "삼성전기",
      title: "삼성전기 분석",
      views: 100,
      sector: true,
    }),
  ];
  const signals = [{ scope: "삼성전기", tier: "critical" }];
  const dg = D.buildDigest({ reports }, signals);
  // 신호 종목이 조회수 높은 무신호 종목을 앞선다
  assert.strictEqual(dg.recommendations[0].report.stock, "삼성전기");
  assert.ok(
    dg.recommendations[0].reasons.some((r) => r.includes("Critical")),
    "Critical 이유가 표시돼야 함",
  );
});
test("같은 종목 다중 신호면 더 강한 등급을 채택", () => {
  const signals = [
    { scope: "A", tier: "watch" },
    { scope: "A", tier: "critical" }, // 더 강한 등급이 채택돼야 함
  ];
  const dg = D.buildDigest(
    { reports: [rep({ stock: "A", title: "t" })] },
    signals,
  );
  assert.ok(dg.recommendations[0].reasons.some((r) => r.includes("Critical")));
});

console.log("── 4. 추천 정렬·이유 일반 규칙 ──");
test("섹터·시황·조회수 가점이 이유로 누적", () => {
  const r = rep({
    category: "market",
    category_label: "시황",
    title: "시황",
    sector: true,
    views: 9000,
    summary: "본문",
    target: "50000원",
  });
  const sigMap = {};
  const out = D.scoreReport(r, sigMap);
  assert.ok(out.score > 0);
  assert.ok(out.reasons.includes("반도체 섹터 연관"));
  assert.ok(out.reasons.includes("시황 — 전체 맥락"));
  assert.ok(out.reasons.includes("조회수 상위"));
  assert.ok(out.reasons.some((x) => x.includes("목표가")));
});
test("추천은 상위 3건, 점수 0은 제외", () => {
  const reports = [
    rep({ stock: "삼성전기", title: "a", sector: true }),
    rep({ category: "market", category_label: "시황", title: "b" }),
    rep({ title: "c", views: 5000 }),
    rep({ title: "d" }), // 모든 가점 0 → 점수 0 → 제외 대상
  ];
  const dg = D.buildDigest({ reports }, []);
  assert.ok(dg.recommendations.length <= 3);
  dg.recommendations.forEach((x) => assert.ok(x.score > 0));
});

console.log("── 5. 종합 통계 ──");
test("카테고리 분포·총건수·flow 교차 종목 집계", () => {
  const reports = [
    rep({ category_label: "시황", title: "시황1" }),
    rep({
      category_label: "종목",
      stock: "삼성전기",
      title: "s",
      sector: true,
    }),
    rep({
      category_label: "종목",
      stock: "삼성전기",
      title: "s2",
      sector: true,
    }),
  ];
  const dg = D.buildDigest({ reports }, [{ scope: "삼성전기", tier: "alert" }]);
  assert.strictEqual(dg.total, 3);
  assert.strictEqual(dg.byCat["시황"], 1);
  assert.strictEqual(dg.byCat["종목"], 2);
  // 중복 종목은 한 번만
  assert.deepStrictEqual(dg.flowLinkStocks, ["삼성전기"]);
});

console.log("\n✅ 전체 " + passed + "개 테스트 통과");
