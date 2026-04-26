"""통합 진단 스크립트.
사용법:  python scripts/check_all.py

다음을 자동으로 점검합니다:
  1) .env 로드 및 키 존재 확인
  2) KRX Open API 통신/인증/응답 schema
  3) pykrx fallback 동작
  4) 네이버 데이터랩 API 통신/인증/실데이터
  5) pytrends Google Trends 동작
  6) 의존 라이브러리 import
  7) main.py dry-run 종단간 검증
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def hr(title=""):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


# ---------------- 1. .env / 키 ----------------
def check_env():
    hr("[1/7] .env 키 점검")
    keys = {
        "KRX_AUTH_KEY":         (40, True),    # KRX 키는 40자
        "NAVER_CLIENT_ID":      (10, True),
        "NAVER_CLIENT_SECRET":  (5,  True),
        "REDDIT_CLIENT_ID":     (5,  False),
        "REDDIT_CLIENT_SECRET": (10, False),
    }
    ok = True
    for k, (min_len, required) in keys.items():
        v = os.getenv(k, "").strip()
        if not v:
            mark = FAIL if required else WARN
            print(f"  {mark} {k}: 미설정")
            if required: ok = False
        elif len(v) < min_len:
            print(f"  {WARN}{k}: 길이 {len(v)} (예상 ≥{min_len}) — 키가 잘렸을 수 있음")
        else:
            print(f"  {PASS} {k}: {len(v)}자 (앞5={v[:5]}…)")
    return ok


# ---------------- 2. KRX Open API ----------------
def check_krx():
    hr("[2/7] KRX Open API 직접 호출")
    import requests
    bas_dd = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
    url = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
    try:
        r = requests.get(url, params={"basDd": bas_dd},
                         headers={"AUTH_KEY": os.getenv("KRX_AUTH_KEY", ""),
                                  "Accept": "application/json"},
                         timeout=15)
        print(f"  status: {r.status_code}, ctype: {r.headers.get('content-type')}")
        if r.status_code == 200:
            j = r.json()
            rows = j.get("OutBlock_1") or j.get("OutBlock_2") or []
            print(f"  {PASS} 응답 정상, 행 수={len(rows)}")
            if rows:
                print(f"     샘플 키: {list(rows[0].keys())[:8]}")
            return True
        else:
            print(f"  {FAIL} body: {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  {FAIL} 통신 실패: {type(e).__name__}: {str(e)[:200]}")
        return False


# ---------------- 3. pykrx fallback ----------------
def check_pykrx():
    hr("[3/7] pykrx fallback 동작")
    try:
        from pykrx import stock
        ymd = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_ticker(ymd, market="KOSPI")
        if df is None or df.empty:
            print(f"  {WARN} 데이터 없음 (휴장일일 수 있음)")
            return False
        print(f"  {PASS} pykrx OK — KOSPI {len(df)}종목 / 컬럼: {df.columns.tolist()[:5]}")
        return True
    except Exception as e:
        print(f"  {FAIL} {type(e).__name__}: {str(e)[:200]}")
        return False


# ---------------- 4. 네이버 데이터랩 ----------------
def check_naver():
    hr("[4/7] 네이버 데이터랩 API")
    import requests
    if not os.getenv("NAVER_CLIENT_ID") or not os.getenv("NAVER_CLIENT_SECRET"):
        print(f"  {WARN} 키 미설정 — 스킵")
        return None
    body = {
        "startDate": (date.today() - timedelta(days=14)).isoformat(),
        "endDate":   (date.today() - timedelta(days=1)).isoformat(),
        "timeUnit":  "date",
        "keywordGroups": [
            {"groupName": "AI반도체", "keywords": ["AI 반도체", "HBM"]},
        ],
    }
    try:
        r = requests.post("https://openapi.naver.com/v1/datalab/search",
                          headers={
                              "X-Naver-Client-Id":     os.getenv("NAVER_CLIENT_ID", ""),
                              "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET", ""),
                              "Content-Type": "application/json",
                          },
                          data=json.dumps(body), timeout=15)
        print(f"  status: {r.status_code}")
        if r.status_code == 200:
            j = r.json()
            n = len(j.get("results", [{}])[0].get("data", []))
            print(f"  {PASS} 응답 정상 — 시계열 데이터 {n}개 포인트")
            return True
        else:
            print(f"  {FAIL} body: {r.text[:300]}")
            return False
    except Exception as e:
        print(f"  {FAIL} 통신 실패: {type(e).__name__}: {str(e)[:200]}")
        return False


# ---------------- 5. pytrends ----------------
def check_pytrends():
    hr("[5/7] pytrends Google Trends")
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="ko-KR", tz=540, timeout=(10, 25))
        df = pt.trending_searches(pn="south_korea")
        if df is None or df.empty:
            print(f"  {WARN} 결과 없음 (rate limit 가능성)")
            return False
        kws = df.iloc[:, 0].head(5).tolist()
        print(f"  {PASS} 상위 5: {kws}")
        return True
    except Exception as e:
        print(f"  {WARN} {type(e).__name__}: {str(e)[:200]} (rate limit/IP 차단 — 자주 발생)")
        return False


# ---------------- 6. 의존 라이브러리 ----------------
def check_imports():
    hr("[6/7] 의존 라이브러리 import")
    deps = ["pandas", "numpy", "yaml", "openpyxl", "jinja2", "tenacity",
            "loguru", "pykrx", "pytrends", "praw", "requests", "dotenv"]
    ok = True
    for d in deps:
        try:
            __import__(d)
            print(f"  {PASS} {d}")
        except ImportError:
            print(f"  {FAIL} {d} 미설치 — pip install {d}")
            ok = False
    return ok


# ---------------- 7. main.py dry-run ----------------
def check_dry_run():
    hr("[7/7] main.py 종단간 dry-run")
    try:
        from main import run
        result = run(date.today(), skip_trends=False, dry_run=True)
        s = result["stats"]
        print(f"  {PASS} 실행 완료: trends={s['trends']}, primary={s['primary']}, "
              f"secondary={s['secondary']}, momentum={s['momentum']}")
        for k in ("excel", "html", "index"):
            print(f"     {k}: {result[k]}")
        return True
    except Exception as e:
        print(f"  {FAIL} {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


# ---------------- main ----------------
def main():
    results = {
        ".env":      check_env(),
        "KRX API":   check_krx(),
        "pykrx":     check_pykrx(),
        "Naver":     check_naver(),
        "pytrends":  check_pytrends(),
        "imports":   check_imports(),
        "dry-run":   check_dry_run(),
    }
    hr("📊 진단 결과 요약")
    for name, ok in results.items():
        if ok is True:    mark = PASS
        elif ok is False: mark = FAIL
        else:             mark = WARN
        print(f"  {mark}  {name}")
    print()
    if results["KRX API"] is True or results["pykrx"] is True:
        print(f"{PASS} 거래량 데이터 수집 가능 (KRX 또는 pykrx 작동)")
    else:
        print(f"{FAIL} 거래량 데이터 수집 불가 — 양쪽 모두 실패")
    if results["Naver"] is True or results["pytrends"] is True:
        print(f"{PASS} 트렌드 데이터 수집 가능")
    else:
        print(f"{WARN} 트렌드 소스 모두 실패 — web_search만으로 동작")


if __name__ == "__main__":
    main()
