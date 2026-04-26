"""KRX Open API 연결/인증 진단 스크립트.

사용법:
    python scripts/check_krx.py [YYYYMMDD]

기본 일자는 어제. KRX OpenAPI + pykrx fallback 양쪽 모두 진단합니다.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

ENDPOINTS = {
    "KOSPI":  "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}


def main():
    bas_dd = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    key = os.getenv("KRX_AUTH_KEY", "").strip()

    print("=" * 60)
    print(f"KRX Open API 진단  |  basDd={bas_dd}")
    print("=" * 60)
    if not key:
        print("❌ KRX_AUTH_KEY 환경변수가 비어 있습니다. .env 또는 export로 설정 필요.")
        sys.exit(1)
    print(f"✅ KRX_AUTH_KEY 길이: {len(key)} chars (앞5={key[:5]}…)")
    print()

    headers = {"AUTH_KEY": key, "Accept": "application/json"}
    for market, url in ENDPOINTS.items():
        print(f"--- {market} : {url}")
        try:
            r = requests.get(url, params={"basDd": bas_dd}, headers=headers, timeout=20)
            print(f"  status: {r.status_code}")
            print(f"  ctype:  {r.headers.get('content-type')}")
            body = r.text[:400]
            print(f"  body:   {body}")
            if r.status_code == 200:
                try:
                    j = r.json()
                    rows = j.get("OutBlock_1") or j.get("OutBlock_2") or []
                    print(f"  ✅ rows={len(rows)} (첫 행: {rows[0] if rows else '없음'})")
                except Exception as e:
                    print(f"  ⚠ JSON 파싱 실패: {e}")
            elif r.status_code == 401:
                print("  ❌ 인증 실패. 키가 잘못되었거나 승인 대기 중일 수 있음.")
            elif r.status_code == 403:
                print("  ❌ 접근 차단. 키 권한 또는 IP 화이트리스트 확인 필요.")
            elif r.status_code == 404:
                print("  ❌ endpoint URL이 변경되었을 가능성. KRX 콘솔의 'API 명세' 확인.")
        except requests.exceptions.RequestException as e:
            print(f"  ❌ 통신 실패: {e}")
        print()


def check_pykrx_fallback(bas_dd: str):
    """pykrx fallback 경로 진단 (Naver Finance 경유 — 한국 외 IP에서도 동작해야 함)."""
    print("=" * 60)
    print(f"pykrx fallback 진단  |  basDd={bas_dd}")
    print("=" * 60)
    try:
        from pykrx import stock as krx
        print("✅ pykrx import 성공")
    except ImportError:
        print("❌ pykrx 미설치 — pip install pykrx")
        return

    # 삼성전자 1일치 테스트 (Naver Finance 경유)
    try:
        df = krx.get_market_ohlcv_by_date(bas_dd, bas_dd, "005930")
        if df is not None and not df.empty:
            print(f"✅ get_market_ohlcv_by_date (삼성전자): {len(df)}행 정상")
            print(f"   columns: {list(df.columns)}")
        else:
            print(f"⚠ get_market_ohlcv_by_date (삼성전자): 빈 응답 (휴장일일 수 있음)")
    except Exception as e:
        print(f"❌ get_market_ohlcv_by_date 실패: {e}")
    print()


if __name__ == "__main__":
    main()
    check_pykrx_fallback(
        (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        if len(sys.argv) < 2 else sys.argv[1]
    )
