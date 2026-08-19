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
  // 같은 기준일 중복 적재 방지(재실행·시딩 안전).
  //
  // 왜 '선착순'이 아니라 '확정 우선'인가 (2026-08-07 사고):
  //   예전 구현은 무조건 선착순이었다. 수집이 개장 뒤로 밀린 날 장중 스냅샷으로 만든 신호가
  //   먼저 박히면, 익일의 확정치는 "이미 적재됨"으로 기각돼 원장이 영구히 틀린 채로 남았다.
  //   이제 collector 의 장중 가드가 미확정 세션을 애초에 차단하지만, 방어를 한 겹 더 둔다:
  //   저장된 레코드가 확정 표식(date_aligned)을 갖고 있지 않다면 확정본으로 덮어쓴다.
  let lines = [];
  try {
    lines = fs.readFileSync(ledgerPath, "utf8").split("\n").filter(Boolean);
  } catch {
    /* 첫 실행 */
  }
  const i = lines.findIndex((l) => {
    try {
      return JSON.parse(l).as_of === rec.as_of;
    } catch {
      return false;
    }
  });
  // as_of 오름차순을 유지한다. 예전엔 무조건 append 라 적재순으로 쌓였고, 승격·backfill 이
  // 섞이면 …08-05, 08-07, 08-06 처럼 순서가 깨졌다. 집계는 as_of 로 조회하므로 결과는
  // 정확했지만 "마지막 줄 = 최신"을 가정하는 코드가 생기면 조용히 틀린다.
  const writeSorted = (rows) => {
    rows.sort((a, b) => (a.as_of < b.as_of ? -1 : a.as_of > b.as_of ? 1 : 0));
    fs.writeFileSync(
      ledgerPath,
      rows.map((r) => JSON.stringify(r)).join("\n") + "\n",
    );
  };
  const parsed = lines.map((l) => {
    try {
      return JSON.parse(l);
    } catch {
      return null;
    }
  });

  if (i < 0) {
    parsed.push(rec);
    writeSorted(parsed.filter(Boolean));
    return "appended";
  }
  const prev = parsed[i] || {};
  // 이미 정렬 검증된 레코드가 있으면 그대로 둔다(재실행 멱등).
  if (prev.date_aligned) return false;
  parsed[i] = rec;
  writeSorted(parsed.filter(Boolean));
  return "upgraded";
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

// 유니버스 버전을 레코드에 박는다.
// 종목을 늘리면 그 전후 표본은 성격이 다르다(신호 발생률·산업 구성이 바뀐다).
// 표식 없이 섞으면 나중에 "이 구간은 몇 종목이었나"를 복원할 수 없다 —
// 장중 오염 구간을 date_aligned 로 갈라낸 것과 같은 이유다.
const UNIVERSE = JSON.parse(
  fs.readFileSync(path.join(ROOT, "universe.json"), "utf8"),
);

const rec = {
  as_of: data.as_of,
  logged_at: new Date().toISOString(),
  universe_version: UNIVERSE.version || null,
  universe_size: (UNIVERSE.stocks || []).length,
  // 2026-08-07 이후 수집기는 종목 시계열을 지수 날짜와 '날짜 기준'으로 맞춘다(위치 기준 아님).
  // 이 표식이 있는 레코드만 settle.js 가 정본 OOS 표본으로 센다. 이전 구간은 격리된다.
  date_aligned: true,
  // 종목 신호만 기록(실제 매매 대상). 지수 신호는 컨텍스트라 제외.
  signals: signals
    .filter((s) => s.scope !== "KOSPI" && s.scope !== "KOSDAQ")
    .map((s) => ({
      scope: s.scope,
      ticker: s.ticker || null, // 원장이 외부 매핑 없이 스스로 정산 가능하도록
      tier: s.tier,
      z: +s.z.toFixed(2),
      conf: s.conf,
      dir: s.title.includes("매수") ? 1 : s.title.includes("매도") ? -1 : 0,
    })),
};
/* 소급 기록 차단 (2026-08-19 사고).
   유니버스를 5→10종목으로 넓힌 날, 로컬에서 collector+ledger 를 돌렸더니 전날(08-18)
   신호가 '새 유니버스'로 다시 계산돼 원장에 들어갔다. 그날의 정규 실행(Actions)은 이미
   그 시점 유니버스(5종목)로 08-18 을 기록해 둔 상태였다.

   이건 가격을 미리 본 것은 아니지만, **종목 선정을 그날 이후에 하고 그날을 기록한 것**이다.
   원장의 존재 이유가 "기록 시점에 미래를 몰랐다" 하나인데 그 축이 무너진다.
   → as_of 가 유니버스 확정일보다 앞서면 적재하지 않는다. 원장은 앞으로만 자란다. */
if (rec.universe_version && rec.as_of < rec.universe_version) {
  console.log(
    `· 원장: ${rec.as_of} 는 유니버스 확정일(${rec.universe_version})보다 앞섬 — 소급 기록 차단.\n` +
      "  새 유니버스는 확정일 이후 거래일부터 기록된다(과거를 새 종목 구성으로 다시 쓰지 않는다).",
  );
  process.exit(0);
}

const result = appendIfNew(rec);
if (result === "appended") {
  console.log(
    `✓ 엣지 원장 적재: ${data.as_of} · 종목신호 ${rec.signals.length}건`,
  );
} else if (result === "upgraded") {
  console.log(
    `✓ 엣지 원장 승격: ${data.as_of} · 정렬 미검증 레코드를 확정본으로 교체 (신호 ${rec.signals.length}건)`,
  );
} else {
  console.log("· 원장: " + data.as_of + " 이미 확정 적재됨(건너뜀)");
}
