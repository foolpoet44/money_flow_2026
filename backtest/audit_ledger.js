/* =========================================================
   자금 조류 · OOS 원장 감사 (backtest/audit_ledger.js)
   ─────────────────────────────────────────────────────────
   왜 이 파일이 있는가 (2026-08-07 조사 기록)

   원장(ledger.jsonl)은 이 프로젝트가 "가장 정직한 증거"로 삼는 자료다. 그런데 조사 결과
   2026-08-07 이전 구간은 두 가지로 훼손돼 있었다:

     ① 장중 스냅샷 오염 — GitHub Actions 의 정시 큐 혼잡으로 수집이 매번 54~71분 밀려
        개장(09:00)선을 넘나들었다. 개장 후에 돈 날은 '진행 중인 장중 캔들'이 완결된
        거래일로 둔갑했다(외국인 순매수 최대 19.6배 차이, 2026-08-04 은 부호 반전).
        appendIfNew 가 선착순이라 그 값이 먼저 박히고 익일 확정치는 기각됐다.
        → 30건 중 15건이 이 경로로 만들어졌고, 거래일 6일은 아예 적재되지 못했다.

     ② 종목–지수 날짜 어긋남 — 예전 collector 는 종목 trend 의 마지막 30행을 지수 날짜와
        '위치로' 짝지었다. 두 피드는 별개 엔드포인트라 최신 행이 같은 세션이라는 보장이 없다.
        측정 결과 2026-06-14 시드(장 마감 후 수동 실행)와 이후 자동 아침 실행이 정확히
        한 세션 어긋나 있었다(오프셋 +1, 26/26). 시드 쪽이 정렬 기준임은 확실하다
        — 마감 후 실행이라 지수·종목 두 피드 모두 당일이 확정돼 있었기 때문이다.

   그래서 왜 '고치지 않고' 격리하는가:
     ②의 보정을 자동 스냅샷 전체에 일괄 적용해 보면 시드와 140/140 완전히 일치한다.
     여기까지는 복원 가능해 보인다. 그러나 자동 스냅샷끼리도 (장중 관측을 제외하고도)
     325건 중 290건이 서로 갈린다 — 즉 오프셋이 상수가 아니라 실행 시각·게시 지연에 따라
     매번 달라진다. 스냅샷별 오프셋을 '추정'해 되맞출 수는 있지만, 그렇게 복원한 원장은
     추정 절차 위에 세운 증거다. 그건 §1.5 가 금지한 거짓 정밀도이고, 하필 이 원장의
     존재 이유(과최적화 불가능한 정직한 증거)를 정면으로 훼손한다.

     → 결론: 다시 쓰지 않는다. 신호는 손대지 않고 date_aligned:false 표식만 남겨 격리하고,
       2026-08-07 수집기 수정 이후분(date_aligned:true)부터 정본 OOS 표본으로 센다.
       settle.js 가 두 코호트를 분리 보고하므로, 훼손된 표본이 깨끗한 증거로 둔갑하지 않는다.
       증거의 시계를 다시 시작하는 편이, 복원한 척하는 것보다 정직하다.

   실행:
     node backtest/audit_ledger.js              진단만 (파일 변경 없음)
     node backtest/audit_ledger.js --annotate   기존 레코드에 date_aligned:false 표식 기입
   ========================================================= */
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const ledgerPath = path.join(__dirname, "ledger.jsonl");
const ANNOTATE = process.argv.includes("--annotate");
const SESSION_CLOSE_MIN = 15 * 60 + 40; // 15:40 KST

function git(cmd) {
  return execSync(cmd, { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 28 });
}

function loadSnapshots() {
  const out = [];
  for (const line of git('git log --reverse --format="%H %cI" -- docs/data.js')
    .trim()
    .split("\n")) {
    if (!line.trim()) continue;
    const [sha, iso] = line.trim().split(/\s+/);
    let raw;
    try {
      raw = git(`git show ${sha}:docs/data.js`);
    } catch {
      continue;
    }
    const i = raw.indexOf("{");
    if (i < 0) continue;
    let data;
    try {
      data = JSON.parse(raw.slice(i).trim().replace(/;$/, ""));
    } catch {
      continue;
    }
    const kst = new Date(new Date(iso).getTime() + 9 * 3600 * 1000);
    out.push({
      sha,
      observedDate: kst.toISOString().slice(0, 10),
      observedMin: kst.getUTCHours() * 60 + kst.getUTCMinutes(),
      observedKST: kst.toISOString().slice(0, 16).replace("T", " "),
      data,
    });
  }
  return out;
}

const isConfirmed = (s, d) =>
  s.observedDate > d
    ? true
    : s.observedDate < d
      ? false
      : s.observedMin >= SESSION_CLOSE_MIN;

// ── 진단 ─────────────────────────────────────────────────
const snaps = loadSnapshots();
const records = fs
  .readFileSync(ledgerPath, "utf8")
  .trim()
  .split("\n")
  .filter(Boolean)
  .map(JSON.parse);

console.log("════════════════════════════════════════════════════");
console.log(" OOS 원장 감사 · 2026-08-07 이전 구간");
console.log("════════════════════════════════════════════════════");
console.log(
  `발행본 스냅샷 ${snaps.length}개 · 원장 레코드 ${records.length}건`,
);

// ① 장중 오염: 레코드가 '그 세션 마감 전'에 기록됐는가
let intraday = 0;
for (const r of records) {
  if (!r.logged_at) continue;
  const kst = new Date(new Date(r.logged_at).getTime() + 9 * 3600 * 1000);
  const d = kst.toISOString().slice(0, 10);
  const min = kst.getUTCHours() * 60 + kst.getUTCMinutes();
  if (d < r.as_of || (d === r.as_of && min < SESSION_CLOSE_MIN)) intraday++;
}

// 결손: 확정 거래일 중 원장에 없는 날
const tradingDays = new Set();
for (const s of snaps)
  for (const d of s.data.index.KOSPI.dates)
    if (isConfirmed(s, d)) tradingDays.add(d);
const all = [...tradingDays].sort();
const have = new Set(records.map((r) => r.as_of));
const first = records[0].as_of;
const lastD = all[all.length - 1];
const missing = all.filter((d) => d >= first && d <= lastD && !have.has(d));

console.log(
  `\n① 장중 스냅샷으로 만들어진 레코드 : ${intraday}/${records.length}건`,
);
console.log(
  `   선착순 기각으로 결손된 거래일    : ${missing.length}일  ${missing.join(", ")}`,
);

// ② 종목–지수 정렬: 시드 vs 자동 오프셋, 그리고 자동끼리의 일관성
const seed = snaps[0];
const autos = snaps.slice(1);
const sd = seed.data.index.KOSPI.dates;
let match = 0,
  mismatch = 0;
for (const st of seed.data.sector.stocks) {
  sd.forEach((d, i) => {
    for (const s of autos) {
      const ad = s.data.index.KOSPI.dates;
      const j = ad.indexOf(d);
      if (j < 0 || !isConfirmed(s, d)) continue;
      const a = s.data.sector.stocks.find((x) => x.ticker === st.ticker);
      if (j + 1 < ad.length)
        a.foreign[j + 1] === st.foreign[i] ? match++ : mismatch++;
      break;
    }
  });
}
const obs = {};
for (const s of autos) {
  const dates = s.data.index.KOSPI.dates;
  for (const st of s.data.sector.stocks)
    dates.forEach((dt, i) => {
      if (!isConfirmed(s, dt)) return;
      (obs[st.ticker + "|" + dt] = obs[st.ticker + "|" + dt] || []).push(
        st.foreign[i],
      );
    });
}
const keys = Object.keys(obs);
const split = keys.filter((k) => new Set(obs[k]).size > 1).length;

console.log(
  `\n② 자동 라벨 -1 보정 후 시드와 일치  : ${match}건 / 불일치 ${mismatch}건`,
);
console.log(
  `   자동 스냅샷끼리 값이 갈리는 조합   : ${split}/${keys.length}건`,
);
console.log(
  split > keys.length / 2
    ? "   → 오프셋이 상수가 아니다. 스냅샷별 추정 없이는 복원 불가 ⇒ 격리가 정답."
    : "   → 오프셋이 상수에 가깝다. 복원 가능성 재검토 여지 있음.",
);

console.log("\n── 판단 ────────────────────────────────────────────");
console.log(
  "이 구간은 신호를 다시 계산하지 않는다. 어떤 재계산도 '스냅샷별 정렬 오프셋 추정'",
);
console.log(
  "위에 서게 되고, 그렇게 만든 원장은 정직한 증거가 아니라 정교해 보이는 추정치다.",
);
console.log(
  "date_aligned:false 로 격리하고, 수집기 수정 이후분부터 정본 표본으로 센다.",
);

if (!ANNOTATE) {
  console.log(
    "\n· 진단만 수행했습니다. 표식을 기입하려면 --annotate 를 붙이세요.",
  );
  process.exit(0);
}

let touched = 0;
const out = records.map((r) => {
  if (r.date_aligned === undefined) {
    touched++;
    return { ...r, date_aligned: false };
  }
  return r;
});
fs.writeFileSync(
  ledgerPath,
  out.map((r) => JSON.stringify(r)).join("\n") + "\n",
);
console.log(
  `\n✓ ${touched}건에 date_aligned:false 표식 기입 (신호는 손대지 않음)`,
);
console.log("· settle.js 가 이 표식을 보고 legacy 코호트로 분리 집계한다.");
