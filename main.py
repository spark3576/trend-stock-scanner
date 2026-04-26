"""trend-stock-scanner 오케스트레이터.

CLI 사용법:
  python main.py                          # today 트리거, 모든 소스 사용
  python main.py --date 2026-04-26        # 특정 일자 트리거
  python main.py --skip-trends            # 트렌드 수집 스킵 (KRX만)
  python main.py --dry-run                # mock 데이터로 종단간 검증 (API 호출 X)

환경변수: .env 또는 OS env (configs/settings.example.yaml의 'API 키 발급' 참고)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

# .env 자동 로드 (있으면)
load_dotenv()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.utils.dates import compute_windows, fetch_range  # noqa
from src.utils.logger import logger  # noqa


# ---------- 설정 로드 ----------

def load_settings() -> dict:
    """settings.yaml 우선, 없으면 settings.example.yaml."""
    p_user = ROOT / "configs" / "settings.yaml"
    p_example = ROOT / "configs" / "settings.example.yaml"
    p = p_user if p_user.exists() else p_example
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ---------- Mock data (dry-run) ----------

def _mock_volume_df() -> pd.DataFrame:
    """가짜 KRX 데이터 — 8주치 일별 거래량."""
    import numpy as np
    rng = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
    sample_codes = [
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("042700", "한미반도체", "KOSDAQ"),
        ("373220", "LG에너지솔루션", "KOSPI"),
        ("034020", "두산에너빌리티", "KOSPI"),
        ("267260", "HD현대일렉트릭", "KOSPI"),
        ("196170", "알테오젠", "KOSDAQ"),
        ("141080", "리가켐바이오", "KOSDAQ"),
        ("352820", "하이브", "KOSPI"),
        ("047810", "한국항공우주", "KOSPI"),
        ("999111", "테스트노이즈A", "KOSPI"),  # 통과 X 시나리오
        ("999222", "테스트노이즈B", "KOSDAQ"),
    ]
    rows = []
    np.random.seed(42)
    for code, name, market in sample_codes:
        # 통과 시나리오: 완만한 우상향 + 최근 10일만 폭발적 급증 (2차 통과)
        if code in ("042700", "267260", "196170", "352820"):
            base = np.linspace(100_000, 180_000, len(rng))
            spike = np.where(np.arange(len(rng)) >= len(rng) - 10,
                             np.random.uniform(4.0, 6.5, len(rng)), 1.0)
            vol = (base * spike + np.random.normal(0, 5_000, len(rng))).clip(min=1000).astype(int)
        elif code in ("034020", "141080"):
            # 1차만 통과 (지속 상승, 급증은 X)
            base = np.linspace(80_000, 180_000, len(rng))
            vol = (base + np.random.normal(0, 10_000, len(rng))).clip(min=1000).astype(int)
        else:
            # 평탄/하락
            vol = np.random.randint(50_000, 200_000, len(rng))
        for d, v in zip(rng, vol):
            rows.append({
                "date": d, "code": code, "name": name, "market": market,
                "close": int(np.random.randint(10_000, 200_000)),
                "volume": int(v),
                "value": int(v * 50_000),
            })
    return pd.DataFrame(rows)


def _mock_trends() -> list[dict]:
    """가짜 트렌드 — 매핑 테마와 일치하도록 의도된 키워드."""
    return [
        {"keyword": "AI 반도체",      "source": "google", "region": "KR", "rank": 1, "score": 95},
        {"keyword": "HBM",            "source": "naver",  "region": "KR", "ratio": 2.8, "score": 84},
        {"keyword": "원전 SMR",       "source": "google", "region": "KR", "rank": 2, "score": 91},
        {"keyword": "두산에너빌리티", "source": "naver",  "region": "KR", "ratio": 2.1, "score": 63},
        {"keyword": "전력 변압기",    "source": "web",    "region": "KR", "mentions": 5, "score": 70},
        {"keyword": "ADC 항암제",     "source": "naver",  "region": "KR", "ratio": 1.9, "score": 57},
        {"keyword": "K팝 하이브",     "source": "google", "region": "KR", "rank": 5, "score": 79},
        {"keyword": "NVDA",           "source": "reddit", "region": "US", "mentions": 18, "score": 72},
    ]


# ---------- 메인 파이프라인 ----------

def run(trigger_date: date, skip_trends: bool = False, dry_run: bool = False) -> dict:
    settings = load_settings()
    logger.info(f"=== Trend Stock Scanner 시작 (trigger={trigger_date}, dry_run={dry_run}) ===")

    # 1) 거래량 데이터
    sc = settings["screening"]
    windows = compute_windows(
        trigger_date,
        days_2w=sc["vol_2w_days"],
        days_2w_4w=sc["vol_2w_4w_days"],
        days_4w_8w=sc["vol_4w_8w_days"],
    )
    logger.info(f"[Windows] 2w: {windows.w2_start}~{windows.w2_end}, "
                f"2-4w: {windows.w24_start}~{windows.w24_end}, "
                f"4-8w: {windows.w48_start}~{windows.w48_end}")

    if dry_run:
        vol_df = _mock_volume_df()
    else:
        from src.collectors.krx import fetch_volume_range, filter_universe
        start, end = fetch_range(windows)
        vol_df = fetch_volume_range(start, end)
        vol_df = filter_universe(
            vol_df,
            exclude_etf=sc["exclude_etf"],
            exclude_spac=sc["exclude_spac"],
            exclude_preferred=sc["exclude_preferred"],
        )

    # 2) 거래량 스크리닝
    from src.screeners.volume import screen_volume
    screen_result = screen_volume(
        vol_df, windows,
        primary_min_ratio=sc["primary_min_ratio"],
        secondary_ratio=sc["secondary_ratio"],
        min_avg_volume=sc["min_avg_volume"],
    )

    # 3) 트렌드 수집
    trend_rows: list[dict] = []
    if not skip_trends:
        if dry_run:
            trend_rows = _mock_trends()
        else:
            tcfg = settings["trends"]
            top_n = tcfg.get("top_n_per_source", 25)
            if tcfg.get("google", True):
                from src.collectors.google_trends import fetch_google_trends
                trend_rows.extend(fetch_google_trends(top_n=top_n))
            if tcfg.get("naver", True):
                from src.collectors.naver_datalab import fetch_naver_trends
                trend_rows.extend(fetch_naver_trends(top_n=top_n))
            if tcfg.get("reddit", True):
                from src.collectors.reddit import fetch_reddit_trends
                trend_rows.extend(fetch_reddit_trends(top_n=top_n))
            if tcfg.get("web_search", True):
                from src.collectors.web_search import fetch_web_headlines
                trend_rows.extend(fetch_web_headlines(top_n=top_n))
    trends_df = pd.DataFrame(trend_rows).sort_values("score", ascending=False) if trend_rows else pd.DataFrame()

    # 4) 모멘텀 분석
    from src.analyzers.momentum import analyze_momentum, MomentumWeights, Classification
    mw = settings["momentum"]
    momentum_df = analyze_momentum(
        trend_rows,
        screen_result.primary,
        screen_result.secondary,
        weights=MomentumWeights(
            volume=mw["weight_volume"],
            trend=mw["weight_trend"],
            theme_density=mw["weight_theme_density"],
        ),
        classification=Classification(
            strong=mw["classification"]["strong"],
            notable=mw["classification"]["notable"],
            watch=mw["classification"]["watch"],
        ),
    )

    # 5) 산출물
    out_cfg = settings["output"]
    archive_root = ROOT / out_cfg["archive_dir"]
    day_dir = archive_root / trigger_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    from src.exporters.excel import export_excel
    from src.exporters.html_report import render_report, render_dashboard, regenerate_index

    excel_path = export_excel(
        out_path=day_dir / out_cfg["excel_filename"],
        trigger_date=trigger_date,
        trends_df=trends_df,
        primary_df=screen_result.primary,
        secondary_df=screen_result.secondary,
        momentum_df=momentum_df,
    )
    html_path = render_report(
        out_dir=day_dir,
        trigger_date=trigger_date,
        trends_df=trends_df,
        primary_df=screen_result.primary,
        secondary_df=screen_result.secondary,
        momentum_df=momentum_df,
        excel_filename=out_cfg["excel_filename"],
    )
    dashboard_path = render_dashboard(
        out_dir=day_dir,
        trigger_date=trigger_date,
        trends_df=trends_df,
        primary_df=screen_result.primary,
        secondary_df=screen_result.secondary,
        momentum_df=momentum_df,
        excel_filename=out_cfg["excel_filename"],
    )
    index_path = regenerate_index(
        archive_root,
        chart_weeks=out_cfg.get("history_chart_weeks", 26),
        list_count=out_cfg.get("history_list_count", 52),
        show_rolling=out_cfg.get("show_rolling_comparison", True),
    )

    logger.info("=== 완료 ===")
    logger.info(f"  Dashboard: {dashboard_path}")
    logger.info(f"  Detail:    {html_path}")
    logger.info(f"  Excel:     {excel_path}")
    logger.info(f"  Index:     {index_path}")

    return {
        "dashboard": str(dashboard_path),
        "excel": str(excel_path),
        "html": str(html_path),
        "index": str(index_path),
        "stats": {
            "trends": len(trends_df),
            "primary": len(screen_result.primary),
            "secondary": len(screen_result.secondary),
            "momentum": len(momentum_df),
        }
    }


# ---------- CLI ----------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trend Stock Scanner")
    p.add_argument("--date", type=str, default="", help="트리거 일자 YYYY-MM-DD (기본: today)")
    p.add_argument("--skip-trends", action="store_true", help="트렌드 수집 스킵 (KRX 거래량만)")
    p.add_argument("--dry-run", action="store_true", help="Mock 데이터로 종단간 검증 (API 호출 X)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.date:
        try:
            trigger_dt = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"잘못된 날짜 형식: {args.date} (YYYY-MM-DD)")
            sys.exit(1)
    else:
        trigger_dt = date.today()

    result = run(trigger_dt, skip_trends=args.skip_trends, dry_run=args.dry_run)
    print("\n=== Stats ===")
    for k, v in result["stats"].items():
        print(f"  {k}: {v}")
    print(f"\n📊 대시보드: {result['dashboard']}")
    print(f"📄 상세:     {result['html']}")
    print(f"📥 엑셀:     {result['excel']}")
    print(f"📁 인덱스:   {result['index']}")
