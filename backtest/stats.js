/* =========================================================
   자금 조류 · 백테스트/정산 공유 방법론 (backtest/stats.js)
   ─────────────────────────────────────────────────────────
   backtest.js(과거 재생)와 settle.js(라이브 원장 정산)가 '같은 잣대'로 판정하도록
   설정 상수와 통계·판정 함수를 한 곳에 둔다. 방법론이 갈라지면 두 결과를 비교할 수 없다.
   ========================================================= */

// ── 공유 설정 ──
const CONFIG = {
  WARMUP: 20, // zlast 신뢰 위한 최소 과거(백테스트 재생용)
  HORIZONS: [1, 5, 10], // 보유일수(거래일)
  COST: 0.002, // 왕복 거래비용 0.2%(수수료+증권거래세 근사)
  MIN_N: 15, // 이 미만이면 '표본부족'
};

// ── 통계 ──
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);

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

// ── 판정 ──
// arr: 거래 순수익 배열, baseMean: 같은 기간 기준선(드리프트) 평균
function verdict(arr, baseMean) {
  if (arr.length < CONFIG.MIN_N) return "표본부족";
  const t = tstat(arr);
  if (Math.abs(t) < 2) return "노이즈(유의X)";
  const m = mean(arr);
  if (m <= 0) return "음(−)";
  return m > baseMean ? "양(+) 엣지후보" : "양(+)이나 기준선이하";
}

module.exports = { CONFIG, mean, std, tstat, pct, verdict };
