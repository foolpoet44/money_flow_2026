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

# 4) 다이제스트 생성 + (옵션) 텔레그램 전송
log "다이제스트 생성"
node scripts/digest.js | tee -a "$LOG"

# 5) 대시보드 발행 (GitHub Pages) — 실패해도 치명적 아님(로컬 화면은 정상)
log "대시보드 발행 (GitHub Pages)"
if ! scripts/publish.sh >>"$LOG" 2>&1; then
  log "⚠ 발행 실패(푸시 권한/네트워크) — Pages만 미갱신, 로컬·텔레그램은 정상"
fi

log "── 데일리 사이클 완료 ──"
