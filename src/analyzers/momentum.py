"""트렌드 키워드 × 거래량 통과 종목 → 종합 모멘텀 분석.

핵심 로직:
  1) configs/keyword_theme_map.json 기반으로 트렌드 키워드를 테마(섹터)에 매핑
  2) 동일 테마의 종목들이 1차/2차 통과 종목과 교집합인지 확인
  3) 종목별 모멘텀 점수 산출:
       score = w_vol * volume_signal
             + w_trend * trend_signal
             + w_density * theme_density
  4) '근거(rationale)' 자동 생성 — 어느 키워드가 어느 출처로, 점수 얼마로 매칭됐는지
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.utils.logger import logger

KEYWORD_MAP_PATH = Path(__file__).resolve().parents[2] / "configs" / "keyword_theme_map.json"


@dataclass
class MomentumWeights:
    volume: float = 0.45
    trend: float = 0.35
    theme_density: float = 0.20


@dataclass
class Classification:
    strong: int = 75
    notable: int = 60
    watch: int = 45


# ----------------- 헬퍼 -----------------

def _load_theme_map() -> dict:
    if not KEYWORD_MAP_PATH.exists():
        logger.warning("keyword_theme_map.json 미발견 — 빈 매핑으로 진행")
        return {"themes": {}}
    return json.loads(KEYWORD_MAP_PATH.read_text(encoding="utf-8"))


def _normalize(s: str) -> str:
    """공백·하이픈·언더바 제거 + 소문자화 → 부분일치 유연하게."""
    return s.lower().replace(" ", "").replace("-", "").replace("_", "").replace("/", "")


def _match_keywords_to_themes(trend_rows: list[dict], theme_map: dict) -> dict[str, list[dict]]:
    """반환: {theme_name: [{keyword, source, score, ratio?}, ...]}.
    매칭 규칙: 공백·하이픈 무시 후 부분 일치 (예: 'AI 반도체' ↔ 'AI반도체' OK).
    """
    matches: dict[str, list[dict]] = {t: [] for t in theme_map.get("themes", {})}
    for row in trend_rows:
        kw_norm = _normalize(row["keyword"])
        if len(kw_norm) < 2:
            continue
        for theme, payload in theme_map.get("themes", {}).items():
            for ref_kw in payload.get("keywords", []):
                ref_norm = _normalize(ref_kw)
                if not ref_norm:
                    continue
                # 양방향 부분 일치
                if ref_norm in kw_norm or (len(kw_norm) >= 3 and kw_norm in ref_norm):
                    matches[theme].append(row)
                    break  # 한 테마당 1회만 카운트
    return matches


def _trend_signal_per_theme(matches: dict[str, list[dict]]) -> dict[str, float]:
    """테마별 트렌드 시그널 점수 (0~100). 매칭된 키워드 점수의 가중평균 + 소스 다양성 보너스."""
    out: dict[str, float] = {}
    for theme, rows in matches.items():
        if not rows:
            out[theme] = 0
            continue
        avg_score = sum(r["score"] for r in rows) / len(rows)
        sources = {r["source"] for r in rows}
        diversity_bonus = min(20, (len(sources) - 1) * 8)  # 2개 출처 +8, 3개 +16, 4개 +20
        out[theme] = min(100, avg_score + diversity_bonus)
    return out


def _volume_signal(row: pd.Series) -> float:
    """단일 종목의 거래량 시그널 점수 (0~100).
    2차 통과(2배 급증) → 60~100, 1차 통과만 → 30~60."""
    r2_4 = float(row.get("ratio_2w_over_2w4w", 1) or 1)
    r4_8 = float(row.get("ratio_2w_over_4w8w", 1) or 1)
    if r2_4 >= 2.0:  # 2차 통과
        return min(100, 60 + (r2_4 - 2.0) * 20)
    return min(60, 30 + (r4_8 - 1.0) * 30)  # 1차만


# ----------------- 메인 -----------------

def analyze_momentum(
    trend_rows: list[dict],
    primary_df: pd.DataFrame,
    secondary_df: pd.DataFrame,
    weights: MomentumWeights = MomentumWeights(),
    classification: Classification = Classification(),
) -> pd.DataFrame:
    """종합 분석 결과 DataFrame 반환.
    columns = [code, name, market, theme, momentum_score, classification,
               volume_signal, trend_signal, theme_density, matched_keywords, rationale]
    """
    theme_map = _load_theme_map()
    keyword_matches = _match_keywords_to_themes(trend_rows, theme_map)
    trend_signal_by_theme = _trend_signal_per_theme(keyword_matches)

    # 거래량 통과 종목 합집합 (1차 ∪ 2차) — 2차에 있으면 secondary metric을 우선
    pass_codes_secondary = set(secondary_df["code"].astype(str)) if not secondary_df.empty else set()
    pass_codes_primary = set(primary_df["code"].astype(str)) if not primary_df.empty else set()
    all_pass = pass_codes_primary | pass_codes_secondary

    if not all_pass:
        logger.warning("[Momentum] 거래량 통과 종목이 없어 분석 스킵")
        return pd.DataFrame()

    # 종목 행 lookup
    base = pd.concat([primary_df, secondary_df]).drop_duplicates("code", keep="last")
    base = base.set_index(base["code"].astype(str))

    rows: list[dict] = []
    for theme, payload in theme_map.get("themes", {}).items():
        theme_kw_matches = keyword_matches.get(theme, [])
        if not theme_kw_matches:
            continue  # 트렌드 매칭 없는 테마는 분석 대상 X (모멘텀 부재)

        theme_stocks = payload.get("stocks", [])
        # 거래량 통과 + 테마 종목 교집합
        intersect = [s for s in theme_stocks if s["code"] in all_pass]
        if not intersect:
            continue

        density = min(100, len(intersect) * 25)  # 1개=25, 4개=100
        trend_sig = trend_signal_by_theme.get(theme, 0)

        for stock in intersect:
            code = stock["code"]
            stock_row = base.loc[code]
            vol_sig = _volume_signal(stock_row)
            score = (
                weights.volume * vol_sig
                + weights.trend * trend_sig
                + weights.theme_density * density
            )
            cls = (
                "🔴 강력주목" if score >= classification.strong
                else "🟡 주목" if score >= classification.notable
                else "⚪ 관찰" if score >= classification.watch
                else "—"
            )

            # 근거 텍스트 생성
            kw_summaries = [f"{m['keyword']}({m['source']}, {m['score']}점)"
                            for m in theme_kw_matches[:5]]
            rationale = (
                f"[테마: {theme}] "
                f"거래량 시그널 {vol_sig:.0f}점 "
                f"(2주avg/4-8주avg = {stock_row['ratio_2w_over_4w8w']:.2f}, "
                f"2주/2-4주 = {stock_row['ratio_2w_over_2w4w']:.2f}). "
                f"트렌드 시그널 {trend_sig:.0f}점 — 매칭 키워드: {', '.join(kw_summaries)}. "
                f"동일 테마 동시통과 {len(intersect)}종목(밀집도 {density:.0f}점). "
                f"종목 근거: {stock.get('rationale', '')}"
            )

            rows.append({
                "code": code,
                "name": stock["name"],
                "market": stock_row.get("market"),
                "close": stock_row.get("close"),
                "theme": theme,
                "momentum_score": round(score, 1),
                "classification": cls,
                "volume_signal": round(vol_sig, 1),
                "trend_signal": round(trend_sig, 1),
                "theme_density": density,
                "passed_secondary": code in pass_codes_secondary,
                "matched_keywords": ", ".join(m["keyword"] for m in theme_kw_matches[:5]),
                "rationale": rationale,
            })

    # ====== 추가: 종목명 직접 매칭 (사전에 없는 종목 보완) ======
    # 1차/2차 통과 종목명을 트렌드 키워드와 직접 부분 매칭하여
    # 사전 정의되지 않은 종목도 분석에 포함시킴.
    already_codes = {r["code"] for r in rows}
    for code in all_pass:
        if code in already_codes:
            continue
        try:
            stock_row = base.loc[code]
        except KeyError:
            continue
        stock_name = str(stock_row.get("name", "")).strip()
        if not stock_name:
            continue
        name_norm = _normalize(stock_name)

        # 이 종목명이 트렌드 키워드와 직접 매칭되는지 확인
        direct_matches: list[dict] = []
        for trow in trend_rows:
            kw_norm = _normalize(trow["keyword"])
            if len(kw_norm) < 3:
                continue
            # 종목명에 키워드가 포함되거나, 키워드에 종목명이 포함될 때
            if kw_norm in name_norm or name_norm in kw_norm:
                direct_matches.append(trow)
        if not direct_matches:
            continue

        vol_sig = _volume_signal(stock_row)
        trend_sig = min(100, sum(t["score"] for t in direct_matches[:3]) / max(1, min(3, len(direct_matches))))
        density = 25  # 사전 외 매칭이라 밀집도 낮게
        score = (
            weights.volume * vol_sig
            + weights.trend * trend_sig
            + weights.theme_density * density
        )
        cls = (
            "🔴 강력주목" if score >= classification.strong
            else "🟡 주목" if score >= classification.notable
            else "⚪ 관찰" if score >= classification.watch
            else "—"
        )
        kw_summaries = [f"{m['keyword']}({m['source']}, {m['score']}점)"
                        for m in direct_matches[:5]]
        rationale = (
            f"[직접매칭] 종목명 '{stock_name}'이 트렌드 키워드와 매칭. "
            f"거래량 시그널 {vol_sig:.0f}점 "
            f"(2주avg/4-8주avg = {stock_row['ratio_2w_over_4w8w']:.2f}, "
            f"2주/2-4주 = {stock_row['ratio_2w_over_2w4w']:.2f}). "
            f"트렌드 시그널 {trend_sig:.0f}점 — 매칭: {', '.join(kw_summaries)}."
        )
        rows.append({
            "code": code,
            "name": stock_name,
            "market": stock_row.get("market"),
            "close": stock_row.get("close"),
            "theme": "[직접매칭]",
            "momentum_score": round(score, 1),
            "classification": cls,
            "volume_signal": round(vol_sig, 1),
            "trend_signal": round(trend_sig, 1),
            "theme_density": density,
            "passed_secondary": code in pass_codes_secondary,
            "matched_keywords": ", ".join(m["keyword"] for m in direct_matches[:5]),
            "rationale": rationale,
        })

    if not rows:
        logger.info("[Momentum] 트렌드와 거래량이 동시에 매칭되는 종목 없음")
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("momentum_score", ascending=False).reset_index(drop=True)
    logger.info(f"[Momentum] 분석 완료: {len(out)}개 (강력 {sum(out['momentum_score']>=classification.strong)}, "
                f"주목 {sum((out['momentum_score']>=classification.notable)&(out['momentum_score']<classification.strong))}, "
                f"관찰 {sum((out['momentum_score']>=classification.watch)&(out['momentum_score']<classification.notable))})")
    return out
