// ==========================================
// UTILITÁRIOS (static/js/utils.js)
// ==========================================

// Helpers de segurança contra XSS: sempre que um dado vindo da API ou do
// usuário for interpolado em HTML (innerHTML / template strings), use
// escapeHtml(). Para href/src montados a partir de URLs armazenadas, use
// safeUrl(), que só aceita http:// ou https:// e devolve '#' caso contrário.

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeUrl(url) {
  if (url === null || url === undefined) return '#';
  const trimmed = String(url).trim();
  if (/^https?:\/\//i.test(trimmed)) return escapeHtml(trimmed);
  return '#';
}

const _fmtFullBR = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

const _fmtShortBR = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});

function debounce(fn, wait = 150) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

/**
 * Converte data/hora salva em UTC para o Horário de Brasília (America/Sao_Paulo, UTC-3).
 * Exemplo: "2026-10-10 20:30:15" -> "10/10/2026 17:30:15"
 */
function formatDateTimeBR(utcStr) {
  if (!utcStr) return '';
  const clean = String(utcStr).trim();
  const iso = clean.replace(' ', 'T') + (clean.endsWith('Z') ? '' : 'Z');
  const d = new Date(iso);
  if (isNaN(d.getTime())) return clean;
  return _fmtFullBR.format(d).replace(', ', ' ');
}

/**
 * Converte data/hora salva em UTC para formato curto (Dia/Mês Hora:Minuto) em Brasília.
 * Exemplo: "2026-10-10 20:30:15" -> "10/10 17:30"
 */
function formatDateTimeShortBR(utcStr) {
  if (!utcStr) return '';
  const clean = String(utcStr).trim();
  const iso = clean.replace(' ', 'T') + (clean.endsWith('Z') ? '' : 'Z');
  const d = new Date(iso);
  if (isNaN(d.getTime())) return clean;
  return _fmtShortBR.format(d).replace(', ', ' ');
}
