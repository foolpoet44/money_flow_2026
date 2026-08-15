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

// 종목명 → 티커 (원장은 종목명으로 기록되므로 가격 조회용 매핑).
// 목록은 universe.json 단일 출처에서 뒤집어 만든다 — 예전엔 여기에 5종목이 또 박혀 있어,
// 유니버스를 늘리면 정산만 조용히 옛 종목을 보고 있었다.
const TICKER = Object.fromEntries(
  JSON.parse(
    fs.readFileSync(path.resolve(__dirname, "..", "universe.json"), "utf8"),
  ).stocks.map((s) => [s.name, s.ticker]),
);

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

  // 코호트 3분할 (2026-08-07 감사 결과):
  //   live   — 수집기 수정 이후. 종목 시계열이 지수 날짜와 '날짜 기준'으로 정렬됨. 정본.
  //   legacy — 수정 이전. 장중 스냅샷 오염 + 종목-지수 ±1 세션 어긋남이 섞여 있어 격리.
  //            (audit_ledger.js 참조 — 오프셋이 상수가 아니라 복원 불가로 판정)
  //   seeded — history 재생. in-sample 참고용.
  // 격리분을 정본에 섞으면 훼손된 표본이 깨끗한 증거로 둔갑한다. 절대 합치지 않는다.
  const store = {
    seeded: emptyBuckets(),
    live: emptyBuckets(),
    legacy: emptyBuckets(),
  };
  const counts = {
    seeded: { settled: 0, pending: 0, expired: 0 },
    live: { settled: 0, pending: 0, expired: 0 },
    legacy: { settled: 0, pending: 0, expired: 0 },
  };

  for (const rec of records) {
    const mode = rec.seeded ? "seeded" : rec.date_aligned ? "live" : "legacy";
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
    "LIVE (날짜정렬 검증 · OUT-OF-SAMPLE 정본)",
    store.live,
    baseline,
    counts.live,
  );
  report(
    "LEGACY (2026-08-07 이전 · 격리 — 증거로 쓰지 말 것)",
    store.legacy,
    baseline,
    counts.legacy,
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
    "· LEGACY는 장중 스냅샷 오염(15/30건)과 종목-지수 ±1 세션 어긋남이 섞인 구간이다.",
  );
  console.log(
    "  복원 불가로 판정해 격리했다(backtest/audit_ledger.js). 판정 근거로 인용하지 않는다.",
  );
  console.log(
    "· SEEDED는 backtest.js와 같은 데이터라 결과가 비슷해야 정상(정산 로직 검증).",
  );
  console.log(
    "· 미정산은 시간이 지나면 자동 정산된다. 라이브 표본이 쌓일수록 신뢰도↑.",
  );

  // 다이제스트/봇이 읽을 라이브 OOS 상태 요약 기록(누적을 매일 눈에 보이게)
  const status = {
    updated_for: records.length
      ? records
          .map((r) => r.as_of)
          .sort()
          .slice(-1)[0]
      : null,
    live_records: records.filter((r) => !r.seeded && r.date_aligned).length,
    quarantined_records: records.filter((r) => !r.seeded && !r.date_aligned)
      .length,
    // 격리 구간의 마지막 기준일. 소비자(대시보드·다이제스트)가 문구를 데이터에서 만들도록 노출한다.
    // 예전엔 '2026-08-07'이 HTML·digest 양쪽에 문자열로 박혀 있었고, 그 날짜 레코드 자신이
    // 격리 대상이라 "…이전"이라는 표현이 사실과 어긋났다.
    quarantine_through:
      records
        .filter((r) => !r.seeded && !r.date_aligned)
        .map((r) => r.as_of)
        .sort()
        .slice(-1)[0] || null,
    settled: counts.live.settled,
    pending: counts.live.pending,
    // 대시보드·다이제스트가 "표본 없음"과 "엣지 없음"을 혼동하지 않도록 상태를 명시한다.
    cohort_note:
      "2026-08-07 이전 원장은 장중 오염·정렬 어긋남으로 격리됨(legacy). 아래 수치는 정렬 검증분만.",
    horizons: {},
  };
  HORIZONS.forEach((h) => {
    const arr = store.live.ALL[h];
    const bm = baseline[h].length ? mean(baseline[h]) : 0;
    status.horizons[h] = {
      n: arr.length,
      meanPct: arr.length ? +(mean(arr) * 100).toFixed(2) : null,
      verdict: verdict(arr, bm),
    };
  });
  fs.writeFileSync(
    path.join(__dirname, "edge_status.json"),
    JSON.stringify(status, null, 2),
  );
}

main().catch((e) => {
  console.error("✗ 정산 실패:", e.message);
  process.exit(1);
});
