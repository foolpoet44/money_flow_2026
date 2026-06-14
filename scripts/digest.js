/* =========================================================
   자금 조류 · 데일리 다이제스트 생성기 (scripts/digest.js)
   ─────────────────────────────────────────────────────────
   data.json + research.json 을 읽어, 신호 엔진·종합 엔진으로
   "오늘의 조류 + Alert↑ 신호 + 정독 추천" 텍스트를 만들어 출력한다.
   텔레그램 전송은 토큰이 설정됐을 때만(opt-in) 일어난다.

   왜 node인가: 신호 판정(§5)·추천(§11) 로직의 정본은 이미 JS 순수함수다.
   파이썬으로 재구현해 두 벌을 만들면 진실의 원천이 갈라진다. 같은 엔진을
   재사용해 단일 출처를 지킨다.

   §8 알림 규칙: Alert 이상만 푸시, Watch 이하는 무음(전송 생략).

   실행:  node scripts/digest.js                 # 드라이런(출력만)
          TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... node scripts/digest.js  # 전송
   ========================================================= */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const E = require(path.join(ROOT, "dashboard/signal_engine.js"));
const RD = require(path.join(ROOT, "dashboard/research_digest.js"));

function loadJSON(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

const data = loadJSON(path.join(ROOT, "data.json"));
if (!data) {
  console.error(
    "✗ data.json 없음 — collector/collector.py 를 먼저 실행하세요.",
  );
  process.exit(1);
}
const research = loadJSON(path.join(ROOT, "research.json")); // 없을 수 있음(graceful)

// ── 신호 산출 ──
const signals = E.buildSignals(data);
const alertPlus = signals.filter(
  (s) => s.tier === "critical" || s.tier === "alert",
);
const tierEmoji = { critical: "🔴", alert: "🟠", watch: "⚪" };
const net = (x) => x.foreign.at(-1) + x.institution.at(-1);
const k = data.index.KOSPI,
  q = data.index.KOSDAQ;

// ── 텍스트 조립 ──
const L = [];
L.push(`📊 자금 조류 · ${data.as_of}`);
L.push("");
L.push("■ 오늘의 조류 (외국인+기관)");
L.push(
  `KOSPI ${E.fmt(net(k))}억 (외 ${E.fmt(k.foreign.at(-1))} · 기 ${E.fmt(k.institution.at(-1))})`,
);
L.push(
  `KOSDAQ ${E.fmt(net(q))}억 (외 ${E.fmt(q.foreign.at(-1))} · 기 ${E.fmt(q.institution.at(-1))})`,
);
L.push("");

if (alertPlus.length) {
  L.push(`■ 신호 ${alertPlus.length}건 (Alert 이상)`);
  alertPlus.slice(0, 6).forEach((s) => {
    L.push(
      `${tierEmoji[s.tier]} ${s.title} (z=${s.z.toFixed(1)} · ${s.conf}조류)`,
    );
  });
} else {
  L.push("■ 신호: Alert 이상 없음 (Watch 이하 — 무음)");
}

if (research && research.reports && research.reports.length) {
  const dg = RD.buildDigest(research, signals);
  L.push("");
  L.push(`■ 리서치 정독 추천 · 톤 ${dg.tone.label}`);
  if (dg.flowLinkStocks.length) {
    L.push(`(수급 신호 교차: ${dg.flowLinkStocks.join(", ")})`);
  }
  dg.recommendations.slice(0, 2).forEach((x, i) => {
    const r = x.report;
    L.push(`${i + 1}. ${r.stock ? r.stock + " " : ""}${r.title} — ${r.broker}`);
    L.push(`   ${x.reasons.join(" / ")}`);
    L.push(`   ${r.pdf || r.url}`);
  });
}
L.push("");
L.push("— 자금 조류 자동 다이제스트");

const text = L.join("\n");
console.log(text);

// ── 텔레그램 전송 (opt-in · §8 Alert 이상만) ──
const TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT = process.env.TELEGRAM_CHAT_ID;
const shouldPush = alertPlus.length > 0; // Watch 이하는 무음

if (!TOKEN || !CHAT) {
  console.log("\n(드라이런 — TELEGRAM_BOT_TOKEN/CHAT_ID 미설정, 전송 생략)");
} else if (!shouldPush) {
  console.log("\n(무음 — Alert 이상 신호 없음, 전송 생략)");
} else {
  const https = require("https");
  const payload = JSON.stringify({
    chat_id: CHAT,
    text,
    disable_web_page_preview: true,
  });
  const req = https.request(
    `https://api.telegram.org/bot${TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(payload),
      },
    },
    (res) => {
      let b = "";
      res.on("data", (d) => (b += d));
      res.on("end", () =>
        console.log(
          res.statusCode === 200
            ? "\n✓ 텔레그램 전송 완료"
            : `\n✗ 텔레그램 실패 ${res.statusCode}: ${b}`,
        ),
      );
    },
  );
  req.on("error", (e) => console.error("\n✗ 텔레그램 오류:", e.message));
  req.write(payload);
  req.end();
}
