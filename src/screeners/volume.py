"""거래량 기반 1차/2차 스크리닝.

입력: KRX collector가 반환한 long-format DataFrame (date, code, name, market, close, volume, value)
출력: 종목별 윈도우 평균 + 통과 여부.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.utils.dates import VolumeWindows
from src.utils.logger import logger


@dataclass
class ScreenResult:
    primary: pd.DataFrame   # 1차 통과: 거래량 지속 상승
    secondary: pd.DataFrame  # 2차 통과: 1차 + 2배 급증
    all_with_metrics: pd.DataFrame  # 전종목 + 윈도우 평균 (디버깅/엑셀용)


def _avg_volume(df: pd.DataFrame, start: date, end: date) -> pd.Series:
    """[start, end] 구간(영업일 기준) 종목별 평균 거래량."""
    mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
    sub = df.loc[mask, ["code", "name", "market", "volume"]]
    if sub.empty:
        return pd.Series(dtype="float64")
    return sub.groupby("code")["volume"].mean()


def _meta(df: pd.DataFrame) -> pd.DataFrame:
    """종목 메타데이터 (가장 최근 일자 기준 name/market/close)."""
    latest = df.sort_values("date").drop_duplicates("code", keep="last")
    return latest[["code", "name", "market", "close"]].set_index("code")


def screen_volume(
    df: pd.DataFrame,
    windows: VolumeWindows,
    primary_min_ratio: float = 1.30,
    secondary_ratio: float = 2.0,
    min_avg_volume: int = 50_000,
) -> ScreenResult:
    """3-bucket 평균 거래량 비교로 1차·2차 스크리닝."""
    if df.empty:
        logger.warning("[Screen] 입력 DataFrame이 비어있음")
        empty = pd.DataFrame()
        return ScreenResult(empty, empty, empty)

    avg_2w   = _avg_volume(df, windows.w2_start, windows.w2_end).rename("vol_2w")
    avg_2_4w = _avg_volume(df, windows.w24_start, windows.w24_end).rename("vol_2w_4w")
    avg_4_8w = _avg_volume(df, windows.w48_start, windows.w48_end).rename("vol_4w_8w")

    metrics = pd.concat([avg_2w, avg_2_4w, avg_4_8w], axis=1).fillna(0)
    meta = _meta(df)
    metrics = metrics.join(meta, how="left").reset_index()

    # 비율 계산 (분모 0 방지)
    metrics["ratio_2w_over_4w8w"] = metrics["vol_2w"] / metrics["vol_4w_8w"].replace(0, pd.NA)
    metrics["ratio_2w_over_2w4w"] = metrics["vol_2w"] / metrics["vol_2w_4w"].replace(0, pd.NA)
    metrics = metrics.dropna(subset=["ratio_2w_over_4w8w", "ratio_2w_over_2w4w"])

    # 최소 거래량 컷
    metrics = metrics[metrics["vol_2w"] >= min_avg_volume]

    # 1차: 지속 상승 (8w < 4w-2w < 2w) AND 노이즈컷
    cond_primary = (
        (metrics["vol_4w_8w"] < metrics["vol_2w_4w"]) &
        (metrics["vol_2w_4w"] < metrics["vol_2w"]) &
        (metrics["ratio_2w_over_4w8w"] >= primary_min_ratio)
    )
    primary = metrics[cond_primary].copy().sort_values("ratio_2w_over_4w8w", ascending=False)

    # 2차: 1차 통과 + 2~4주 대비 2배 급증
    cond_secondary = primary["ratio_2w_over_2w4w"] >= secondary_ratio
    secondary = primary[cond_secondary].copy().sort_values("ratio_2w_over_2w4w", ascending=False)

    logger.info(f"[Screen] 전체 {len(metrics):,} → 1차 {len(primary):,} → 2차 {len(secondary):,}")

    # 컬럼 순서 정리
    cols = ["code", "name", "market", "close",
            "vol_4w_8w", "vol_2w_4w", "vol_2w",
            "ratio_2w_over_4w8w", "ratio_2w_over_2w4w"]
    return ScreenResult(
        primary=primary[cols],
        secondary=secondary[cols],
        all_with_metrics=metrics[cols],
    )
