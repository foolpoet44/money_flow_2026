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

/* ── 일자 집계 (독립성 보정) ────────────────────────────────
   왜 필요한가 (2026-08-19 측정):
     tstat 은 관측이 서로 독립이라고 가정한다. 그런데 같은 날 여러 종목에서 난 신호는
     독립이 아니다. 추적 5종목의 일별 수익률 쌍상관을 재 보니 ρ=0.871 이었다.
     유효표본수 N/(1+(N-1)ρ) 로 환산하면 5종목이든 50종목이든 하루 관측은 1.1개다.

     그런데 예전 구현은 (신호 × 기간)마다 1관측으로 세었다. 종목을 K배 늘리면 N 이 K배,
     t 값이 √K배로 부푼다 — 같은 데이터인데 유니버스만 넓혀서 노이즈가 '유의'로 둔갑한다.
     실측 예: 전체 5일 N=13 t=−3.86 → 25종목이면 N≈65 t≈−8.6.

     이 프로젝트는 훼손된 표본을 격리하고 '미측정'을 '엣지 없음'과 구분해 왔다.
     여기서 무너지면 그 모든 장치가 무의미하다.

   → 하루에 발생한 신호들의 평균 수익을 '그날의 1관측'으로 삼는다. N = 거래일 수다.
     정직한 부작용: 유니버스를 넓혀도 N 이 그만큼 늘지 않는다. 표본은 시간이 만든다.
     (넓힐수록 '신호가 난 날'의 비율이 올라가 N 이 조금 빨리 쌓이는 이득은 있다.)

   obs: [{ date, net }, ...]  →  날짜별 평균 수익 배열 (날짜 오름차순) */
function byDayMean(obs) {
  const byDate = new Map();
  for (const o of obs) {
    if (!byDate.has(o.date)) byDate.set(o.date, []);
    byDate.get(o.date).push(o.net);
  }
  return [...byDate.keys()].sort().map((d) => mean(byDate.get(d)));
}

// ── 판정 ──
// arr: **일자 집계된** 수익 배열(byDayMean 출력), baseMean: 같은 기간 기준선 평균.
// 집계 전 배열을 넘기면 N 이 부풀어 판정이 과신한다 — 호출부는 반드시 byDayMean 을 거친다.
function verdict(arr, baseMean) {
  if (arr.length < CONFIG.MIN_N) return "표본부족";
  const t = tstat(arr);
  if (Math.abs(t) < 2) return "노이즈(유의X)";
  const m = mean(arr);
  if (m <= 0) return "음(−)";
  return m > baseMean ? "양(+) 엣지후보" : "양(+)이나 기준선이하";
}

module.exports = { CONFIG, mean, std, tstat, pct, verdict, byDayMean };

/* ── 자체 검사 (node backtest/stats.js) ──
   일자 집계가 실제로 N 을 억제하는지 한 번은 돌려서 확인한다. */
if (require.main === module) {
  const A = (c, m) => console.assert(c, m) || (c ? 0 : (process.exitCode = 1));
  const obs = [
    { date: "2026-08-01", net: 0.1 },
    { date: "2026-08-01", net: 0.3 }, // 같은 날 두 종목 → 1관측(평균 0.2)
    { date: "2026-08-02", net: -0.1 },
  ];
  const agg = byDayMean(obs);
  A(agg.length === 2, "일자 집계 후 관측은 거래일 수와 같아야 한다");
  A(Math.abs(agg[0] - 0.2) < 1e-12, "같은 날 신호는 평균으로 묶인다");
  A(agg[1] === -0.1, "날짜 오름차순 유지");
  A(byDayMean([]).length === 0, "빈 입력은 빈 출력");
  // 같은 값을 K번 복제해도 t 가 커지지 않아야 한다 — 이 수정의 존재 이유
  const dup = (k) =>
    byDayMean(
      Array.from({ length: 10 }, (_, d) =>
        Array.from({ length: k }, () => ({
          date: "d" + d,
          net: 0.01 * (d - 5),
        })),
      ).flat(),
    );
  A(
    Math.abs(tstat(dup(1)) - tstat(dup(20))) < 1e-9,
    "종목을 20배로 늘려도 t 값이 변하지 않아야 한다",
  );
  console.log(
    process.exitCode
      ? "✗ stats 자체검사 실패"
      : "✓ stats 자체검사 통과 (일자 집계 5건)",
  );
}
