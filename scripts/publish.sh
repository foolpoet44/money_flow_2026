#!/usr/bin/env bash
# =========================================================
# 자금 조류 · 대시보드 발행 (scripts/publish.sh)
# ---------------------------------------------------------
# dashboard/ 의 화면 + 생성된 실데이터(data.js·research.js)를 docs/ 로 복사해
# 커밋·푸시한다. GitHub Pages가 docs/ 를 서빙하므로 폰/텔레그램에서 열어볼 수 있다.
#   → https://foolpoet44.github.io/money_flow_2026/
#
# run_daily.sh 끝에서 자동 호출되어 매일 실데이터로 갱신된다.
# =========================================================
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f dashboard/data.js ]; then
  echo "⚠ dashboard/data.js 없음 — 발행본이 합성 데이터로 표시됩니다(collector를 먼저 실행 권장)"
fi

mkdir -p docs
# 화면·엔진(소스) + 생성 데이터(발행본). docs/는 gitignore 대상이 아니므로 커밋된다.
# 머니플로 화면 — 상호 링크 경로를 Pages 구조로 재작성(../hr/index.html → hr/)
sed 's#\.\./hr/index\.html#hr/#' dashboard/index.html > docs/index.html
cp dashboard/signal_engine.js docs/signal_engine.js
cp dashboard/research_digest.js docs/research_digest.js
[ -f dashboard/data.js ] && cp dashboard/data.js docs/data.js || true
[ -f dashboard/research.js ] && cp dashboard/research.js docs/research.js || true
touch docs/.nojekyll   # Jekyll 빌드 비활성(정적 파일 그대로 서빙)

# ── HR 대시보드(조직 조류) 발행 → docs/hr/ ──
# 커널(signal_engine)을 같은 폴더에 복사하고 index.html의 상대경로를 재작성한다.
mkdir -p docs/hr
cp dashboard/signal_engine.js docs/hr/signal_engine.js
cp hr/hr_engine.js docs/hr/hr_engine.js
cp hr/hr_data.js docs/hr/hr_data.js
# 엔진 경로 + 상호 링크(../dashboard/index.html → ../) 를 Pages 구조로 재작성
sed -e 's#\.\./dashboard/signal_engine\.js#signal_engine.js#' \
    -e 's#\.\./dashboard/index\.html#../#' \
    hr/index.html > docs/hr/index.html

git add docs
if git diff --cached --quiet; then
  echo "발행본 변경 없음 — 생략"
else
  git commit -q -m "chore(pages): 대시보드 발행본 갱신"
  git push -q origin main
  echo "✓ 발행·푸시 완료 → https://foolpoet44.github.io/money_flow_2026/"
fi
