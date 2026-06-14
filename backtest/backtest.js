/* =========================================================
   자금 조류 · 백테스트 하니스 (backtest/backtest.js)
   ─────────────────────────────────────────────────────────
   history.json 을 시점불변(point-in-time)으로 재생해 신호의 예측력을 측정한다.
   신호 로직은 dashboard/signal_engine.js 를 그대로 재사용한다(단일 출처).

   정직성 규칙:
   · 무미래참조: t일 종가로 신호 산출 → 신호는 t 마감 후에야 안다 → 진입은 t+1 종가.
   · 비용차감: 왕복 거래비용(수수료+세금 근사)을 매 거래 수익에서 뺀다.
   · 방향성: 외국인 매수신호=롱, 매도신호=숏(개인 현실상 숏 어려움 → long-only도 별도).
   · 기준선: '아무 날이나 다음날 롱'의 평균수익(드리프트)과 비교. 신호가 그걸 이기나?
   · 유의성: t값으로 노이즈와 구분. 표본 적으면 '표본부족'으로 솔직히 표기.

   실행:  node backtest/backtest.js
   ========================================================= */
const fs = require("fs");
const path = require("path");
const E = require(path.join(__dirname, "..", "dashboard", "signal_engine.js"));

// ── 설정 ──
const WARMUP = 20; // zlast 신뢰 위한 최소 과거
const HORIZONS = [1, 5, 10]; // 보유일수(거래일)
const COST = 0.002; // 왕복 거래비용 0.2%(수수료+증권거래세 근사)
const MIN_N = 15; // 이 미만이면 통계적으로 '표본부족'

const hist = JSON.parse(
  fs.readFileSync(path.join(__dirname, "history.json"), "utf8"),
);

// ── 통계 유틸 ──
const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
function std(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1));
}
function tstat(a) {
  // 평균이 0과 다른가 (H0: mean=0)
  const s = std(a);
  return s === 0 ? 0 : mean(a) / (s / Math.sqrt(a.length));
}
const pct = (x) => (x * 100).toFixed(2) + "%";

// ── 재생: 모든 종목·모든 날의 신호와 사후수익 ──
// trades[tier][h] = [수익...], trades.ALL[h] = [...]; longOnly 동일 구조
const trades = { critical: {}, alert: {}, watch: {}, ALL: {} };
const tradesLong = { critical: {}, alert: {}, watch: {}, ALL: {} };
const baseline = {}; // h → [무조건 익일 롱 수익...]
HORIZONS.forEach((h) => {
  ["critical", "alert", "watch", "ALL"].forEach((k) => {
    trades[k][h] = [];
    tradesLong[k][h] = [];
  });
  baseline[h] = [];
});

let signalDays = 0;
for (const s of hist.stocks) {
  const { foreign, institution, close } = s;
  const N = foreign.length;
  for (let t = WARMUP; t < N - 1; t++) {
    // 기준선: 신호와 무관하게 t+1 진입 롱 수익(드리프트) 적재
    HORIZONS.forEach((h) => {
      const exit = t + 1 + h;
      if (exit < N)
        baseline[h].push((close[exit] - close[t + 1]) / close[t + 1]);
    });

    // 시점불변 신호: t까지의 외국인 시계열로 z 산출
    const z = E.zlast(foreign.slice(0, t + 1));
    if (Math.abs(z) < E.STOCK_MIN_Z) continue; // 종목 노이즈 컷(엔진과 동일)

    const dir = Math.sign(foreign[t]); // 매수(+1)/매도(-1)
    const iDir = Math.sign(institution[t]);
    const conf = 1 + (dir === iDir && dir !== 0 ? 1 : 0);
    const tier = E.tierOf(Math.abs(z), conf);
    signalDays++;

    HORIZONS.forEach((h) => {
      const entry = t + 1,
        exit = t + 1 + h;
      if (exit >= N) return;
      const raw = (close[exit] - close[entry]) / close[entry];
      const net = dir * raw - COST; // 신호 방향 베팅, 비용 차감
      trades[tier][h].push(net);
      trades.ALL[h].push(net);
      if (dir > 0) {
        // long-only(매수신호만 롱) — 개인 현실 모드
        const netLong = raw - COST;
        tradesLong[tier][h].push(netLong);
        tradesLong.ALL[h].push(netLong);
      }
    });
  }
}

// ── 판정 ──
function verdict(arr, baseMean) {
  if (arr.length < MIN_N) return "표본부족";
  const t = tstat(arr);
  if (Math.abs(t) < 2) return "노이즈(유의X)";
  const m = mean(arr);
  if (m <= 0) return "음(−)";
  return m > baseMean ? "양(+) 엣지후보" : "양(+)이나 기준선이하";
}

function reportBucket(title, store) {
  console.log(`\n■ ${title}`);
  console.log(
    "  " +
      ["등급", "기간", "N", "평균", "적중", "t값", "기준선", "판정"]
        .map((s, i) => s.padEnd([6, 5, 4, 8, 7, 6, 8, 16][i]))
        .join(""),
  );
  for (const tier of ["critical", "alert", "watch", "ALL"]) {
    for (const h of HORIZONS) {
      const arr = store[tier][h];
      const bm = baseline[h].length ? mean(baseline[h]) : 0;
      if (!arr.length) continue;
      const row = [
        tier === "ALL" ? "전체" : tier,
        h + "일",
        String(arr.length),
        arr.length ? pct(mean(arr)) : "-",
        arr.length
          ? ((100 * arr.filter((x) => x > 0).length) / arr.length).toFixed(0) +
            "%"
          : "-",
        arr.length ? tstat(arr).toFixed(2) : "-",
        pct(bm),
        verdict(arr, bm),
      ];
      console.log(
        "  " +
          row
            .map((s, i) => String(s).padEnd([6, 5, 4, 8, 7, 6, 8, 16][i]))
            .join(""),
      );
    }
  }
}

// ── 출력 ──
console.log("════════════════════════════════════════════════════════");
console.log(" 자금 조류 · 엣지 백테스트");
console.log("════════════════════════════════════════════════════════");
const days = hist.stocks[0].dates;
console.log(
  `표본 구간 : ${days[0]} ~ ${days[days.length - 1]} (${days.length}거래일 × ${hist.stocks.length}종목)`,
);
console.log(
  `설정      : warmup ${WARMUP}일 · 왕복비용 ${pct(COST)} · 익일진입(무미래참조)`,
);
console.log(`신호 발생 : 총 ${signalDays}건 (|z|≥${E.STOCK_MIN_Z})`);

reportBucket("방향성 모드 (매수=롱, 매도=숏) — 이론적", trades);
reportBucket("Long-only 모드 (매수신호만 롱) — 개인 현실", tradesLong);

console.log("\n── 해석 가이드 ──");
console.log(
  "· '판정'이 '양(+) 엣지후보'이고 기준선을 넘어야 비로소 '엣지 가능성'이다.",
);
console.log(
  "· t값 |2| 미만은 우연과 구분 불가. N<" + MIN_N + "은 통계적으로 무의미.",
);
console.log(
  "· 한계: 단일 상승장 구간 3개월, 고정 5종목(생존편향), 슬리피지 미반영,",
);
console.log(
  "        숏 비용 과소. 이 표본으로 '엣지 있음'을 주장할 수 없다 — 라이브",
);
console.log(
  "        원장(ledger.jsonl)으로 표본을 누적해 out-of-sample로 재판정할 것.",
);
