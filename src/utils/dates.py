"""영업일 계산 유틸. KRX 휴장일 정확한 캘린더는 pykrx의 get_previous_business_day를 사용."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import contextlib
import io
import os

# pykrx import 시 출력되는 KRX 로그인 시도 메시지 억제
# (KRX_ID/KRX_PW 환경변수가 없을 때 stdout에 경고를 print 함)
try:
    if os.getenv("KRX_ID") and os.getenv("KRX_PW"):
        # 자격증명 있으면 정상 import (로그인 성공 메시지만 출력)
        from pykrx import stock as _krx_stock
    else:
        # 자격증명 없으면 import 시 stdout 캡쳐로 메시지 숨김
        with contextlib.redirect_stdout(io.StringIO()):
            from pykrx import stock as _krx_stock
except ImportError:  # pragma: no cover
    _krx_stock = None


@dataclass
class VolumeWindows:
    """3-bucket 평균 윈도우의 시작/끝 일자.
    end는 inclusive, start는 inclusive."""
    w2_start: date    # 0~2주
    w2_end: date
    w24_start: date   # 2~4주
    w24_end: date
    w48_start: date   # 4~8주
    w48_end: date


def previous_business_day(d: date) -> date:
    """주어진 일자 이전 가장 가까운 영업일."""
    if _krx_stock is not None:
        try:
            ymd = _krx_stock.get_previous_business_day(date=d.strftime("%Y%m%d"))
            return date.fromisoformat(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
        except Exception:
            pass
    cur = d
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def compute_windows(
    trigger_date: date,
    days_2w: int = 14,
    days_2w_4w: int = 14,
    days_4w_8w: int = 28,
) -> VolumeWindows:
    """트리거 일자로부터 거꾸로 3개 평균 윈도우의 양 끝 일자 계산.
    settings.yaml의 days 값 그대로 받아서 calendar day 기반 윈도우를 만든다.
    실제 영업일 필터링은 데이터 조회 후 dataframe에서 수행."""
    anchor = previous_business_day(trigger_date - timedelta(days=1))  # T-1
    w2_end = anchor
    w2_start = w2_end - timedelta(days=days_2w - 1)

    w24_end = w2_start - timedelta(days=1)
    w24_start = w24_end - timedelta(days=days_2w_4w - 1)

    w48_end = w24_start - timedelta(days=1)
    w48_start = w48_end - timedelta(days=days_4w_8w - 1)

    return VolumeWindows(
        w2_start=w2_start, w2_end=w2_end,
        w24_start=w24_start, w24_end=w24_end,
        w48_start=w48_start, w48_end=w48_end,
    )


def fetch_range(windows: VolumeWindows) -> tuple[date, date]:
    """전체 조회 구간(가장 과거~최근). 데이터 한 번 받아서 모든 윈도우에 활용."""
    return windows.w48_start, windows.w2_end
