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

mkdir -p docs docs/vendor
# 화면·엔진(소스) + 생성 데이터(발행본). docs/는 gitignore 대상이 아니므로 커밋된다.
cp dashboard/index.html docs/index.html
cp dashboard/signal_engine.js docs/signal_engine.js
cp dashboard/research_digest.js docs/research_digest.js
# 벤드링 라이브러리(uPlot). npm 이 아니라 파일이므로 발행본에도 그대로 복사한다.
cp dashboard/vendor/* docs/vendor/
[ -f dashboard/data.js ] && cp dashboard/data.js docs/data.js || true
[ -f dashboard/research.js ] && cp dashboard/research.js docs/research.js || true
# 시계열 원장 → 대시보드 입력(§4.1). 원장 자체는 docs/series/ 에 이미 누적돼 있고,
# collector 가 그것을 series.js 로 굽는다. 여기서는 발행본으로 옮기기만 한다.
[ -f dashboard/series.js ] && cp dashboard/series.js docs/series.js || true

# OOS 정산 현황 → 방식 B(§6)로 화면에 노출. 그동안 edge_status.json 은 텔레그램에만 나갔고
# 대시보드는 등급 A/B/C 만 자신 있게 보여줬다. "그 신호가 실제로 돈이 됐는가"를 같은 화면에 둔다.
if [ -f backtest/edge_status.json ]; then
  { printf 'window.EDGE = '; cat backtest/edge_status.json; printf ';\n'; } > dashboard/edge.js
  cp dashboard/edge.js docs/edge.js
fi

# OOS 원장 자체도 화면에 낸다. edge_status 는 집계 한 줄이라 "표본이 자라는 중"이 안 보였다.
# JSONL → JS 배열 변환은 node 로 한다(셸 문자열 조립은 따옴표·이스케이프에서 조용히 깨진다).
if [ -f backtest/ledger.jsonl ]; then
  node -e '
    const fs = require("fs");
    const rows = fs.readFileSync("backtest/ledger.jsonl", "utf8")
      .split("\n").filter(Boolean).map((l) => JSON.parse(l));
    fs.writeFileSync("dashboard/ledger.js", "window.LEDGER = " + JSON.stringify(rows) + ";\n");
  '
  cp dashboard/ledger.js docs/ledger.js
fi
touch docs/.nojekyll   # Jekyll 빌드 비활성(정적 파일 그대로 서빙)

git add docs
# OOS 원장(재생성 불가한 시점불변 증거)과 그 파생 현황도 함께 커밋해 누적을 영속화한다.
# 무인 클라우드 실행(GitHub Actions)은 매번 빈 디스크로 시작하므로, git 이 유일한 누적 매체다.
# if 로 분기해 "파일 없음"은 안전히 건너뛰되, 파일이 있는데 git add 가 실패하면(lock·권한 등)
# set -e 로 파이프라인을 중단시킨다 — 원장 유실을 조용히 넘기지 않는다.
if [ -f backtest/ledger.jsonl ]; then
  git add backtest/ledger.jsonl
fi
if [ -f backtest/edge_status.json ]; then
  git add backtest/edge_status.json
fi
if git diff --cached --quiet; then
  echo "발행본 변경 없음 — 생략"
else
  git commit -q -m "chore(pages): 대시보드 발행본 갱신"
  # 푸시 대상은 '지금 체크아웃된 브랜치'다. main 하드코딩이면 브랜치에서 파이프라인을
  # 검증할 수 없고(수정본을 돌려봐도 결과가 main 으로 나간다), 검증 없이 main 에 합치는
  # 것 말고는 선택지가 없어진다. 정기 실행은 main 을 체크아웃하므로 동작은 그대로다.
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"

  # 푸시 실패 = 그날 OOS 원장 영구 유실.
  #   러너는 매번 빈 디스크로 시작하므로 git 이 유일한 누적 매체다. 체크아웃 이후 원격이
  #   움직였으면(수동 커밋 등) non-fast-forward 로 푸시가 막히고, run_daily.sh 는 이를
  #   '비치명적'으로 삼킨다. 그 사이 만들어진 ledger.jsonl 한 줄은 러너와 함께 사라지고
  #   다음 실행은 오늘 as_of 만 적재하므로 그 하루는 영영 안 돌아온다.
  #   → 밀어내기 전에 원격을 흡수한다. 실패하면 한 번 더 시도하고, 그래도 안 되면 죽는다
  #     (조용히 성공한 척하지 않는다 — 유실은 알려져야 한다).
  for attempt in 1 2; do
    git pull --rebase -q origin "$BRANCH" || true
    if git push -q origin "HEAD:${BRANCH}"; then
      echo "✓ 발행·푸시 완료(${BRANCH}) → https://foolpoet44.github.io/money_flow_2026/"
      exit 0
    fi
    echo "⚠ 푸시 실패(${attempt}/2) — 원격을 다시 흡수하고 재시도"
  done
  echo "✗ 푸시 최종 실패 — 이 실행의 원장 적재분이 유실됩니다(수동 확인 필요)"
  exit 1
fi
