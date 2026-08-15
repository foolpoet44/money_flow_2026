#!/usr/bin/env bash
# =========================================================
# 자금 조류 · 데일리 1회 실행 파이프라인 (scripts/run_daily.sh)
# ---------------------------------------------------------
# 수집(수급+리서치) → 신호 엔진 테스트 게이트 → 다이제스트(→ 텔레그램 옵션).
# cron / launchd 진입점. 한국시간 아침 1회 실행을 상정한다.
#
# 실행:  scripts/run_daily.sh
# 로그:  scripts/run_daily.log (append)
#
# 텔레그램을 켜려면 cron/launchd 환경에 아래 두 변수를 주입한다(opt-in):
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# 없으면 다이제스트는 출력만 하고 전송하지 않는다.
# =========================================================
set -euo pipefail

# 스크립트 위치 기준으로 리포 루트로 이동 (cron의 CWD에 흔들리지 않게)
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG="$ROOT/scripts/run_daily.log"

# 비밀키(텔레그램 토큰 등)는 커밋하지 않는 로컬 env 파일에서 로드한다(있으면).
# 수동 실행과 launchd 실행이 같은 비밀을 공유하게 하는 단일 출처.
if [ -f "$ROOT/scripts/run_daily.env" ]; then
  set -a
  . "$ROOT/scripts/run_daily.env"
  set +a
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "── 데일리 사이클 시작 ──"

# 0) 수집기 가드 테스트 (네트워크 불필요 · 순수 로직)
#    장중 캔들 차단과 종목-지수 날짜 정렬을 먼저 못박는다. 두 가드는 2026-08-07 사고
#    (개장 후 수집으로 장중 스냅샷이 종가로 둔갑, 종목 시계열이 지수와 한 세션 어긋남)의
#    재발 방지 장치다. 깨진 가드로 수집을 시작하면 원장이 다시 오염된다.
log "수집기 가드 테스트"
python3 collector/guard.test.py >>"$LOG" 2>&1

# 0-2) 데일리 사이클 리플레이 (네트워크 불필요 · git 히스토리 픽스처)
#    guard.test.py 는 순수 함수 단위를 못박지만, "수집기 전체가 장중에 돌면 무슨 일이
#    벌어지는가"는 단위 테스트로 표현되지 않는다. 2026-08-07 사고가 정확히 그 틈에서 났다
#    — 개별 함수는 다 정상이었고 조합과 실행 시각이 문제였다. 그 통합 테스트가 그동안
#    수동 전용이라 아무도 안 돌렸다. 가장 비싼 사고를 막는 테스트를 게이트에 세운다.
#    수집보다 먼저 돌린다 — 리플레이는 끝에서 data.json/dashboard/data.js 를 지운다.
log "데일리 사이클 리플레이 테스트"
python3 collector/replay.test.py >>"$LOG" 2>&1

# 1) 수급·지수 수집 (실패 시 중단 — 직전 data.js가 화면에 남는다)
log "수급 수집 (collector.py)"
python3 collector/collector.py >>"$LOG" 2>&1

# 2) 리서치 수집 (실패해도 치명적 아님 — 다이제스트는 리서치 없이도 동작)
log "리서치 수집 (research_collector.py)"
if ! python3 collector/research_collector.py >>"$LOG" 2>&1; then
  log "⚠ 리서치 수집 실패 — 리서치 없이 진행"
fi

# 3) 신호 엔진 테스트 게이트 (검증 안 된 엔진으로 알림 쏘는 사고 방지)
log "신호 엔진 테스트"
node dashboard/signal_engine.test.js >>"$LOG" 2>&1

# 4) 엣지 원장 적재 — 오늘 신호를 미리 기록(out-of-sample 누적). 실패 비치명적.
log "엣지 원장 적재"
node backtest/ledger.js >>"$LOG" 2>&1 || log "⚠ 원장 적재 실패(비치명적)"

# 5) 원장 정산 재판정(OOS) — 과거 신호에 실현수익 결합. 가격 조회. 비치명적.
#    다이제스트보다 먼저 돌려 '당일' OOS 현황(edge_status.json)을 반영한다.
log "원장 정산 재판정(OOS)"
node backtest/settle.js >>"$LOG" 2>&1 || log "⚠ 정산 실패(비치명적)"

# 6) 다이제스트 생성 + (옵션) 텔레그램 전송 — OOS 누적 현황 한 줄 포함
log "다이제스트 생성"
node scripts/digest.js | tee -a "$LOG"

# 7) 대시보드 발행 (GitHub Pages)
#    무인 클라우드 실행에서는 '비치명적'이 아니다 — 러너는 빈 디스크로 시작하므로
#    푸시가 안 되면 오늘 적재한 OOS 원장 한 줄과 시계열 원장이 러너와 함께 사라진다.
#    그래서 실패를 로그로만 넘기지 않고 잡을 빨갛게 만든다(실패 알림이 뜬다).
log "대시보드 발행 (GitHub Pages)"
if ! scripts/publish.sh >>"$LOG" 2>&1; then
  log "✗ 발행 실패 — 이 실행의 원장 적재분이 유실됩니다(수동 확인 필요)"
  exit 1
fi

log "── 데일리 사이클 완료 ──"
