"""WebSearch 보강 수집기.

GitHub Actions나 로컬 환경에서 무료로 쓸 수 있는 일반 검색 API가 마땅치 않으므로,
DuckDuckGo HTML 결과(API 키 불필요)를 파싱해 한국 시장 테마 관련 보도/이슈 헤드라인을 수집한다.
실패 시 빈 리스트 반환 — 파이프라인은 그대로 진행."""
from __future__ import annotations

import re
import time
from datetime import date
from typing import List
from urllib.parse import quote_plus

import requests

from src.utils.logger import logger

DDG_URL = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; trend-stock-scanner/0.1)",
    "Accept-Language": "ko,en;q=0.5",
}

DEFAULT_QUERIES_KR = [
    "KOSPI 테마주 급등 오늘",
    "KOSDAQ 테마주 화제",
    "주식 종목토론 인기",
    "정부 정책 수혜주",
]
DEFAULT_QUERIES_US = [
    "trending stocks today reddit wallstreetbets",
    "hot sector stocks this week",
    "thematic ETF new launches",
]

TITLE_RE = re.compile(r'<a rel="nofollow" class="result__a"[^>]*>(.*?)</a>', re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def _ddg_search(query: str, max_results: int = 8) -> list[str]:
    try:
        r = requests.post(DDG_URL, data={"q": query}, headers=HEADERS, timeout=15)
        r.raise_for_status()
        titles = []
        for m in TITLE_RE.findall(r.text)[:max_results]:
            text = TAG_RE.sub("", m).strip()
            if text:
                titles.append(text)
        return titles
    except Exception as e:
        logger.warning(f"[Web] DuckDuckGo failed for '{query}': {e}")
        return []


def fetch_web_headlines(top_n: int = 25) -> List[dict]:
    """KR/US 검색 헤드라인에서 후보 키워드 추출."""
    out: list[dict] = []
    for region, queries in [("KR", DEFAULT_QUERIES_KR), ("US", DEFAULT_QUERIES_US)]:
        for q in queries:
            titles = _ddg_search(q)
            for title in titles:
                # 한글/영문 명사 후보 (3자 이상)
                tokens = re.findall(r"[A-Za-z가-힣]{3,}", title)
                for tk in tokens:
                    out.append({
                        "keyword": tk, "source": "web", "region": region,
                        "context": title[:120], "score": 35,
                    })
            time.sleep(0.7)
    # 중복 제거 + 빈도 가중
    from collections import Counter
    cnt = Counter((r["keyword"], r["region"]) for r in out)
    seen: set = set()
    deduped: list[dict] = []
    for r in out:
        key = (r["keyword"], r["region"])
        if key in seen:
            continue
        seen.add(key)
        r["mentions"] = cnt[key]
        r["score"] = min(100, 30 + cnt[key] * 8)
        deduped.append(r)
    deduped.sort(key=lambda x: -x["score"])
    deduped = deduped[:top_n]
    logger.info(f"[Web] collected {len(deduped)} headline tokens")
    return deduped
