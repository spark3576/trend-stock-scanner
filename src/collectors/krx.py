"""KRX 전종목 일별 거래량 수집기.

우선순위:
  1) KRX Open API (https://openapi.krx.co.kr/) — KRX_AUTH_KEY 필요
  2) pykrx (스크래핑 fallback) — 무인증

반환 DataFrame schema:
  columns = [date, code, name, market, close, volume, value]
  date: pandas.Timestamp (날짜)
  code: 6자리 종목코드 (str)
  name: 종목명
  market: 'KOSPI' | 'KOSDAQ'
  close: 종가 (int)
  volume: 거래량 (int)
  value: 거래대금 (int)
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Iterable

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import logger

# ---------- KRX Open API ----------

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
# 공식 서비스 코드 (KRX OpenAPI 콘솔의 '서비스 목록' 기준)
ENDPOINTS = {
    "KOSPI":  f"{KRX_BASE}/sto/stk_bydd_trd",   # 유가증권 일별매매정보
    "KOSDAQ": f"{KRX_BASE}/sto/ksq_bydd_trd",   # 코스닥 일별매매정보
}


def _has_krx_key() -> bool:
    return bool(os.getenv("KRX_AUTH_KEY", "").strip())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _krx_request(url: str, basDd: str) -> dict:
    """KRX Open API 단일 호출.
    인증키는 'AUTH_KEY' 헤더로 전달, 일자는 yyyymmdd."""
    headers = {
        "AUTH_KEY": os.getenv("KRX_AUTH_KEY", ""),
        "Accept": "application/json",
    }
    params = {"basDd": basDd}
    r = requests.get(url, params=params, headers=headers, timeout=30)

    debug = os.getenv("SCANNER_DEBUG", "false").lower() == "true"
    if debug or r.status_code >= 400:
        logger.warning(
            f"[KRX raw] {url}?basDd={basDd} → status={r.status_code} "
            f"content-type={r.headers.get('content-type')} "
            f"body={r.text[:300]}"
        )
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        logger.error(f"[KRX] JSON 파싱 실패. 응답 본문 일부: {r.text[:300]}")
        raise


def _normalize_krx_row(row: dict, market: str, ymd: str) -> dict:
    """KRX 응답 1행 → 표준 schema.
    실제 KRX OpenAPI(stk_bydd_trd) 응답 키 (확인됨, 2026-04):
      BAS_DD, ISU_CD, ISU_NM, MKT_NM, SECT_TP_NM,
      TDD_CLSPRC, CMPPREVDD_PRC, FLUC_RT,
      TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC,
      ACC_TRDVOL, ACC_TRDVAL, MKTCAP, LIST_SHRS
    """
    # 단축코드 추출: ISU_CD가 6자리이면 그대로, 12자리 ISIN(KR7…)이면 [3:9] 슬라이스
    raw_code = row.get("ISU_SRT_CD") or row.get("ISU_CD") or row.get("SHRT_ISIN") or ""
    raw_code = str(raw_code).strip()
    if len(raw_code) == 12 and raw_code.startswith("KR"):
        code = raw_code[3:9]   # ISIN → 단축코드
    else:
        code = raw_code.zfill(6) if raw_code else None

    name = row.get("ISU_NM") or row.get("ISU_ABBRV") or row.get("ISU_KR_NM")
    close = row.get("TDD_CLSPRC") or row.get("CLSPRC")
    # 거래량 키 다중 후보 (KRX 응답 스키마 변형 대응)
    volume = (row.get("ACC_TRDVOL") or row.get("TRDVOL") or
              row.get("TDD_TRDVOL") or row.get("ACC_TRDVOL_RPT"))
    value = (row.get("ACC_TRDVAL") or row.get("TRDVAL") or
             row.get("TDD_TRDVAL") or row.get("ACC_TRDVAL_RPT"))
    market_resolved = row.get("MKT_NM") or market

    def _to_int(x):
        if x is None or x == "" or x == "-":
            return None
        try:
            return int(str(x).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    return {
        "date": pd.Timestamp(ymd[:4] + "-" + ymd[4:6] + "-" + ymd[6:8]),
        "code": code,
        "name": (name or "").strip(),
        "market": "KOSPI" if "유가" in str(market_resolved) or "KOSPI" in str(market_resolved).upper()
                  else ("KOSDAQ" if "코스닥" in str(market_resolved) or "KOSDAQ" in str(market_resolved).upper()
                        else market),
        "close": _to_int(close),
        "volume": _to_int(volume),
        "value": _to_int(value),
    }


def _fetch_one_day_krx_api(d: date) -> pd.DataFrame:
    ymd = d.strftime("%Y%m%d")
    frames = []
    for market, url in ENDPOINTS.items():
        try:
            payload = _krx_request(url, ymd)
            rows = payload.get("OutBlock_1") or payload.get("OutBlock_2") or []
            if not rows:
                logger.debug(f"KRX API empty for {market} {ymd}")
                continue
            df = pd.DataFrame([_normalize_krx_row(r, market, ymd) for r in rows])
            frames.append(df)
        except Exception as e:
            logger.warning(f"KRX API failed for {market} {ymd}: {e}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------- pykrx fallback ----------

def _fetch_one_day_pykrx(d: date) -> pd.DataFrame:
    """pykrx로 KOSPI+KOSDAQ 전종목 일별 시세 조회."""
    from pykrx import stock as krx
    ymd = d.strftime("%Y%m%d")
    out = []
    for market_code, market_name in [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")]:
        try:
            df = krx.get_market_ohlcv_by_ticker(ymd, market=market_code)
            if df is None or df.empty:
                continue
            # 종목명 매핑
            tickers = df.index.tolist()
            name_map = {t: krx.get_market_ticker_name(t) for t in tickers}
            df = df.reset_index().rename(columns={
                "티커": "code", "종가": "close", "거래량": "volume", "거래대금": "value",
            })
            df["name"] = df["code"].map(name_map)
            df["market"] = market_name
            df["date"] = pd.Timestamp(ymd[:4] + "-" + ymd[4:6] + "-" + ymd[6:8])
            out.append(df[["date", "code", "name", "market", "close", "volume", "value"]])
        except Exception as e:
            logger.warning(f"pykrx fetch failed for {market_name} {ymd}: {e}")
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


# ---------- Public API ----------

def fetch_volume_range(start: date, end: date, prefer_api: bool = True) -> pd.DataFrame:
    """[start, end] 구간 전영업일의 KOSPI+KOSDAQ 전종목 일별 시세.
    KRX_AUTH_KEY가 설정돼 있으면 Open API 우선 시도, 실패/미설정 시 pykrx로 fallback.
    """
    use_api = prefer_api and _has_krx_key()
    logger.info(f"[KRX] fetching {start} ~ {end} (mode={'OpenAPI' if use_api else 'pykrx'})")

    cur = start
    frames: list[pd.DataFrame] = []
    api_success_count = 0
    while cur <= end:
        if cur.weekday() < 5:  # 평일만
            day_df = pd.DataFrame()
            if use_api:
                day_df = _fetch_one_day_krx_api(cur)
                if not day_df.empty:
                    api_success_count += 1
            if day_df.empty:
                # API 미사용 또는 빈 응답 → pykrx
                day_df = _fetch_one_day_pykrx(cur)
            if not day_df.empty:
                frames.append(day_df)
            time.sleep(0.15)  # rate limit
        cur += timedelta(days=1)

    if not frames:
        logger.error("[KRX] 모든 일자에서 데이터를 가져오지 못했습니다.")
        return pd.DataFrame()

    full = pd.concat(frames, ignore_index=True)
    full = full.dropna(subset=["code", "volume"])
    full["volume"] = full["volume"].fillna(0).astype("int64")
    logger.info(f"[KRX] collected: {len(full):,} rows / {full['date'].nunique()} business days "
                f"/ {full['code'].nunique():,} unique tickers (API hits={api_success_count})")
    return full


def filter_universe(
    df: pd.DataFrame,
    exclude_etf: bool = True,
    exclude_spac: bool = True,
    exclude_preferred: bool = True,
) -> pd.DataFrame:
    """ETF/스팩/우선주 필터링."""
    if df.empty:
        return df
    out = df.copy()
    if exclude_etf:
        out = out[~out["name"].str.contains("ETF|ETN|ARIRANG|TIGER|KODEX|KOSEF|KBSTAR|HANARO|SOL|RISE", case=False, na=False)]
    if exclude_spac:
        out = out[~out["name"].str.contains("스팩|SPAC", case=False, na=False)]
    if exclude_preferred:
        out = out[~out["code"].str.endswith(("5", "7", "9"))]  # 우선주 코드 끝자리 휴리스틱
    return out
