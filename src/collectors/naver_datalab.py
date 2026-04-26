"""네이버 데이터랩 Open API 검색어 트렌드 수집기.

공식 문서: https://developers.naver.com/docs/serviceapi/datalab/search/search.md
필요 환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

API 특성:
  - 1 요청 = keywordGroups 최대 5개 그룹
  - 각 그룹 = keywords 최대 5개 (합산 시계열 1개 반환)
  - 따라서 1요청에 최대 25 키워드를 별도 시계열로 받을 수 있음
  - 본 모듈은 키워드 1개당 1그룹으로 묶어 1요청에 5키워드씩 효율적으로 처리

전략:
  1) configs/keyword_theme_map.json의 모든 한글 키워드를 후보로
  2) 5개씩 묶어 한 번에 호출 (요청 수 1/5)
  3) 최근 7일 / 직전 7일 검색량 비율 → 1.5x 이상이면 'surge'로 채택
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import logger

NAVER_URL = "https://openapi.naver.com/v1/datalab/search"
KEYWORD_MAP_PATH = Path(__file__).resolve().parents[2] / "configs" / "keyword_theme_map.json"


def _load_candidate_keywords() -> List[str]:
    if not KEYWORD_MAP_PATH.exists():
        return []
    data = json.loads(KEYWORD_MAP_PATH.read_text(encoding="utf-8"))
    kws: list[str] = []
    for theme, payload in data.get("themes", {}).items():
        kws.extend(payload.get("keywords", []))
    # 한글 1자 이상 포함 + 너무 짧지 않은 것 (1글자/2글자 영어 제외)
    out: list[str] = []
    seen = set()
    for k in kws:
        kk = k.strip()
        if not kk or kk in seen:
            continue
        if any("가" <= ch <= "힯" for ch in kk):
            out.append(kk)
            seen.add(kk)
        elif len(kk) >= 4:  # 영어는 4자 이상만 (예: HBM은 빠지고 GPT5, ADC 같은 짧은 건 빠짐)
            out.append(kk)
            seen.add(kk)
    return out


def _has_creds() -> bool:
    return bool(os.getenv("NAVER_CLIENT_ID")) and bool(os.getenv("NAVER_CLIENT_SECRET"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _request(body: dict) -> dict:
    headers = {
        "X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID", ""),
        "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET", ""),
        "Content-Type": "application/json",
    }
    r = requests.post(NAVER_URL, headers=headers, data=json.dumps(body), timeout=45)
    r.raise_for_status()
    return r.json()


def fetch_naver_trends(top_n: int = 25, surge_ratio: float = 1.5,
                       groups_per_request: int = 5) -> List[dict]:
    """네이버 데이터랩 검색어트렌드로 후보 키워드의 최근 검색량 급증분 산출."""
    if not _has_creds():
        logger.warning("[Naver] CLIENT_ID/SECRET 미설정 — 스킵")
        return []
    candidates = _load_candidate_keywords()
    if not candidates:
        logger.warning("[Naver] keyword_theme_map.json에 후보 키워드가 없음")
        return []

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=13)
    mid = end - timedelta(days=6)  # 최근 7일 vs 직전 7일

    out: list[dict] = []
    n_requests, n_failed = 0, 0
    for i in range(0, len(candidates), groups_per_request):
        batch = candidates[i:i + groups_per_request]
        # 각 키워드를 별도 그룹으로 (그룹명=키워드 자체) → 시계열 분리
        groups = [{"groupName": kw, "keywords": [kw]} for kw in batch]
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": "date",
            "keywordGroups": groups,
        }
        try:
            payload = _request(body)
            n_requests += 1
            for series in payload.get("results", []):
                kw = series.get("title")
                points = series.get("data", [])
                if not points:
                    continue
                recent = [p["ratio"] for p in points if p["period"] >= mid.isoformat()]
                prior  = [p["ratio"] for p in points if p["period"] <  mid.isoformat()]
                if not recent or not prior:
                    continue
                r_avg = sum(recent) / len(recent)
                p_avg = sum(prior) / len(prior) or 1e-6
                ratio = r_avg / p_avg
                if ratio >= surge_ratio:
                    score = min(100, int(ratio * 30))
                    out.append({
                        "keyword": kw, "source": "naver", "region": "KR",
                        "ratio": round(ratio, 2), "score": score,
                    })
            time.sleep(1.5)  # rate limit 보호
        except Exception as e:
            n_failed += 1
            logger.warning(f"[Naver] batch {i//groups_per_request} ({batch[:2]}…) 실패: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(2)

    out.sort(key=lambda x: -x["score"])
    out = out[:top_n]
    logger.info(f"[Naver] {n_requests}회 요청 / {n_failed}회 실패 / surge 키워드 {len(out)}개")
    return out
