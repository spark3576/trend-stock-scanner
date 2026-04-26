/**
 * Trend Stock Scanner — Telegram Trigger (Cloudflare Worker)
 *
 * 텔레그램 봇이 받은 메시지(/scan)를 GitHub Actions workflow_dispatch로 변환.
 * 화이트리스트 chat_id 외 사용자는 차단.
 *
 * Bindings (wrangler secret put 으로 등록):
 *   TG_TOKEN          — Telegram Bot Token (BotFather 발급)
 *   GH_TOKEN          — GitHub PAT (workflow scope)
 *   ALLOWED_CHAT_IDS  — 허용 chat_id 콤마구분 (예: "12345678,87654321")
 *
 * Vars (wrangler.toml):
 *   GH_REPO          — "spark3576/trend-stock-scanner"
 *   GH_WORKFLOW      — "weekly-scan.yml"
 */

const COMMANDS = {
  '/scan':   'scan',
  'scan':    'scan',
  '/start':  'help',
  '/help':   'help',
  '/status': 'status',
};

async function ghDispatch(env) {
  return fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'trend-scanner-tg-bot',
      },
      body: JSON.stringify({ ref: 'main' }),
    }
  );
}

async function ghLatestRun(env) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/runs?per_page=1`,
    {
      headers: {
        'Authorization': `Bearer ${env.GH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'trend-scanner-tg-bot',
      },
    }
  );
  if (!r.ok) return null;
  const j = await r.json();
  return j.workflow_runs?.[0] ?? null;
}

async function tgSend(env, chatId, text) {
  return fetch(
    `https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: 'Markdown',
        disable_web_page_preview: true,
      }),
    }
  );
}

function siteUrl(env) {
  const [user, repo] = env.GH_REPO.split('/');
  return `https://${user}.github.io/${repo}/`;
}

export default {
  async fetch(request, env) {
    // GET = 살아있는지 확인용
    if (request.method !== 'POST') {
      return new Response('Trend Scanner Telegram Bot — alive', { status: 200 });
    }

    let update;
    try { update = await request.json(); }
    catch { return new Response('Bad JSON', { status: 400 }); }

    const msg = update.message;
    if (!msg || !msg.text) return new Response('OK');

    const chatId = String(msg.chat.id);
    const allowed = (env.ALLOWED_CHAT_IDS || '').split(',').map(s => s.trim());
    if (!allowed.includes(chatId)) {
      await tgSend(env, chatId,
        `⛔ 허용되지 않은 사용자입니다.\n` +
        `귀하의 chat_id: \`${chatId}\`\n\n` +
        `봇 운영자(@spark3576)가 화이트리스트에 추가해야 사용 가능합니다.`
      );
      return new Response('OK');
    }

    const text = msg.text.trim().toLowerCase();
    const cmd = COMMANDS[text];

    if (cmd === 'scan') {
      await tgSend(env, chatId, '⏳ *스캔 트리거 중...*');
      const r = await ghDispatch(env);
      if (r.ok || r.status === 204) {
        await tgSend(env, chatId,
          `✅ *스캔 시작!*\n\n` +
          `약 3분 후 완료 예정.\n\n` +
          `📊 [결과 사이트](${siteUrl(env)})\n` +
          `🔍 [진행 상황](https://github.com/${env.GH_REPO}/actions)\n\n` +
          `완료 후 \`/status\`로 결과 확인 가능`
        );
      } else {
        const err = await r.text();
        await tgSend(env, chatId,
          `❌ GitHub API 실패 (${r.status})\n\`\`\`\n${err.slice(0, 200)}\n\`\`\``
        );
      }
    } else if (cmd === 'status') {
      const run = await ghLatestRun(env);
      if (!run) {
        await tgSend(env, chatId, '⚠️ 워크플로우 정보를 가져오지 못했습니다.');
      } else {
        const icon = {
          completed: run.conclusion === 'success' ? '✅' : '❌',
          in_progress: '⏳', queued: '🕐', waiting: '🕐',
        }[run.status] || '❔';
        await tgSend(env, chatId,
          `${icon} *최근 실행*\n\n` +
          `상태: \`${run.status}\` ${run.conclusion ? `(${run.conclusion})` : ''}\n` +
          `시작: ${new Date(run.created_at).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})}\n` +
          `[로그 보기](${run.html_url})\n` +
          `[결과 사이트](${siteUrl(env)})`
        );
      }
    } else if (cmd === 'help') {
      await tgSend(env, chatId,
        `📈 *Trend Stock Scanner Bot*\n\n` +
        `/scan — 트렌드 스캔 즉시 실행\n` +
        `/status — 최근 실행 상태 확인\n` +
        `/help — 도움말\n\n` +
        `🌐 [사이트 바로가기](${siteUrl(env)})`
      );
    } else {
      await tgSend(env, chatId, '❓ 알 수 없는 명령입니다. `/help` 입력');
    }
    return new Response('OK');
  },
};
