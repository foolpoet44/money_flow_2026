/* =========================================================
   자금 조류 · 리서치 종합·추천 엔진 (research_digest.js)
   ─────────────────────────────────────────────────────────
   리포트 목록(window.RESEARCH)과 수급 신호(SIGNALS)를 입력받아
   ① 일단위 종합(테마 빈도·카테고리 분포·리포트 톤)과
   ② "전문을 읽어볼 만한" 추천 리포트를 설명 가능한 점수로 산출한다.

   왜 순수함수인가: 신호 엔진(signal_engine.js)과 같은 이유다. 추천을 AI
   블랙박스로 "골랐다"고 하지 않고, 입력→출력이 결정적인 점수 규칙으로 만들어
   '왜 추천되는가'를 이유(reasons)로 노출하고 테스트로 못박는다.

   핵심 아이디어 — 수급 신호와의 교차: 오늘 외국인·기관이 극단적으로 사고판
   종목(SIGNALS)에 마침 리포트가 나왔다면, 그것이 1순위 정독 대상이다.
   "사실(수급)"과 "해석(리서치)"이 겹치는 지점이 가장 정보가 많다.
   ========================================================= */
(function (global) {
  "use strict";

  // 도메인 테마 키워드 — 제목·요약에서 빈도를 세어 "오늘 무엇이 화두인가"를 본다.
  var THEMES = [
    "반도체",
    "HBM",
    "메모리",
    "DRAM",
    "낸드",
    "파운드리",
    "AI",
    "실적",
    "어닝",
    "목표주가",
    "금리",
    "환율",
    "수출",
    "관세",
    "2차전지",
    "배터리",
    "바이오",
    "방산",
    "조선",
    "자동차",
    "밸류업",
    "배당",
    "엔비디아",
    "로봇",
    "원전",
    "전력",
    "데이터센터",
  ];
  // 리포트 '톤' 근사 — 긍정/부정 키워드 빈도(정밀 감성분석 아님, 어디까지나 근사).
  var BULL = [
    "매수",
    "상향",
    "긍정",
    "반등",
    "수혜",
    "성장",
    "턴어라운드",
    "확대",
    "개선",
    "호조",
    "최선호",
    "강세",
  ];
  var BEAR = [
    "매도",
    "하향",
    "부진",
    "우려",
    "둔화",
    "리스크",
    "약세",
    "축소",
    "비중축소",
    "하락",
  ];

  function countKeywords(reports, list) {
    var blob = reports
      .map(function (r) {
        return (r.title || "") + " " + (r.summary || "");
      })
      .join(" ");
    return list
      .map(function (k) {
        return { k: k, n: blob.split(k).length - 1 };
      })
      .filter(function (x) {
        return x.n > 0;
      })
      .sort(function (a, b) {
        return b.n - a.n;
      });
  }

  // signals(=buildSignals 출력)에서 종목명→신호 매핑. scope가 종목 신호는 종목명이다.
  function signalsByStock(signals) {
    var map = {};
    (signals || []).forEach(function (s) {
      // 같은 종목에 여러 신호면 더 강한 등급을 남긴다
      var rank = { critical: 0, alert: 1, watch: 2 };
      if (!map[s.scope] || rank[s.tier] < rank[map[s.scope].tier])
        map[s.scope] = s;
    });
    return map;
  }

  // 한 리포트의 정독 가치 점수 + 이유. 규칙은 모두 투명하다.
  function scoreReport(r, sigMap) {
    var score = 0;
    var reasons = [];
    var sig = r.stock && sigMap[r.stock];
    if (sig) {
      if (sig.tier === "critical") {
        score += 80;
        reasons.push("🔴 수급 Critical 신호 종목");
      } else if (sig.tier === "alert") {
        score += 65;
        reasons.push("🟠 수급 Alert 신호 종목");
      } else {
        score += 40;
        reasons.push("수급 관찰 종목");
      }
    }
    if (r.sector) {
      score += 20;
      reasons.push("반도체 섹터 연관");
    }
    if (r.category === "market") {
      score += 15;
      reasons.push("시황 — 전체 맥락");
    }
    var vpts = Math.min(20, Math.round((r.views || 0) / 500));
    score += vpts;
    if (vpts >= 8) reasons.push("조회수 상위");
    if (r.summary) score += 10; // 읽을 본문 요약이 있으면 가점
    if (r.target) {
      score += 8;
      reasons.push("목표가 " + r.target);
    }
    return { report: r, score: score, reasons: reasons };
  }

  function buildDigest(research, signals) {
    var reports = (research && research.reports) || [];
    var sigMap = signalsByStock(signals);

    // ── ① 일단위 종합 ──
    var byCat = {};
    reports.forEach(function (r) {
      byCat[r.category_label] = (byCat[r.category_label] || 0) + 1;
    });
    var themes = countKeywords(reports, THEMES).slice(0, 6);
    var bull = countKeywords(reports, BULL).reduce(function (a, x) {
      return a + x.n;
    }, 0);
    var bear = countKeywords(reports, BEAR).reduce(function (a, x) {
      return a + x.n;
    }, 0);
    var tone = bull > bear ? "매수 우위" : bear > bull ? "신중 우위" : "중립";

    // 수급 신호 ∩ 리서치: 신호가 뜬 종목 중 리포트가 함께 나온 것
    var flowStocks = [];
    reports.forEach(function (r) {
      if (r.stock && sigMap[r.stock] && flowStocks.indexOf(r.stock) < 0)
        flowStocks.push(r.stock);
    });

    // ── ② 정독 추천 ──
    var scored = reports
      .map(function (r) {
        return scoreReport(r, sigMap);
      })
      .sort(function (a, b) {
        return b.score - a.score;
      });
    var recommendations = scored.slice(0, 3).filter(function (x) {
      return x.score > 0;
    });

    return {
      as_of: research && research.as_of,
      total: reports.length,
      byCat: byCat,
      themes: themes,
      tone: { label: tone, bull: bull, bear: bear },
      flowLinkStocks: flowStocks,
      recommendations: recommendations,
    };
  }

  var api = {
    buildDigest: buildDigest,
    scoreReport: scoreReport,
    countKeywords: countKeywords,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(global, api);
})(typeof window !== "undefined" ? window : globalThis);
