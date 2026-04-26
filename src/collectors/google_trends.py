"""Google Trends 수집기.

두 가지 경로:
  A) pytrends.trending_searches() — 지역별 일일 인기 검색어 (가끔 404 발생)
  B) Google Trends Daily Trends RSS feed — 키 불필요, 안정적
     https://trends.google.com/trends/trendingsearches/daily/rss?geo=KR
     RSS 차단 시 pytrends로 fallback.

KR/US 모두 지원. 키 불필요.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import List

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import logger

try:
    from pytrends.request import TrendReq
except ImportError:  # pragma: no cover
    TrendReq = None

DAILY_RSS = "https://trends.google.com/trending/rss"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trend-stock-scanner/0.1)"}


# -------- A: pytrends --------

@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8))
def _via_pytrends(pn: str = "south_korea") -> list[str]:
    pytrends = TrendReq(hl="ko-KR", tz=540, timeout=(10, 25), retries=2, backoff_factor=0.5)
    df = pytrends.trending_searches(pn=pn)
    if df is None or df.empty:
        return []
    return df.iloc[:, 0].astype(str).tolist()


# -------- B: Daily Trends RSS --------

def _via_rss(geo: str = "KR") -> list[str]:
    """공식 일일 트렌드 RSS. pytrends보다 안정적이며 키 불필요."""
    try:
        r = requests.get(DAILY_RSS, params={"geo": geo}, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            logger.debug(f"[Google-RSS] {geo}: HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
        # RSS 표준: channel/item/title 들이 트렌드 키워드
        kws = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                kws.append(title_el.text.strip())
        return kws
    except Exception as e:
        logger.warning(f"[Google-RSS] {geo} 실패: {e}")
        return []


# -------- 메인 --------

def fetch_google_trends(top_n: int = 25) -> List[dict]:
    """KR + US 트렌드 키워드. RSS 우선, 실패 시 pytrends fallback."""
    out: list[dict] = []
    for region, geo, pn in [("KR", "KR", "south_korea"), ("US", "US", "united_states")]:
        kws: list[str] = []

        # 1차: RSS
        kws = _via_rss(geo)
        method = "RSS" if kws else None

        # 2차: pytrends fallback
        if not kws and TrendReq is not None:
            try:
                kws = _via_pytrends(pn=pn)
                method = "pytrends" if kws else None
            except Exception as e:
                logger.warning(f"[Google] {region} pytrends 실패: {e}")

        if not kws:
            logger.warning(f"[Google] {region}: RSS·pytrends 모두 실패 — 스킵")
            continue

        kws = kws[:top_n]
        for rank, kw in enumerate(kws, 1):
            score = max(0, 100 - (rank - 1) * 4)
            out.append({
                "keyword": kw, "source": "google", "region": region,
                "rank": rank, "score": score, "method": method,
            })
        logger.info(f"[Google-{method}] {region}: {len(kws)} keywords")
        time.sleep(1.0)
    return out
