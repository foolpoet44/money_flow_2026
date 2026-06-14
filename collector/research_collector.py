"""
자금 조류 · 리서치 다이제스트 수집기 (research_collector)
────────────────────────────────────────────────────────
네이버 금융 리서치(증권사 리포트)를 자동으로 읽어, 데일리 요약 + 원본 링크를
research.json / dashboard/research.js 로 만든다. 대시보드 하단 "리서치 다이제스트"
섹션이 이를 읽는다.

왜 이 기능인가 (설계 의도):
    수급 대시보드가 "외국인이 무엇을 사고팔았나"라는 사실을 보여준다면,
    리서치 다이제스트는 그 옆에 "왜"를 읽을 단서를 붙인다. 둘은 상보적이다.

"요약"의 원칙 — 추출형 (설계원칙 §1.5 거짓 정밀도 금지):
    AI로 요약을 지어내지 않는다. 리포트 자신의 제목(애널리스트 핵심 논지),
    상세 페이지의 리드 문단, 목표가·투자의견을 '있는 그대로' 가져온다.
    표준 라이브러리(urllib, re, json)만 쓰고 외부 키·LLM 의존이 없다.

데이터 소스 (네이버 금융 리서치, EUC-KR HTML):
    · 종목분석 : finance.naver.com/research/company_list.naver
    · 산업분석 : finance.naver.com/research/industry_list.naver
    · 시황정보 : finance.naver.com/research/market_info_list.naver
    각 행 = 종목/분야 · 제목 · 증권사 · 작성일 · 조회수 · 상세링크(*_read) · PDF원본

실행:  python collector/research_collector.py   →  research.json + dashboard/research.js
"""

import os
import re
import json
import time
import html as _html
import urllib.request
import urllib.error

# ── 설정 ─────────────────────────────────────────────
# 우리가 추적하는 반도체 섹터 — 이 종목/키워드 리포트를 우선 노출한다(money flow와 연결)
SECTOR_TICKERS = {"005930", "000660", "009150", "402340", "042700"}
SECTOR_KEYWORDS = ["반도체", "메모리", "HBM", "DRAM", "낸드", "파운드리", "AI 반도체"]

CATEGORIES = [
    ("company", "종목", "https://finance.naver.com/research/company_list.naver"),
    ("industry", "산업", "https://finance.naver.com/research/industry_list.naver"),
    ("market", "시황", "https://finance.naver.com/research/market_info_list.naver"),
]

MAX_SUMMARIES = 12   # 상세 리드요약을 가져올 리포트 상한(섹터연관·시황 우선). 호출수 제한.
RETRY = 3
SLEEP = 0.25

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
}
BASE = "https://finance.naver.com/research/"


# ── HTTP (EUC-KR 디코드) ─────────────────────────────
def _get_html(url):
    """네이버 리서치 페이지를 EUC-KR로 디코드해 반환. 실패 시 재시도."""
    last = None
    for attempt in range(1, RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            # 네이버 금융 리서치는 EUC-KR. 깨진 문자는 무시.
            return raw.decode("euc-kr", errors="ignore")
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(SLEEP * attempt * 2)
    raise RuntimeError(f"리서치 호출 실패({RETRY}회): {url}\n  마지막 오류: {last!r}")


# ── 파싱 유틸 ────────────────────────────────────────
def _strip(html):
    """태그 제거 + HTML 엔티티 복원(&nbsp; 등) + 공백 정규화."""
    text = _html.unescape(re.sub(r"<[^>]+>", "", html))
    return re.sub(r"\s+", " ", text).strip()

def _date_iso(s):
    """'26.06.12' → '2026-06-12'."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", s)
    return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_list(category, label, html):
    """리포트 목록 페이지 → 리포트 dict 리스트."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        # 상세(read) 링크가 있는 행만 데이터 행으로 본다
        read = re.search(r'href="([^"]*_read\.naver\?[^"]+)"', row)
        if not read:
            continue
        read_url = BASE + read.group(1)

        # 앵커별 텍스트 매핑 (제목·종목명 추출용)
        anchors = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        title, stock = "", ""
        for href, txt in anchors:
            if "_read.naver" in href:
                title = _strip(txt)
            elif "/item/main.naver" in href:
                stock = _strip(txt)

        ticker = ""
        mt = re.search(r"/item/main\.naver\?code=(\d+)", row)
        if mt:
            ticker = mt.group(1)

        pdf = ""
        mp = re.search(r'href="(https?://[^"]+\.pdf)"', row)
        if mp:
            pdf = mp.group(1)

        date = _date_iso(row)

        # 셀들에서 증권사·조회수 추출
        cells = [_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c]
        broker = next((c for c in cells
                       if re.search(r"(증권|투자|리서치|자산운용|금융투자)$", c)), "")
        nums = [c for c in cells if re.fullmatch(r"[\d,]+", c)]
        views = int(nums[-1].replace(",", "")) if nums else 0

        if not title:
            continue

        # 섹터 연관 판정: 추적 종목이거나 제목/종목에 반도체 키워드
        text_blob = f"{stock} {title}"
        sector = (ticker in SECTOR_TICKERS) or any(k in text_blob for k in SECTOR_KEYWORDS)

        out.append({
            "category": category,         # company | industry | market
            "category_label": label,      # 종목 | 산업 | 시황
            "title": title,
            "stock": stock,
            "ticker": ticker,
            "broker": broker,
            "date": date,
            "views": views,
            "url": read_url,
            "pdf": pdf,
            "sector": sector,
            "summary": "",
            "target": "",
            "opinion": "",
        })
    return out


def enrich_detail(report):
    """상세 페이지에서 리드 요약문단 + 목표가·투자의견을 추출해 report에 채운다."""
    try:
        html = _get_html(report["url"])
    except RuntimeError:
        return  # 상세 실패는 치명적이지 않다 — 제목이 곧 요약 역할

    # 목표가 / 투자의견 — 엄격 패턴으로 '확실할 때만' 채운다(거짓 정밀도 금지).
    #   target: '목표(주)가 ... 12,345원'처럼 4자리 이상 + 원 접미사일 때만.
    mt = re.search(r"목표주?가[^\d]{0,8}([1-9][\d,]{3,})\s*원", html)
    if mt:
        report["target"] = mt.group(1).replace(",", "") + "원"
    #   opinion: 알려진 투자의견 표현에만 매칭(태그 잔여물 'em' 같은 오염 차단).
    OPINIONS = ("적극매수", "비중확대", "비중축소", "매수", "매도", "중립", "보유",
                "Strong Buy", "Trading Buy", "Buy", "Hold", "Sell",
                "Outperform", "Marketperform", "Underperform")
    mo = re.search(r"투자의견[^가-힣A-Za-z]{0,8}(" + "|".join(OPINIONS) + r")", html)
    if mo:
        report["opinion"] = mo.group(1)

    # 리드 요약: 본문 td 중 가장 긴 텍스트 블록(리포트 첫 문단)
    texts = [_strip(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)]
    texts = [t for t in texts if len(t) > 40 and "pdf" not in t.lower()]
    if texts:
        lead = max(texts, key=len)
        lead = re.sub(r"\d{8,}_?\w*\.pdf", "", lead).strip()  # 끝에 붙는 파일명 제거
        report["summary"] = lead[:280]


# ── 엔트리포인트 ─────────────────────────────────────
def main():
    print("· 네이버 리서치 목록 수집 중…")
    reports = []
    for category, label, url in CATEGORIES:
        print(f"   - {label}분석")
        reports.extend(parse_list(category, label, _get_html(url)))
        time.sleep(SLEEP)

    if not reports:
        raise RuntimeError("리포트를 한 건도 파싱하지 못했습니다. 페이지 구조 변경 의심.")

    # 데일리 필터: 가장 최근 작성일의 리포트만 (= 오늘의 리서치)
    as_of = max(r["date"] for r in reports if r["date"])
    reports = [r for r in reports if r["date"] == as_of]

    # 정렬: 섹터연관 우선 → 시황 → 조회수 내림차순
    cat_rank = {"market": 0, "industry": 1, "company": 2}
    reports.sort(key=lambda r: (not r["sector"], cat_rank.get(r["category"], 9), -r["views"]))

    # 상세 요약 보강: 섹터연관·시황 우선으로 상한까지만 호출(시간 제한)
    print("· 핵심 리포트 상세 요약 추출 중…")
    enriched = 0
    for r in reports:
        if enriched >= MAX_SUMMARIES:
            break
        if r["sector"] or r["category"] == "market":
            enrich_detail(r)
            enriched += 1
            time.sleep(SLEEP)

    data = {
        "as_of": as_of,
        "count": len(reports),
        "sector_count": sum(1 for r in reports if r["sector"]),
        "reports": reports,
    }

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(os.path.join(root, "research.json"), "w", encoding="utf-8") as fp:
        fp.write(payload)
    with open(os.path.join(root, "dashboard", "research.js"), "w", encoding="utf-8") as fp:
        fp.write("window.RESEARCH = " + payload + ";\n")
    print(f"✓ research.json + dashboard/research.js · {as_of} · "
          f"리포트 {data['count']}건(섹터연관 {data['sector_count']}건) · 요약보강 {enriched}건")


if __name__ == "__main__":
    main()
