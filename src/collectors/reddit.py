"""Reddit 핫 토픽 수집기.

두 가지 모드 지원:
  1) 인증 모드 (PRAW) — REDDIT_CLIENT_ID/SECRET/USER_AGENT 환경변수 있을 때
     장점: rate limit 여유, 신뢰성 ↑
  2) 익명 모드 (requests + .json) — 환경변수 없을 때 자동 fallback
     장점: 키 발급 불필요, 즉시 사용 가능
     단점: rate limit 빡빡 (분당 ~30회), User-Agent 필수

대상 서브레딧: wallstreetbets, investing, stocks, KoreanInvestor.
"""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from typing import List

import requests

from src.utils.logger import logger

try:
    import praw
except ImportError:  # pragma: no cover
    praw = None

SUBREDDITS = ["wallstreetbets", "investing", "stocks", "KoreanInvestor"]
TICKER_RE = re.compile(r"\b\$?([A-Z]{2,5})\b")
COMMON_BLACKLIST = {
    # 일반 영단어 (대문자 표기시 티커처럼 보임)
    "THE", "AND", "FOR", "YOU", "ALL", "WITH", "BUT", "NOT", "ARE", "WAS",
    "THIS", "WE", "BE", "NOW", "FROM", "HE", "MY", "HAS", "AT", "IT", "THAT",
    "ON", "SO", "THEY", "IF", "IN", "TO", "OF", "OR", "AS", "AN", "BY",
    "IS", "AM", "DO", "GO", "NO", "ME", "US", "UP", "OUT", "OUR", "HIS",
    "HER", "HOW", "WHO", "WHY", "WHAT", "WHEN", "WHERE", "WHICH", "WILL",
    "JUST", "LIKE", "OVER", "THAN", "MORE", "ONE", "TWO", "WAY", "GET",
    "MAKE", "SOME", "DOES", "DID", "DAY", "WAY", "NEW", "BIG", "OLD",
    "TIME", "YEAR", "MONTH", "WEEK", "ABOUT", "ALSO", "ANY", "BACK",
    "BEEN", "EVEN", "HAVE", "HERE", "INTO", "LAST", "MANY", "MOST",
    "MUCH", "ONLY", "OTHER", "OWN", "SAID", "SAME", "STILL", "SUCH",
    "THEIR", "THEM", "THERE", "THESE", "THOSE", "WERE", "WORK", "WOULD",
    # 욕설/감탄사
    "FUCK", "SHIT", "WTF", "OMG", "LOL", "LMAO", "ROFL", "ASS", "DAMN",
    # 금융 일반어 (티커 아님)
    "USA", "FED", "CEO", "IPO", "ETF", "AI", "NYSE", "USD", "GDP",
    "Q1", "Q2", "Q3", "Q4", "DD", "YOLO", "FOMO", "FUD", "ATH", "EOD",
    "SPY", "QQQ", "PE", "EV", "ROI", "ROE", "EPS", "TLDR", "OP", "EDIT",
    "USD", "EUR", "GBP", "JPY", "KRW", "CNY", "BTC", "ETH",
    "PUT", "CALL", "BUY", "SELL", "HOLD", "LONG", "SHORT", "BULL", "BEAR",
}
USER_AGENT_DEFAULT = "trend-stock-scanner/0.1 (anonymous mode)"


def _has_creds() -> bool:
    return all(os.getenv(k) for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"))


# -------- Mode 1: PRAW (인증) --------

def _fetch_authenticated(top_n: int, posts_per_sub: int) -> tuple[Counter, Counter]:
    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT"),
    )
    tickers, keywords = Counter(), Counter()
    for sub_name in SUBREDDITS:
        try:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=posts_per_sub):
                text = f"{post.title} {getattr(post, 'selftext', '') or ''}"
                _extract(text, tickers, keywords, post.score)
        except Exception as e:
            logger.warning(f"[Reddit-PRAW] r/{sub_name}: {e}")
    return tickers, keywords


# -------- Mode 2: 익명 JSON --------

def _fetch_anonymous(top_n: int, posts_per_sub: int) -> tuple[Counter, Counter]:
    """https://www.reddit.com/r/{sub}/hot.json — 키 없이 가능. UA만 설정."""
    headers = {"User-Agent": USER_AGENT_DEFAULT}
    tickers, keywords = Counter(), Counter()
    for sub_name in SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub_name}/hot.json"
        try:
            r = requests.get(url, headers=headers, params={"limit": posts_per_sub}, timeout=15)
            if r.status_code != 200:
                logger.warning(f"[Reddit-anon] r/{sub_name}: HTTP {r.status_code}")
                time.sleep(2)
                continue
            data = r.json().get("data", {}).get("children", [])
            for child in data:
                p = child.get("data", {})
                text = f"{p.get('title','')} {p.get('selftext','')}"
                score = int(p.get("score", 0))
                _extract(text, tickers, keywords, score)
            time.sleep(2)  # rate limit (~30/min)
        except Exception as e:
            logger.warning(f"[Reddit-anon] r/{sub_name}: {e}")
            time.sleep(2)
    return tickers, keywords


def _extract(text: str, tickers: Counter, keywords: Counter, score: int) -> None:
    for m in TICKER_RE.findall(text.upper()):
        if m not in COMMON_BLACKLIST and 2 <= len(m) <= 5:
            tickers[m] += 1 + (max(score, 0) // 100)
    for w in re.findall(r"[A-Za-z가-힣]{5,}", text):
        keywords[w.lower()] += 1


# -------- 메인 --------

def fetch_reddit_trends(top_n: int = 25, posts_per_sub: int = 50) -> List[dict]:
    """모드 자동 선택 → 티커/키워드 빈도 집계."""
    if _has_creds() and praw is not None:
        mode = "PRAW"
        tickers, keywords = _fetch_authenticated(top_n, posts_per_sub)
    else:
        mode = "anonymous"
        logger.info("[Reddit] 키 미설정 — 익명 모드(JSON) 사용")
        tickers, keywords = _fetch_anonymous(top_n, posts_per_sub)

    out: list[dict] = []
    for ticker, cnt in tickers.most_common(top_n):
        out.append({
            "keyword": ticker, "source": "reddit", "region": "US",
            "mentions": cnt, "score": min(100, cnt * 4),
        })
    for kw, cnt in keywords.most_common(top_n // 2):
        if cnt >= 5:
            out.append({
                "keyword": kw, "source": "reddit", "region": "US/KR",
                "mentions": cnt, "score": min(100, cnt * 2),
            })
    logger.info(f"[Reddit-{mode}] collected {len(out)} items")
    return out
