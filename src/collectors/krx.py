"""KRX 전종목 일별 거래량 수집기.

우선순위:
  1) KRX Open API (https://data-dbg.krx.co.kr/) — KRX_AUTH_KEY 필요
  2) pykrx (스크래핑 fallback) — 무인증, Naver Finance 기반

pykrx fallback 주의:
  - get_market_ohlcv_by_ticker(date, market) 는 KRX getJsonData.cmd 사용 →
    비한국 IP(GitHub Actions 등)에서 LOGOUT/빈응답 반환.
  - 대신 종목 목록을 먼저 확보 후 get_market_ohlcv_by_date(start, end, ticker) 로
    Naver Finance 경유 조회 → 한국 외 IP에서도 동작.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import logger

# ---------- KRX Open API ----------

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
ENDPOINTS = {
    "KOSPI":  f"{KRX_BASE}/sto/stk_bydd_trd",
    "KOSDAQ": f"{KRX_BASE}/sto/ksq_bydd_trd",
}

_KIND_URL = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/",
}


def _has_krx_key() -> bool:
    return bool(os.getenv("KRX_AUTH_KEY", "").strip())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _krx_request(url: str, basDd: str) -> dict:
    """KRX Open API 단일 호출. 인증키는 'AUTH_KEY' 헤더로 전달."""
    headers = {
        "AUTH_KEY": os.getenv("KRX_AUTH_KEY", ""),
        "Accept": "application/json",
    }
    r = requests.get(url, params={"basDd": basDd}, headers=headers, timeout=30)
    debug = os.getenv("SCANNER_DEBUG", "false").lower() == "true"
    if debug or r.status_code >= 400:
        logger.warning(
            f"[KRX raw] {url}?basDd={basDd} → status={r.status_code} "
            f"body={r.text[:300]}"
        )
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        logger.error(f"[KRX] JSON 파싱 실패. 응답 본문 일부: {r.text[:300]}")
        raise


def _normalize_krx_row(row: dict, market: str, ymd: str) -> dict:
    """KRX 응답 1행 → 표준 schema."""
    raw_code = row.get("ISU_SRT_CD") or row.get("ISU_CD") or row.get("SHRT_ISIN") or ""
    raw_code = str(raw_code).strip()
    if len(raw_code) == 12 and raw_code.startswith("KR"):
        code = raw_code[3:9]
    else:
        code = raw_code.zfill(6) if raw_code else None

    name = row.get("ISU_NM") or row.get("ISU_ABBRV") or row.get("ISU_KR_NM")
    close = row.get("TDD_CLSPRC") or row.get("CLSPRC")
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

_ticker_list_cache: pd.DataFrame | None = None


def _get_ticker_list() -> pd.DataFrame:
    """KOSPI+KOSDAQ 전종목 목록 조회.
    캐시 우선, kind.krx.co.kr HTML 다운로드 fallback."""
    global _ticker_list_cache
    if _ticker_list_cache is not None and not _ticker_list_cache.empty:
        return _ticker_list_cache

    # 1차: kind.krx.co.kr (한국 외 IP에서도 동작)
    try:
        resp = requests.get(_KIND_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_html(BytesIO(resp.content), encoding="euc-kr")[0]
        df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
        df["market"] = df["시장구분"].map({"유가": "KOSPI", "코스닥": "KOSDAQ"})
        df = df[df["market"].notna()][["종목코드", "회사명", "market"]].rename(
            columns={"종목코드": "code", "회사명": "name"}
        )
        _ticker_list_cache = df.reset_index(drop=True)
        logger.info(f"[pykrx] 종목 목록 로드: {len(_ticker_list_cache)}개")
        return _ticker_list_cache
    except Exception as e:
        logger.warning(f"[pykrx] kind.krx 종목 목록 실패: {e}")

    # 2차: pykrx ticker list (한국 IP에서만 안정적)
    try:
        from pykrx import stock as krx
        rows = []
        for market_code, market_name in [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")]:
            ymd = date.today().strftime("%Y%m%d")
            tickers = krx.get_market_ticker_list(ymd, market=market_code)
            for t in tickers:
                rows.append({"code": t, "name": "", "market": market_name})
        _ticker_list_cache = pd.DataFrame(rows)
        return _ticker_list_cache
    except Exception as e:
        logger.error(f"[pykrx] ticker list 수집 실패: {e}")
        return pd.DataFrame(columns=["code", "name", "market"])


def _fetch_ticker_range_pykrx(
    ticker_info: dict, start_str: str, end_str: str
) -> list[dict]:
    """pykrx로 단일 종목의 [start, end] 기간 OHLCV 조회.
    Naver Finance 경유 → 한국 외 IP에서도 동작."""
    from pykrx import stock as krx
    code = ticker_info["code"]
    name = ticker_info["name"]
    market = ticker_info["market"]
    try:
        df = krx.get_market_ohlcv_by_date(start_str, end_str, code)
        if df is None or df.empty or "거래량" not in df.columns:
            return []
        df.index = pd.to_datetime(df.index)
        rows = []
        for idx, row in df.iterrows():
            vol = int(row["거래량"])
            if vol == 0:
                continue
            rows.append({
                "date": idx,
                "code": code,
                "name": name,
                "market": market,
                "close": int(row.get("종가", row.get("Close", 0))),
                "volume": vol,
                "value": int(row.get("거래대금", 0)),
            })
        return rows
    except Exception:
        return []


def _fetch_range_pykrx(start: date, end: date) -> pd.DataFrame:
    """pykrx로 전종목 범위 조회.
    get_market_ohlcv_by_date 사용 (Naver Finance) → 한국 외 IP에서도 동작."""
    ticker_df = _get_ticker_list()
    if ticker_df.empty:
        logger.error("[pykrx] 종목 목록 없음 — 수집 불가")
        return pd.DataFrame()

    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    tickers = ticker_df.to_dict("records")

    all_rows: list[dict] = []
    done = 0
    total = len(tickers)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {
            executor.submit(_fetch_ticker_range_pykrx, t, start_str, end_str): t
            for t in tickers
        }
        for future in as_completed(futures):
            done += 1
            if done % 300 == 0:
                logger.info(f"  [pykrx] 진행: {done}/{total} ({done/total*100:.0f}%)")
            rows = future.result()
            all_rows.extend(rows)

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    logger.info(f"[pykrx] 수집 완료: {len(df):,} rows / {df['code'].nunique():,} 종목")
    return df


# ---------- Public API ----------

def fetch_volume_range(start: date, end: date, prefer_api: bool = True) -> pd.DataFrame:
    """[start, end] 구간의 KOSPI+KOSDAQ 전종목 일별 시세.
    KRX_AUTH_KEY가 설정돼 있으면 Open API 우선, 실패/미설정 시 pykrx fallback.
    """
    use_api = prefer_api and _has_krx_key()
    logger.info(f"[KRX] fetching {start} ~ {end} (mode={'OpenAPI' if use_api else 'pykrx'})")

    # KRX API: 날짜별 순차 수집
    if use_api:
        frames: list[pd.DataFrame] = []
        api_success = 0
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                day_df = _fetch_one_day_krx_api(cur)
                if not day_df.empty:
                    frames.append(day_df)
                    api_success += 1
                time.sleep(0.15)
            cur += timedelta(days=1)

        if frames:
            full = pd.concat(frames, ignore_index=True)
            full = full.dropna(subset=["code", "volume"])
            full["volume"] = full["volume"].fillna(0).astype("int64")
            logger.info(
                f"[KRX API] collected: {len(full):,} rows / {full['date'].nunique()} days "
                f"/ {full['code'].nunique():,} tickers (API hits={api_success})"
            )
            return full
        logger.warning("[KRX API] 응답 없음 — pykrx fallback 시도")

    # pykrx fallback: 종목별 범위 조회 (Naver Finance 경유, IP 제한 없음)
    full = _fetch_range_pykrx(start, end)
    if full.empty:
        logger.error("[KRX] 모든 소스에서 데이터를 가져오지 못했습니다.")
        return pd.DataFrame()

    full = full.dropna(subset=["code", "volume"])
    full["volume"] = full["volume"].fillna(0).astype("int64")
    logger.info(
        f"[pykrx] collected: {len(full):,} rows / {full['date'].nunique()} days "
        f"/ {full['code'].nunique():,} unique tickers"
    )
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
        out = out[~out["name"].str.contains(
            "ETF|ETN|ARIRANG|TIGER|KODEX|KOSEF|KBSTAR|HANARO|SOL|RISE",
            case=False, na=False,
        )]
    if exclude_spac:
        out = out[~out["name"].str.contains("스팩|SPAC", case=False, na=False)]
    if exclude_preferred:
        # 우선주 코드: 5번째 자리(index 4)가 '8'이고 마지막이 '5'인 6자리 코드
        # 또는 코드 끝자리 '5'인 경우 대부분 우선주 (보수적 휴리스틱)
        mask_preferred = out["code"].str.len().eq(6) & out["code"].str[-1].isin(["5"])
        out = out[~mask_preferred]
    return out
