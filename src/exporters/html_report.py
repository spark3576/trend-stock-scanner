"""HTML 리포트 생성기 (Jinja2) + 일자별 인덱스 자동 갱신.

생성 파일:
  archive/YYYY-MM-DD/dashboard.html   — 요약 대시보드(Chart.js)
  archive/YYYY-MM-DD/index.html       — 상세 리포트
  archive/YYYY-MM-DD/metadata.json    — 시계열 집계용 통계
  archive/index.html                  — 메인 허브 + 시계열 차트
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.utils.logger import logger

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
INDEX_TEMPLATE = "index.html.j2"
REPORT_TEMPLATE = "report.html.j2"
DASHBOARD_TEMPLATE = "dashboard.html.j2"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_report(
    out_dir: Path,
    trigger_date: date,
    trends_df: pd.DataFrame,
    primary_df: pd.DataFrame,
    secondary_df: pd.DataFrame,
    momentum_df: pd.DataFrame,
    excel_filename: str = "report.xlsx",
) -> Path:
    """일별 HTML 리포트 생성."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()
    template = env.get_template(REPORT_TEMPLATE)

    # 데이터 직렬화
    trends_records = trends_df.head(40).to_dict(orient="records") if not trends_df.empty else []
    primary_records = primary_df.head(50).to_dict(orient="records") if not primary_df.empty else []
    secondary_records = secondary_df.head(50).to_dict(orient="records") if not secondary_df.empty else []
    momentum_records = momentum_df.to_dict(orient="records") if not momentum_df.empty else []

    summary = {
        "trends_total": len(trends_df),
        "primary_total": len(primary_df),
        "secondary_total": len(secondary_df),
        "momentum_total": len(momentum_df),
        "strong": int((momentum_df["classification"] == "🔴 강력주목").sum()) if not momentum_df.empty else 0,
        "notable": int((momentum_df["classification"] == "🟡 주목").sum()) if not momentum_df.empty else 0,
        "watch": int((momentum_df["classification"] == "⚪ 관찰").sum()) if not momentum_df.empty else 0,
    }

    html = template.render(
        trigger_date=trigger_date.isoformat(),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        summary=summary,
        momentum=momentum_records,
        trends=trends_records,
        primary=primary_records,
        secondary=secondary_records,
        excel_filename=excel_filename,
    )
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"[HTML] report → {out_path}")
    return out_path


def render_dashboard(
    out_dir: Path,
    trigger_date: date,
    trends_df: pd.DataFrame,
    primary_df: pd.DataFrame,
    secondary_df: pd.DataFrame,
    momentum_df: pd.DataFrame,
    excel_filename: str = "report.xlsx",
) -> Path:
    """요약 대시보드 HTML 생성 (Chart.js 그래프 포함)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()
    template = env.get_template(DASHBOARD_TEMPLATE)

    # KPI summary
    summary = {
        "trends_total": len(trends_df),
        "primary_total": len(primary_df),
        "secondary_total": len(secondary_df),
        "momentum_total": len(momentum_df),
        "strong":  int((momentum_df["classification"] == "🔴 강력주목").sum()) if not momentum_df.empty else 0,
        "notable": int((momentum_df["classification"] == "🟡 주목").sum()) if not momentum_df.empty else 0,
        "watch":   int((momentum_df["classification"] == "⚪ 관찰").sum()) if not momentum_df.empty else 0,
    }

    # Top 10 종목 가로막대 차트 데이터
    top_n = 10
    if not momentum_df.empty:
        topdf = momentum_df.head(top_n)
        top_stocks_labels = [f"{r['name']} ({r['code']})" for _, r in topdf.iterrows()]
        top_stocks_scores = [float(r["momentum_score"]) for _, r in topdf.iterrows()]
        def _color(c):
            return "#d62828" if c == "🔴 강력주목" else "#f4a261" if c == "🟡 주목" else "#6c757d"
        top_stocks_colors = [_color(r["classification"]) for _, r in topdf.iterrows()]
    else:
        top_stocks_labels = top_stocks_scores = top_stocks_colors = []

    # 테마별 종목수
    if not momentum_df.empty:
        theme_counts_series = momentum_df["theme"].value_counts()
        theme_labels = theme_counts_series.index.tolist()
        theme_counts = theme_counts_series.values.tolist()
    else:
        theme_labels, theme_counts = [], []

    # 트렌드 소스별 키워드 수
    if not trends_df.empty and "source" in trends_df.columns:
        src_counts = trends_df["source"].value_counts()
        source_labels = src_counts.index.tolist()
        source_counts = src_counts.values.tolist()
    else:
        source_labels, source_counts = [], []

    # 트렌드 키워드 클라우드 (상위 50)
    if not trends_df.empty:
        trends_top = trends_df.head(50).to_dict(orient="records")
    else:
        trends_top = []

    # 모멘텀 표 (상위 30)
    if not momentum_df.empty:
        momentum = momentum_df.head(30).to_dict(orient="records")
    else:
        momentum = []

    html = template.render(
        trigger_date=trigger_date.isoformat(),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        summary=summary,
        momentum=momentum,
        trends_top=trends_top,
        top_stocks_labels=top_stocks_labels,
        top_stocks_scores=top_stocks_scores,
        top_stocks_colors=top_stocks_colors,
        theme_labels=theme_labels,
        theme_counts=theme_counts,
        source_labels=source_labels,
        source_counts=source_counts,
        excel_filename=excel_filename,
    )
    out_path = out_dir / "dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"[HTML] dashboard → {out_path}")

    # 시계열 집계용 metadata.json 저장
    meta_path = out_dir / "metadata.json"
    # 테마별 종목 수
    theme_dist = {}
    if not momentum_df.empty:
        theme_dist = momentum_df["theme"].value_counts().to_dict()
    # 등급별 키 종목 (Top 5)
    top_picks = []
    if not momentum_df.empty:
        for _, r in momentum_df.head(5).iterrows():
            top_picks.append({
                "code": r["code"], "name": r["name"],
                "score": float(r["momentum_score"]),
                "classification": r["classification"], "theme": r["theme"],
            })
    metadata = {
        "date": trigger_date.isoformat(),
        "trends_total": summary["trends_total"],
        "primary_total": summary["primary_total"],
        "secondary_total": summary["secondary_total"],
        "momentum_total": summary["momentum_total"],
        "strong": summary["strong"],
        "notable": summary["notable"],
        "watch": summary["watch"],
        "theme_distribution": theme_dist,
        "top_picks": top_picks,
        "trend_sources": dict(zip(source_labels, source_counts)) if source_labels else {},
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[META] {meta_path}")
    return out_path


def regenerate_index(
    archive_root: Path,
    chart_weeks: int = 26,
    list_count: int = 52,
    show_rolling: bool = True,
) -> Path:
    """archive/ 하위의 모든 일자 폴더를 스캔해 archive/index.html 갱신.
    각 일자별 dashboard/detail/excel 3종 링크 + 시계열 집계 차트 + 롤링 비교 카드.

    Parameters
    ----------
    chart_weeks : 시계열 차트에 표시할 최근 주 수 (기본 26 = 6개월)
    list_count  : 일자별 카드 목록의 최대 건수 (기본 52 = 1년치)
    show_rolling: 최근 4주 vs 직전 4주 비교 카드 표시 여부
    """
    archive_root = Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

    entries = []          # 카드 목록용 (최신순)
    series  = []          # 시계열용 (오래된순)
    for child in sorted(archive_root.iterdir()):  # 오래된순으로 처리해 시계열 일관성 유지
        if not child.is_dir():
            continue
        if not (child / "index.html").exists():
            continue
        try:
            d = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue

        # metadata.json 로드 (있으면)
        meta_path = child / "metadata.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"metadata.json 파싱 실패: {meta_path} — {e}")

        series.append({
            "date": d.isoformat(),
            "weekday": WEEKDAY_KR[d.weekday()],
            "trends": meta.get("trends_total", 0),
            "primary": meta.get("primary_total", 0),
            "secondary": meta.get("secondary_total", 0),
            "momentum": meta.get("momentum_total", 0),
            "strong": meta.get("strong", 0),
            "notable": meta.get("notable", 0),
            "watch": meta.get("watch", 0),
            "theme_distribution": meta.get("theme_distribution", {}),
            "top_picks": meta.get("top_picks", []),
        })
        entries.append({
            "date": d.isoformat(),
            "weekday": WEEKDAY_KR[d.weekday()],
            "dashboard_url": f"./{child.name}/dashboard.html" if (child / "dashboard.html").exists() else None,
            "report_url":    f"./{child.name}/index.html",
            "excel_url":     f"./{child.name}/report.xlsx" if (child / "report.xlsx").exists() else None,
            "stats": series[-1],   # 카드에 미니 통계 표시용
        })
    entries.reverse()  # 카드는 최신순

    # ---- 시계열 윈도우 적용: 최근 N주만 차트에 표시 ----
    chart_max = chart_weeks  # 1주 = 1 트리거(매주 운영 가정). 일별 운영 시에는 일수와 동일하게 동작
    series_chart = series[-chart_max:] if len(series) > chart_max else series
    series_dates = [s["date"] for s in series_chart]
    chart_data = {
        "labels": series_dates,
        "trends":   [s["trends"]    for s in series_chart],
        "primary":  [s["primary"]   for s in series_chart],
        "secondary":[s["secondary"] for s in series_chart],
        "strong":   [s["strong"]    for s in series_chart],
        "notable":  [s["notable"]   for s in series_chart],
        "watch":    [s["watch"]     for s in series_chart],
    }

    # ---- 테마 추이 (윈도우 내) ----
    all_themes: list[str] = []
    for s in series_chart:
        for t in s["theme_distribution"]:
            if t not in all_themes:
                all_themes.append(t)
    theme_totals = {t: sum(s["theme_distribution"].get(t, 0) for s in series_chart) for t in all_themes}
    top_themes = sorted(theme_totals.items(), key=lambda x: -x[1])[:6]
    theme_chart = {
        "themes": [t for t, _ in top_themes],
        "series": {t: [s["theme_distribution"].get(t, 0) for s in series_chart]
                   for t, _ in top_themes}
    }

    # ---- 롤링 비교: 최근 4주 평균 vs 직전 4주 평균 (전체 series 기준) ----
    rolling = None
    if show_rolling and len(series) >= 4:
        recent  = series[-4:]
        prior   = series[-8:-4] if len(series) >= 8 else []
        def _avg(lst, key):
            return round(sum(s[key] for s in lst) / max(1, len(lst)), 1)
        rolling = {
            "recent_strong":   _avg(recent, "strong"),
            "prior_strong":    _avg(prior, "strong") if prior else None,
            "recent_secondary":_avg(recent, "secondary"),
            "prior_secondary": _avg(prior, "secondary") if prior else None,
            "recent_trends":   _avg(recent, "trends"),
            "prior_trends":    _avg(prior, "trends") if prior else None,
            "n_recent": len(recent),
            "n_prior":  len(prior),
        }

    # ---- 카드 목록도 윈도우 적용 ----
    entries_visible = entries[:list_count]
    truncated = len(entries) - len(entries_visible)

    env = _env()
    template = env.get_template(INDEX_TEMPLATE)
    html = template.render(
        entries=entries_visible,
        truncated_count=truncated,
        total_archive_count=len(entries),
        chart_data=chart_data,
        chart_window_weeks=chart_weeks,
        theme_chart=theme_chart,
        rolling=rolling,
        has_chart_data=len(series) >= 1,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M KST"),
    )
    out_path = archive_root / "index.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"[HTML] index regenerated (cards: {len(entries_visible)}/{len(entries)}, "
                f"chart: 최근 {len(series_chart)}회, themes tracked: {len(top_themes)}) → {out_path}")
    return out_path
