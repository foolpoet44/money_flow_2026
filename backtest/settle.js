/* =========================================================
   자금 조류 · 원장 정산·재판정 (backtest/settle.js)
   ─────────────────────────────────────────────────────────
   ledger.jsonl 에 적재된 과거 신호에 '사후' 실현수익을 결합해 재판정한다.
   백테스트(backtest.js)가 과거를 들여다보며 규칙을 맞췄을 위험이 있는 반면,
   원장은 '기록 시점에 미래를 몰랐다'가 보장된다 — 가장 정직한 증거.

   방법: 각 신호일(as_of)에 대해 네이버 종목 시세에서 '익일 종가 진입 → h거래일
   뒤 종가 청산' 수익을 구하고, 신호 방향(dir)과 왕복비용(COST)을 반영한다.
   stats.js의 동일 잣대로 등급×기간별 판정한다.

   seeded(history 시딩, in-sample) 와 live(실제 적재, out-of-sample)를 분리 보고한다.
   live가 정본이다. 아직 청산일이 안 된 신호는 '미정산(pending)'으로 솔직히 센다.

   실행:  node backtest/settle.js
   ========================================================= */
const fs = require("fs");
const path = require("path");
const https = require("https");
const S = require(path.join(__dirname, "stats.js"));

const { HORIZONS, COST } = S.CONFIG;
const { mean, tstat, pct, verdict } = S;

// 종목명 → 티커 (원장은 종목명으로 기록되므로 가격 조회용 매핑)
const TICKER = {
  삼성전자: "005930",
  SK하이닉스: "000660",
  삼성전기: "009150",
  SK스퀘어: "402340",
  한미반도체: "042700",
};

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
  Referer: "https://m.stock.naver.com/",
};

function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: HEADERS }, (r) => {
        let b = "";
        r.on("data", (d) => (b += d));
        r.on("end", () => {
          try {
            resolve(JSON.parse(b));
          } catch (e) {
            reject(e);
          }
        });
      })
      .on("error", reject);
  });
}

const ymd = (s) => String(s).replace(/-/g, ""); // '2026-06-12'→'20260612'

async function getPrices(ticker) {
  const url = `https://api.stock.naver.com/chart/domestic/item/${ticker}?periodType=dayCandle&count=110`;
  const d = await fetchJSON(url);
  const rows = d.priceInfos || []; // 과거→최신 오름차순
  const dates = rows.map((r) => String(r.localDate));
  const close = {};
  const idx = {};
  rows.forEach((r, i) => {
    close[String(r.localDate)] = Number(r.closePrice);
    idx[String(r.localDate)] = i;
  });
  return { dates, close, idx };
}

function emptyBuckets() {
  const b = { critical: {}, alert: {}, watch: {}, ALL: {} };
  HORIZONS.forEach((h) => Object.keys(b).forEach((k) => (b[k][h] = [])));
  return b;
}

function report(title, store, baseline, counts) {
  console.log(
    `\n■ ${title}  (정산 ${counts.settled} · 미정산 ${counts.pending} · 가격범위밖 ${counts.expired})`,
  );
  if (!counts.settled) {
    console.log(
      "  (정산된 신호 없음 — 아직 청산일 미도래이거나 원장이 비었음)",
    );
    return;
  }
  const pad = [6, 5, 4, 8, 7, 6, 8, 16];
  const head = ["등급", "기간", "N", "평균", "적중", "t값", "기준선", "판정"];
  console.log("  " + head.map((s, i) => s.padEnd(pad[i])).join(""));
  for (const tier of ["critical", "alert", "watch", "ALL"]) {
    for (const h of HORIZONS) {
      const arr = store[tier][h];
      if (!arr.length) continue;
      const bm = baseline[h].length ? mean(baseline[h]) : 0;
      const row = [
        tier === "ALL" ? "전체" : tier,
        h + "일",
        String(arr.length),
        pct(mean(arr)),
        (100 * arr.filter((x) => x > 0).length) / arr.length + "%",
        tstat(arr).toFixed(2),
        pct(bm),
        verdict(arr, bm),
      ];
      // 적중률 정수화
      row[4] =
        Math.round((100 * arr.filter((x) => x > 0).length) / arr.length) + "%";
      console.log("  " + row.map((s, i) => String(s).padEnd(pad[i])).join(""));
    }
  }
}

async function main() {
  const ledgerPath = path.join(__dirname, "ledger.jsonl");
  let lines;
  try {
    lines = fs
      .readFileSync(ledgerPath, "utf8")
      .trim()
      .split("\n")
      .filter(Boolean);
  } catch {
    console.error(
      "✗ ledger.jsonl 없음 — `node backtest/ledger.js` 로 적재하세요.",
    );
    process.exit(1);
  }
  const records = lines.map((l) => JSON.parse(l));

  // 필요한 종목 가격 한 번씩 조회
  const names = [
    ...new Set(records.flatMap((r) => r.signals.map((s) => s.scope))),
  ];
  const prices = {};
  for (const nm of names) {
    const tk = TICKER[nm];
    if (!tk) continue;
    prices[nm] = await getPrices(tk);
  }

  // 기준선(드리프트): 가격 차트에서 무조건 익일진입 롱 수익 풀링
  const baseline = {};
  HORIZONS.forEach((h) => (baseline[h] = []));
  for (const nm of Object.keys(prices)) {
    const P = prices[nm];
    for (let i = 0; i < P.dates.length; i++) {
      HORIZONS.forEach((h) => {
        const e = i + 1,
          x = i + 1 + h;
        if (x < P.dates.length) {
          const ret =
            (P.close[P.dates[x]] - P.close[P.dates[e]]) / P.close[P.dates[e]];
          baseline[h].push(ret);
        }
      });
    }
  }

  const store = { seeded: emptyBuckets(), live: emptyBuckets() };
  const counts = {
    seeded: { settled: 0, pending: 0, expired: 0 },
    live: { settled: 0, pending: 0, expired: 0 },
  };

  for (const rec of records) {
    const mode = rec.seeded ? "seeded" : "live";
    const a = ymd(rec.as_of);
    for (const sig of rec.signals) {
      const P = prices[sig.scope];
      if (!P) continue;
      const i = P.idx[a];
      if (i === undefined) {
        counts[mode].expired++; // as_of가 가격 조회 범위(110일) 밖
        continue;
      }
      for (const h of HORIZONS) {
        const e = i + 1,
          x = i + 1 + h;
        if (x >= P.dates.length) {
          counts[mode].pending++; // 아직 청산일 미도래
          continue;
        }
        const entry = P.close[P.dates[e]],
          exit = P.close[P.dates[x]];
        const net = (sig.dir * (exit - entry)) / entry - COST;
        store[mode][sig.tier][h].push(net);
        store[mode].ALL[h].push(net);
        counts[mode].settled++;
      }
    }
  }

  console.log("════════════════════════════════════════════════════════");
  console.log(" 자금 조류 · 원장 정산 재판정 (out-of-sample)");
  console.log("════════════════════════════════════════════════════════");
  console.log(
    `원장 레코드: ${records.length}일 · 설정 익일진입·왕복비용 ${pct(COST)}`,
  );

  report(
    "LIVE (실제 적재 · OUT-OF-SAMPLE 정본)",
    store.live,
    baseline,
    counts.live,
  );
  report(
    "SEEDED (history 시딩 · in-sample 참고/검증용)",
    store.seeded,
    baseline,
    counts.seeded,
  );

  console.log("\n── 메모 ──");
  console.log(
    "· LIVE가 정본이다. 기록 시점에 미래를 몰랐으므로 과최적화가 불가능하다.",
  );
  console.log(
    "· SEEDED는 backtest.js와 같은 데이터라 결과가 비슷해야 정상(정산 로직 검증).",
  );
  console.log(
    "· 미정산은 시간이 지나면 자동 정산된다. 라이브 표본이 쌓일수록 신뢰도↑.",
  );
}

main().catch((e) => {
  console.error("✗ 정산 실패:", e.message);
  process.exit(1);
});
