#!/usr/bin/env bash
# Telegram 봇 + Cloudflare Worker 자동 배포 스크립트
# 전제: Mac에 brew, npm 설치 (gh 설치할 때 brew 사용했으면 OK)

set -e
cd "$(dirname "$0")"

echo "🤖 Telegram 봇 + Cloudflare Worker 자동 배포"
echo ""

# ===== 0) wrangler 설치 =====
if ! command -v wrangler &>/dev/null; then
  echo "📦 wrangler 설치 (Cloudflare Workers CLI)..."
  if command -v npm &>/dev/null; then
    npm install -g wrangler
  else
    echo "❌ npm이 없습니다. 'brew install node' 후 다시 시도하세요."
    exit 1
  fi
fi
echo "✅ wrangler $(wrangler --version 2>&1 | head -1)"
echo ""

# ===== 1) 사용자 토큰 입력 (이전 입력값은 .telegram.env에 저장) =====
ENV_FILE=".telegram.env"
[ -f "$ENV_FILE" ] && source "$ENV_FILE" && echo "🔑 이전 입력값 로드"

if [ -z "${TG_TOKEN:-}" ]; then
  echo ""
  echo "─────────────────────────────────────────"
  echo "🤖 Telegram Bot Token 입력"
  echo "─────────────────────────────────────────"
  echo "발급 방법: 텔레그램에서 @BotFather 검색 → /newbot →"
  echo "  봇 이름 (예: trend-scan-bot) → username (예: trend_scan_xxxx_bot)"
  echo "  → 받은 토큰 (123456:ABC-DEF1234ghIkl...)"
  read -r -p "Bot Token: " TG_TOKEN
fi

if [ -z "${TG_CHAT_ID:-}" ]; then
  echo ""
  echo "─────────────────────────────────────────"
  echo "🆔 본인 Telegram Chat ID"
  echo "─────────────────────────────────────────"
  echo "확인 방법: 텔레그램에서 @userinfobot 검색 → 메시지 한 번 보내기 →"
  echo "  응답 'Id: 123456789' 의 숫자"
  read -r -p "Chat ID: " TG_CHAT_ID
fi

if [ -z "${GH_TOKEN:-}" ]; then
  echo ""
  echo "─────────────────────────────────────────"
  echo "🐙 GitHub Personal Access Token"
  echo "─────────────────────────────────────────"
  echo "이미 발급한 PAT가 있으면 그것 사용 (workflow scope 필수)"
  echo "신규 발급: https://github.com/settings/tokens →"
  echo "  Generate new token (classic) → ✅ repo + ✅ workflow"
  read -rs -p "GitHub PAT (입력 안 보임): " GH_TOKEN
  echo ""
fi

# .env에 저장 (.gitignore 처리)
cat > "$ENV_FILE" <<EOF
TG_TOKEN=$TG_TOKEN
TG_CHAT_ID=$TG_CHAT_ID
GH_TOKEN=$GH_TOKEN
EOF
chmod 600 "$ENV_FILE"
echo "✅ 입력값 저장됨 ($ENV_FILE)"

# ===== 2) Cloudflare 로그인 =====
echo ""
if ! wrangler whoami &>/dev/null; then
  echo "🌐 Cloudflare 로그인 (브라우저가 자동 열립니다)..."
  echo "   가입 안 되어 있으면: https://dash.cloudflare.com/sign-up (무료, 1분)"
  wrangler login
fi
echo "✅ Cloudflare 인증: $(wrangler whoami 2>&1 | tail -1)"

# ===== 3) Worker secrets 등록 =====
echo ""
echo "🔐 Worker secrets 등록..."
echo "$TG_TOKEN"   | wrangler secret put TG_TOKEN          2>&1 | tail -1
echo "$GH_TOKEN"   | wrangler secret put GH_TOKEN          2>&1 | tail -1
echo "$TG_CHAT_ID" | wrangler secret put ALLOWED_CHAT_IDS  2>&1 | tail -1

# ===== 4) Worker 배포 =====
echo ""
echo "🚀 Worker 배포..."
DEPLOY_OUTPUT=$(wrangler deploy 2>&1)
echo "$DEPLOY_OUTPUT" | tail -10

# Worker URL 추출
WORKER_URL=$(echo "$DEPLOY_OUTPUT" | grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' | head -1)
if [ -z "$WORKER_URL" ]; then
  echo "⚠ Worker URL을 자동 추출 못함. wrangler 출력에서 https://...workers.dev 주소를 찾아 수동으로:"
  read -r -p "Worker URL: " WORKER_URL
fi
echo "✅ Worker URL: $WORKER_URL"

# ===== 5) Telegram Webhook 등록 =====
echo ""
echo "🔗 Telegram webhook 등록..."
WEBHOOK_RESP=$(curl -s "https://api.telegram.org/bot$TG_TOKEN/setWebhook?url=$WORKER_URL")
if echo "$WEBHOOK_RESP" | grep -q '"ok":true'; then
  echo "✅ Webhook 등록 성공"
else
  echo "❌ Webhook 등록 실패: $WEBHOOK_RESP"
  exit 1
fi

# ===== 6) 자가 테스트 =====
echo ""
echo "🧪 자가 테스트 (봇이 본인에게 환영 메시지 발송)..."
curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\":\"$TG_CHAT_ID\",\"text\":\"🎉 *Trend Scanner Bot 활성화!*\n\n명령어:\n• /scan — 스캔 즉시 실행\n• /status — 최근 결과 확인\n• /help — 도움말\n\n사이트: https://spark3576.github.io/trend-stock-scanner/\",\"parse_mode\":\"Markdown\"}" \
  > /dev/null
echo "✅ 텔레그램에서 환영 메시지 확인하세요"

echo ""
echo "✨ 완료!"
echo ""
echo "  📱 텔레그램 봇에서 /scan 입력 → 즉시 스캔 시작"
echo "  📊 결과 사이트: https://spark3576.github.io/trend-stock-scanner/"
echo "  ⚙  Worker URL: $WORKER_URL"
echo ""
echo "  Worker 코드/설정 변경 후 재배포: wrangler deploy"
echo "  Worker 로그 실시간 보기:        wrangler tail"
