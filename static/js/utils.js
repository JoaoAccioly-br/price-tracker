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
