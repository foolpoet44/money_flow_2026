"""
자금 조류 · 텔레그램 봇 응답기 (scripts/bot.py)
────────────────────────────────────────────────────────
온디맨드로 대시보드 링크와 최신 다이제스트를 답한다. long-polling 방식.

실행:  set -a; . scripts/run_daily.env; set +a; python3 scripts/bot.py
명령:  /dashboard  — 대시보드(GitHub Pages) 링크
       /status     — 최신 다이제스트(run_daily.sh가 만든 캐시)
       /help       — 도움말

보안: TELEGRAM_CHAT_ID 와 일치하는 채팅에만 응답한다(소유자 외 무시).
표준 라이브러리만 사용.
"""
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER = os.environ.get("TELEGRAM_CHAT_ID")
DASH = os.environ.get("DASHBOARD_URL", "https://foolpoet44.github.io/money_flow_2026/")
HERE = os.path.dirname(os.path.abspath(__file__))

if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN 미설정 — `set -a; . scripts/run_daily.env; set +a` 후 실행")

API = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "자금 조류 봇\n"
    "/dashboard — 대시보드 링크\n"
    "/status — 최신 다이제스트\n"
    "/help — 도움말"
)


def _api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def _last_digest():
    try:
        with open(os.path.join(HERE, "last_digest.txt"), encoding="utf-8") as fp:
            return fp.read()
    except OSError:
        return "아직 생성된 다이제스트가 없습니다. scripts/run_daily.sh 를 먼저 실행하세요."


def _reply(chat, text):
    _api("sendMessage", chat_id=chat, text=text, disable_web_page_preview="true")


def handle(msg):
    chat = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip().lower()
    # 소유자(설정된 chat_id) 외에는 응답하지 않는다
    if OWNER and chat != str(OWNER):
        return
    if text.startswith("/dashboard"):
        _reply(chat, f"📈 대시보드\n{DASH}")
    elif text.startswith("/status") or text.startswith("/now"):
        _reply(chat, _last_digest() + f"\n\n📈 {DASH}")
    else:
        _reply(chat, HELP + f"\n\n📈 {DASH}")


def main():
    print("봇 시작 (long-polling) — Ctrl-C 로 종료")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            res = _api("getUpdates", **params)
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if msg:
                    handle(msg)
        except KeyboardInterrupt:
            print("\n종료")
            break
        except (urllib.error.URLError, TimeoutError) as e:
            print("네트워크 오류, 3초 후 재시도:", e)
            time.sleep(3)
        except Exception as e:
            print("오류, 3초 후 재시도:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
