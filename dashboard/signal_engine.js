/* =========================================================
   자금 조류 · 신호 엔진 (signal_engine.js)
   ─────────────────────────────────────────────────────────
   CLAUDE.md §5 명세의 정본 구현. 순수함수만 모았다 — DOM·전역 상태에
   의존하지 않으므로 브라우저(대시보드)와 Node(단위 테스트)가 같은 코드를 공유한다.

   왜 분리했나: 신호 판정 로직이 화면 렌더 코드와 섞여 있으면 "등급이 맞는가"를
   독립적으로 검증할 수 없다. 로직을 떼어내 테스트로 못박아야, 이후 자동화(Sprint 4)가
   무인으로 돌 때 틀린 신호를 매일 푸시하는 사고를 막는다.
   ========================================================= */
(function (global) {
  "use strict";

  // ── 임계치 상수 (단일 출처 · 튜닝 대상) ────────────────
  // §5.3 등급 경계. 한 곳에서만 관리하고 buildSignals/tierOf가 이를 참조한다.
  var Z_ALERT = 1.2; // |z| 이 값 이상이면 최소 ALERT
  var Z_CRITICAL = 2.0; // |z| 이 값 이상이면 CRITICAL
  var STOCK_MIN_Z = 1.2; // 종목 신호 노이즈 컷 — 이 미만이면 신호 생성 안 함

  // ── z-score (시계열 자기참조) §5.1 ─────────────────────
  function stats(arr) {
    // population mean/sd. sd가 0이면 1로 둔다(0 나눗셈 방지).
    var m =
      arr.reduce(function (a, b) {
        return a + b;
      }, 0) / arr.length;
    var sd =
      Math.sqrt(
        arr.reduce(function (a, b) {
          return a + (b - m) ** 2;
        }, 0) / arr.length,
      ) || 1;
    return { m: m, sd: sd };
  }
  function zlast(arr) {
    // 오늘(마지막)을 제외한 과거 분포 대비, 오늘값이 얼마나 극단인가
    var s = stats(arr.slice(0, -1));
    return (arr[arr.length - 1] - s.m) / s.sd;
  }

  // ── 표시 포맷 (부호 + 천단위) ──────────────────────────
  function fmt(n) {
    var s = n >= 0 ? "+" : "−"; // 유니코드 마이너스(−)로 폭 안정
    return s + Math.abs(Math.round(n)).toLocaleString();
  }
  function fmtP(n) {
    return Math.abs(Math.round(n)).toLocaleString();
  }

  // ── 등급 판정 §5.3 ─────────────────────────────────────
  function tierOf(absz, confluence) {
    if (absz >= Z_CRITICAL || confluence >= 3) return "critical";
    if (absz >= Z_ALERT || confluence >= 2) return "alert";
    return "watch";
  }

  // ── 신호 생성 §5.4 ─────────────────────────────────────
  // data = data.json 스키마(§4). 전역에 의존하지 않고 인자로 받는다.
  function buildSignals(data) {
    var sigs = [];

    // 지수 레벨 신호 (KOSPI/KOSDAQ): 외국인·기관·종가 3조류 confluence
    ["KOSPI", "KOSDAQ"].forEach(function (mkt) {
      var d = data.index[mkt];
      var n = d.foreign.length; // 시계열 길이를 데이터에서 직접 도출(전역 N 제거)
      var zf = zlast(d.foreign);
      var zi = zlast(d.institution);
      var fDir = Math.sign(d.foreign[n - 1]);
      var iDir = Math.sign(d.institution[n - 1]);
      var closeDir = Math.sign(d.close[n - 1] - d.close[n - 2]);
      var conf = 1;
      if (fDir === iDir && fDir !== 0) conf++; // 외국인·기관 정렬
      if (fDir === closeDir && fDir !== 0) conf++; // 수급·가격 정렬(지수 한정)
      var az = Math.max(Math.abs(zf), Math.abs(zi));
      var tier = tierOf(az, conf);
      var dir = d.foreign[n - 1] + d.institution[n - 1] >= 0;
      sigs.push({
        tier: tier,
        scope: mkt,
        z: az,
        conf: conf,
        title: (
          mkt +
          " " +
          (dir ? "쌍끌이 유입" : "동반 이탈") +
          " " +
          (conf >= 3 ? "(3조류 정렬)" : conf >= 2 ? "(2조류 정렬)" : "")
        ).trim(),
        desc:
          "외국인 " +
          fmt(d.foreign[n - 1]) +
          "억(z=" +
          zf.toFixed(1) +
          "), 기관 " +
          fmt(d.institution[n - 1]) +
          "억(z=" +
          zi.toFixed(1) +
          "). 지수 종가 " +
          (closeDir >= 0 ? "상승" : "하락") +
          " 동반.",
        tags: ["외국인", "기관", "지수종가"].slice(0, conf + 0),
        tagsFull: [
          "외국인 수급",
          "기관 수급",
          closeDir >= 0 ? "종가 상승" : "종가 하락",
        ],
      });
    });

    // 종목 극단 신호: |zlast(외국인)| >= STOCK_MIN_Z 일 때만(노이즈 컷)
    data.sector.stocks.forEach(function (s) {
      var n = s.foreign.length;
      var zf = zlast(s.foreign);
      if (Math.abs(zf) >= STOCK_MIN_Z) {
        var fDir = Math.sign(s.foreign[n - 1]);
        var iDir = Math.sign(s.institution[n - 1]);
        var conf = 1;
        if (fDir === iDir && fDir !== 0) conf++; // 종목은 가격 미반영, 최대 2
        var tier = tierOf(Math.abs(zf), conf);
        sigs.push({
          tier: tier,
          scope: s.name,
          z: Math.abs(zf),
          conf: conf,
          title:
            s.name +
            " 외국인 " +
            (s.foreign[n - 1] >= 0 ? "집중 매수" : "집중 매도"),
          desc:
            "외국인 " +
            fmt(s.foreign[n - 1]) +
            "억 — 30일 평균 대비 z=" +
            zf.toFixed(1) +
            ". 기관은 " +
            fmt(s.institution[n - 1]) +
            "억 " +
            (conf >= 2 ? "동조" : "역행") +
            ".",
          tagsFull: [
            "외국인 수급",
            conf >= 2 ? "기관 동조" : "기관 역행",
            s.ticker,
          ],
        });
      }
    });

    // 정렬: tier 우선(critical→alert→watch), 동급 내 z 내림차순
    var order = { critical: 0, alert: 1, watch: 2 };
    sigs.sort(function (a, b) {
      return order[a.tier] - order[b.tier] || b.z - a.z;
    });
    return sigs;
  }

  // ── 내보내기 (브라우저 전역 + Node 모듈 동시 지원) ──────
  var api = {
    Z_ALERT: Z_ALERT,
    Z_CRITICAL: Z_CRITICAL,
    STOCK_MIN_Z: STOCK_MIN_Z,
    stats: stats,
    zlast: zlast,
    fmt: fmt,
    fmtP: fmtP,
    tierOf: tierOf,
    buildSignals: buildSignals,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node: require("./signal_engine.js")
  } else {
    Object.assign(global, api); // 브라우저: 전역 함수로 노출(대시보드가 그대로 호출)
  }
})(typeof window !== "undefined" ? window : globalThis);
