/* =========================================================
   자금 조류 · 라이브 엣지 원장 (backtest/ledger.js)
   ─────────────────────────────────────────────────────────
   매일 생성된 신호를 backtest/ledger.jsonl 에 한 줄씩 적재한다.
   백테스트(과거)는 표본이 ~60일로 빈약하다. 원장은 매일 신호를 '미리' 기록해
   두므로, 시간이 지나며 진짜 out-of-sample 표본이 쌓인다 — 이후 사후수익을
   결합해 "신호가 실제로 예측했는가"를 정직하게 재판정할 수 있다.

   왜 중요한가: 백테스트는 과거를 들여다보며 규칙을 맞출 유혹(과최적화)이 있다.
   라이브 원장은 '기록 시점에 미래를 몰랐다'는 사실이 보장돼 가장 정직한 증거다.

   실행:  node backtest/ledger.js   (run_daily.sh가 매일 호출)
   ========================================================= */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const E = require(path.join(ROOT, "dashboard", "signal_engine.js"));
const S = require(path.join(__dirname, "stats.js"));
const ledgerPath = path.join(__dirname, "ledger.jsonl");

function appendIfNew(rec) {
  // 같은 기준일 중복 적재 방지(재실행·시딩 안전)
  let existing = "";
  try {
    existing = fs.readFileSync(ledgerPath, "utf8");
  } catch {
    /* 첫 실행 */
  }
  if (existing.includes('"as_of":"' + rec.as_of + '"')) return false;
  fs.appendFileSync(ledgerPath, JSON.stringify(rec) + "\n");
  return true;
}

// ── --seed: history.json을 시점불변 재생해 과거 신호를 backfill ──
// 정산(settle.js) 검증·교차확인용. seeded:true 로 라이브 OOS와 구분한다.
if (process.argv.includes("--seed")) {
  const hist = JSON.parse(
    fs.readFileSync(path.join(__dirname, "history.json"), "utf8"),
  );
  const { WARMUP } = S.CONFIG;
  const byDate = {}; // 날짜 → [신호...]
  for (const st of hist.stocks) {
    const { foreign, institution, close, dates } = st;
    for (let t = WARMUP; t < foreign.length; t++) {
      const z = E.zlast(foreign.slice(0, t + 1));
      if (Math.abs(z) < E.STOCK_MIN_Z) continue;
      const dir = Math.sign(foreign[t]);
      const conf = 1 + (dir === Math.sign(institution[t]) && dir !== 0 ? 1 : 0);
      (byDate[dates[t]] = byDate[dates[t]] || []).push({
        scope: st.name,
        tier: E.tierOf(Math.abs(z), conf),
        z: +z.toFixed(2),
        conf,
        dir,
      });
    }
  }
  let added = 0;
  for (const d of Object.keys(byDate).sort()) {
    if (
      appendIfNew({
        as_of: d,
        logged_at: null,
        seeded: true,
        signals: byDate[d],
      })
    )
      added++;
  }
  console.log(
    `✓ 시딩 완료: ${added}일치 적재(seeded). 정산 검증용 — 라이브 OOS 아님.`,
  );
  process.exit(0);
}

let data;
try {
  data = JSON.parse(fs.readFileSync(path.join(ROOT, "data.json"), "utf8"));
} catch {
  console.error("✗ data.json 없음 — collector를 먼저 실행하세요.");
  process.exit(1);
}

const signals = E.buildSignals(data);

const rec = {
  as_of: data.as_of,
  logged_at: new Date().toISOString(),
  // 종목 신호만 기록(실제 매매 대상). 지수 신호는 컨텍스트라 제외.
  signals: signals
    .filter((s) => s.scope !== "KOSPI" && s.scope !== "KOSDAQ")
    .map((s) => ({
      scope: s.scope,
      tier: s.tier,
      z: +s.z.toFixed(2),
      conf: s.conf,
      dir: s.title.includes("매수") ? 1 : s.title.includes("매도") ? -1 : 0,
    })),
};
if (appendIfNew(rec)) {
  console.log(
    `✓ 엣지 원장 적재: ${data.as_of} · 종목신호 ${rec.signals.length}건`,
  );
} else {
  console.log("· 원장: " + data.as_of + " 이미 적재됨(건너뜀)");
}
