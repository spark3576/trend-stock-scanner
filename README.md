# 📈 Trend Stock Scanner

검색트렌드와 거래량 급등을 결합해 **상승 모멘텀이 있는 KR(KOSPI/KOSDAQ) 종목**을 선제적으로 스크리닝하는 자동화 파이프라인.

> ⚠️ 본 결과물은 정보 제공 목적이며 투자 권유가 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다.

---

## 🔁 파이프라인 개요

```
[1] 트렌드 키워드 수집  ─┐
   • Google Trends      │
   • 네이버 데이터랩    │
   • Reddit             │      ┌───────────────┐      ┌──────────────────┐
   • Web Search         ├──►  │ 5. 모멘텀 종합│  ──► │ 6. Excel + HTML  │
                        │      │   분석/근거   │      │    아카이빙      │
[2] 전종목 거래량 수집  │      └───────────────┘      └──────────────────┘
   • KRX Open API       │              ▲                       │
   • pykrx (fallback)   │              │                       ▼
                        │      ┌───────────────┐      ┌──────────────────┐
[3] 1차 스크리닝        ├──►  │ 4. 2차 스크리│      │ 7. GitHub Pages  │
   거래량 지속 상승     │      │   2주avg ÷    │      │   배포 + 인덱싱  │
   (8w<4w<2w 평균)      │      │   2~4w avg ≥2 │      └──────────────────┘
                        ┘      └───────────────┘
```

매주 월요일 06:00 KST 자동 실행 + 언제든 수동 트리거 가능.

---

## 📦 폴더 구조

```
trend-stock-scanner/
├── README.md
├── requirements.txt
├── .gitignore
├── main.py                       # 오케스트레이터 (CLI 진입점)
├── configs/
│   ├── settings.example.yaml     # 환경설정 템플릿
│   └── keyword_theme_map.json    # 키워드↔섹터↔종목 사전
├── src/
│   ├── collectors/               # 데이터 수집 (외부 API)
│   │   ├── krx.py                # KRX Open API + pykrx fallback
│   │   ├── google_trends.py      # pytrends
│   │   ├── naver_datalab.py      # 네이버 데이터랩 Open API
│   │   ├── reddit.py             # PRAW
│   │   └── web_search.py         # 보강용 일반 웹 검색
│   ├── screeners/
│   │   └── volume.py             # 1차/2차 거래량 스크리닝
│   ├── analyzers/
│   │   └── momentum.py           # 트렌드+거래량 종합 모멘텀 점수
│   ├── exporters/
│   │   ├── excel.py              # openpyxl 4시트 출력
│   │   └── html_report.py        # Jinja2 HTML 리포트 + 인덱스 갱신
│   └── utils/
│       ├── dates.py              # 영업일/윈도우 계산
│       └── logger.py
├── templates/
│   └── report.html.j2            # HTML 리포트 템플릿
├── archive/                      # ⭐ GitHub Pages 배포 루트
│   ├── index.html                # 일자별 리포트 인덱스 (자동 갱신)
│   └── YYYY-MM-DD/
│       ├── report.xlsx
│       └── index.html
└── .github/workflows/
    └── weekly-scan.yml           # cron(월 06:00 KST) + workflow_dispatch
```

---

## 🚀 빠른 시작 (로컬)

```bash
# 1. 가상환경 + 의존성
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 환경변수 설정 (.env 또는 export)
cp configs/settings.example.yaml configs/settings.yaml
# 필요한 키 발급 후 .env에 채우기 (아래 'API 키 발급' 참고)

# 3. 수동 실행 (트리거 일자 지정 가능)
python main.py --date 2026-04-26

# Mock 데이터 dry-run (API 키 없이 동작 검증)
python main.py --dry-run
```

---

## 🔑 API 키 발급 가이드

| 서비스 | 비용 | 발급 소요 | 필요 환경변수 |
|---|---|---|---|
| **KRX Open API** | 무료 | 최대 1영업일 (승인 필요) | `KRX_AUTH_KEY` |
| **네이버 데이터랩** | 무료 | 즉시 | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` |
| **Reddit (PRAW)** | 무료 | 즉시 | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |
| **Google Trends** | 무료 (비공식 API) | 키 불필요 | — |

### 발급 링크
- KRX Open API: <https://openapi.krx.co.kr/> → 회원가입 → MyPage → 인증키 신청
- 네이버 데이터랩: <https://developers.naver.com/apps/#/register> → 데이터랩 검색어트렌드 선택
- Reddit: <https://www.reddit.com/prefs/apps> → Create App (script 타입)

키가 없는 소스는 자동으로 skip되며, 가능한 소스만으로도 파이프라인은 실행됩니다.

---

## ⏰ 정기 스케줄 (GitHub Actions)

`.github/workflows/weekly-scan.yml`이 다음 두 가지 트리거를 지원합니다.

```yaml
on:
  schedule:
    - cron: '0 21 * * SUN'   # UTC 일요일 21시 = 월요일 06:00 KST
  workflow_dispatch:          # GitHub UI에서 'Run workflow' 클릭 시 즉시 실행
    inputs:
      date:
        description: '트리거 일자 (YYYY-MM-DD), 비우면 today'
        required: false
```

### 1회만 설정하면 되는 것 (배포 URL: `https://spark3576.github.io/trend-stock-scanner`)

```bash
# 1. 본 폴더를 GitHub 신규 리포로 push
cd trend-stock-scanner
git init
git add .
git commit -m "init: trend stock scanner v0.1"
git branch -M main
git remote add origin https://github.com/spark3576/trend-stock-scanner.git
git push -u origin main
```

2. **Settings → Pages → Source: Deploy from branch → `main` / `/archive`** 선택
3. **Settings → Secrets and variables → Actions**에 위 표의 환경변수 등록
   - `KRX_AUTH_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` (Reddit 키는 추후)
4. `Settings → Actions → General → Workflow permissions: Read and write` 체크 (커밋 권한)

이후 매주 월요일 06:00 KST 자동 실행 + 필요 시 Actions 탭에서 'Run workflow' 클릭으로 즉시 실행 가능.

---

## 📊 산출물 형식

### Excel (`archive/YYYY-MM-DD/report.xlsx`)
| 시트 | 내용 |
|---|---|
| `00_summary` | 트리거일·소스별 수집 건수·스크리닝 결과 요약 |
| `10_trends` | 키워드, 출처, 점수, 매핑 테마 |
| `20_screen1` | 1차 스크리닝(거래량 지속 상승) 통과 종목 |
| `30_screen2` | 2차 스크리닝(2배 급증) 통과 종목 |
| `40_momentum` | 트렌드×거래량 종합 모멘텀 분석 + 근거 |

### HTML 리포트 (`archive/YYYY-MM-DD/index.html`)
- 상단: 요약 카드(스캔일자, 통과종목수, 강력주목 테마수)
- 중단: 모멘텀 종합 분석 카드(테마별 종목 + 매칭 근거)
- 하단: 1차/2차 스크리닝 테이블
- 우측: 트렌드 키워드 워드클라우드

### 아카이브 인덱스 (`archive/index.html`)
일자별 리포트 링크 자동 갱신.

---

## 🧮 핵심 산식

### 거래량 윈도우 평균
- `vol_2w` = 트리거일 직전 영업일 14일 평균거래량
- `vol_2w_4w` = 트리거일 직전 15~28일 평균거래량
- `vol_4w_8w` = 트리거일 직전 29~56일 평균거래량

### 1차 필터 (거래량 지속 상승)
```
vol_4w_8w  <  vol_2w_4w  <  vol_2w
AND  vol_2w / vol_4w_8w  >  1.30   (노이즈 컷)
```

### 2차 필터 (급증)
```
vol_2w  /  vol_2w_4w  ≥  2.0
```

### 모멘텀 종합 점수 (0~100)
```
score = 0.45 * volume_signal      # 1차+2차 통과 강도
      + 0.35 * trend_signal       # 매칭된 키워드의 트렌드 강도
      + 0.20 * theme_density      # 동일 테마 내 다른 종목 동시 통과 수
```

각 종목마다 `근거(rationale)` 자동 생성: 어느 키워드가 어떤 출처에서 어떤 점수로 매칭됐는지 명시.

---

## ⚠️ 한계 / 주의사항

- KRX Open API는 **T-1** 데이터. 당일 종가/거래량은 다음 영업일 13시 이후 반영.
- 트렌드 데이터는 **후행 지표**일 수 있음 (가격이 이미 움직인 후 검색량이 따라오는 경우 다수).
- 거래량 급증은 **악재성 매도** 신호일 수도 있음 → 등락률·뉴스 교차검증 필수.
- pytrends 등 비공식 라이브러리는 IP 차단 위험이 있어 GitHub Actions IP에서 일시적 실패 가능 → 자동 retry/skip 처리.
- 본 결과물은 정보 제공 목적이며 투자 권유가 아닙니다.

---

## 📝 변경 이력

- 2026-04-26: 초기 버전 (v0.1)
