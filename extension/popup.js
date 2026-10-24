// ==========================================
// POPUP SCRIPT (extension/popup.js)
// Manifest V3 - Sem inline event handlers
// ==========================================

const DEFAULT_SERVER_URL = 'http://localhost:8000';
let currentAuthTab = 'token'; // 'token' | 'creds'
let currentCaptureMode = 'new'; // 'new' | 'existing'
let activeTabInfo = { url: '', title: '' };

// Utilitários de escape HTML
function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ==========================================
// DETECÇÃO DE LOJA E HIGIENIZAÇÃO DE TÍTULO
// ==========================================
function detectStore(url) {
  if (!url) return '';
  try {
    const raw = (url.startsWith('http://') || url.startsWith('https://')) ? url : 'https://' + url;
    const parsed = new URL(raw);
    const host = parsed.hostname.toLowerCase();

    if (host.includes('mercadolivre') || host.includes('mercado-livre')) return 'Mercado Livre';
    if (host.includes('shopee')) return 'Shopee';
    if (host.includes('amazon')) return 'Amazon';
    if (host.includes('aliexpress')) return 'AliExpress';
    if (host.includes('kabum')) return 'Kabum';
    if (host.includes('pichau')) return 'Pichau';
    if (host.includes('terabyteshop') || host.includes('terabyte')) return 'Terabyte';
    if (host.includes('magazineluiza') || host.includes('magalu')) return 'Magalu';
    if (host.includes('lenovo')) return 'Lenovo';

    const cleanHost = host.replace(/^www\./, '');
    const parts = cleanHost.split('.');
    if (parts.length > 0 && parts[0]) {
      return parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
    }
  } catch (err) {}
  return '';
}

function cleanProductTitle(rawTitle) {
  if (!rawTitle) return '';
  let title = rawTitle.trim();

  const patterns = [
    /\s*\|\s*Mercado\s*Livre.*$/i,
    /\s*-\s*Mercado\s*Livre.*$/i,
    /\s*-\s*Shopee\s*Brasil.*$/i,
    /\s*\|\s*Shopee.*$/i,
    /\s*:\s*Amazon\..*$/i,
    /\s*\|\s*Amazon\..*$/i,
    /\s*\|\s*KaBuM!?.*$/i,
    /\s*\|\s*Pichau.*$/i,
    /\s*\|\s*Terabyte.*$/i,
    /\s*-\s*Magazine\s*Luiza.*$/i,
    /\s*\|\s*Magalu.*$/i,
    /\s*\|\s*Lenovo.*$/i,
    /\s*\|\s*AliExpress.*$/i,
  ];

  for (const pat of patterns) {
    title = title.replace(pat, '');
  }

  // Remove separadores soltos no final
  title = title.replace(/[\s\-\|:]+$/, '').trim();
  return title;
}

// ==========================================
// GERENCIAMENTO DE STORAGE
// ==========================================
async function getStoredData() {
  return new Promise(resolve => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(['authToken', 'serverUrl', 'userEmail', 'userName'], resolve);
    } else {
      resolve({
        authToken: localStorage.getItem('pt_authToken') || '',
        serverUrl: localStorage.getItem('pt_serverUrl') || DEFAULT_SERVER_URL,
        userEmail: localStorage.getItem('pt_userEmail') || '',
        userName: localStorage.getItem('pt_userName') || '',
      });
    }
  });
}

async function setStoredData(data) {
  return new Promise(resolve => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.set(data, resolve);
    } else {
      if (data.authToken !== undefined) localStorage.setItem('pt_authToken', data.authToken);
      if (data.serverUrl !== undefined) localStorage.setItem('pt_serverUrl', data.serverUrl);
      if (data.userEmail !== undefined) localStorage.setItem('pt_userEmail', data.userEmail);
      if (data.userName !== undefined) localStorage.setItem('pt_userName', data.userName);
      resolve();
    }
  });
}

async function clearStoredAuth() {
  return new Promise(resolve => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.remove(['authToken', 'userEmail', 'userName'], resolve);
    } else {
      localStorage.removeItem('pt_authToken');
      localStorage.removeItem('pt_userEmail');
      localStorage.removeItem('pt_userName');
      resolve();
    }
  });
}

async function getServerUrl() {
  const data = await getStoredData();
  let url = (data.serverUrl || DEFAULT_SERVER_URL).trim();
  if (url.endsWith('/')) url = url.slice(0, -1);
  return url;
}

async function getAuthToken() {
  const data = await getStoredData();
  return data.authToken || '';
}

// ==========================================
// UI FEEDBACK (NOTIFICAÇÕES & SPINNER)
// ==========================================
function showNotification(msg, type = 'info') {
  const el = document.getElementById('notification-box');
  if (!el) return;
  el.textContent = msg;
  el.className = `notification ${type}`;
}

function clearNotification() {
  const el = document.getElementById('notification-box');
  if (!el) return;
  el.textContent = '';
  el.className = 'notification hidden';
}

function showLoading(msg = 'Processando…') {
  const overlay = document.getElementById('loading-overlay');
  const msgEl = document.getElementById('loading-msg');
  if (msgEl) msgEl.textContent = msg;
  if (overlay) overlay.classList.remove('hidden');
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) overlay.classList.add('hidden');
}

// ==========================================
// CONTROLE DE TELAS E ABAS
// ==========================================
function showAuthView() {
  document.getElementById('view-auth').classList.remove('hidden');
  document.getElementById('view-capture').classList.add('hidden');
  document.getElementById('connection-status-dot').className = 'status-dot disconnected';
  document.getElementById('connection-status-dot').title = 'Desconectado';
  document.getElementById('btn-header-account').classList.add('hidden');
}

function showCaptureView(userEmail = '') {
  document.getElementById('view-auth').classList.add('hidden');
  document.getElementById('view-capture').classList.remove('hidden');
  document.getElementById('connection-status-dot').className = 'status-dot connected';
  document.getElementById('connection-status-dot').title = `Conectado como ${userEmail}`;
  document.getElementById('btn-header-account').classList.remove('hidden');
  const emailEl = document.getElementById('header-user-email');
  if (emailEl) emailEl.textContent = userEmail || 'Conectado';
}

function switchAuthSubtab(tab) {
  currentAuthTab = tab;
  clearNotification();
  const btnToken = document.getElementById('tab-btn-token');
  const btnLogin = document.getElementById('tab-btn-login');
  const subToken = document.getElementById('subview-token');
  const subCreds = document.getElementById('subview-creds');

  if (tab === 'token') {
    btnToken.classList.add('active');
    btnLogin.classList.remove('active');
    subToken.classList.remove('hidden');
    subCreds.classList.add('hidden');
  } else {
    btnLogin.classList.add('active');
    btnToken.classList.remove('active');
    subCreds.classList.remove('hidden');
    subToken.classList.add('hidden');
  }
}

function switchCaptureMode(mode) {
  currentCaptureMode = mode;
  clearNotification();
  const btnNew = document.getElementById('btn-mode-new');
  const btnExisting = document.getElementById('btn-mode-existing');
  const secNew = document.getElementById('section-new-product');
  const secExisting = document.getElementById('section-existing-product');

  if (mode === 'new') {
    btnNew.classList.add('active');
    btnExisting.classList.remove('active');
    secNew.classList.remove('hidden');
    secExisting.classList.add('hidden');
  } else {
    btnExisting.classList.add('active');
    btnNew.classList.remove('active');
    secExisting.classList.remove('hidden');
    secNew.classList.add('hidden');
  }
}

function toggleServerConfig() {
  const wrapper = document.getElementById('server-input-wrapper');
  const icon = document.getElementById('server-toggle-icon');
  if (wrapper.classList.contains('hidden')) {
    wrapper.classList.remove('hidden');
    icon.textContent = '▾';
  } else {
    wrapper.classList.add('hidden');
    icon.textContent = '▸';
  }
}

// ==========================================
// CARREGAMENTO DA ABA ATIVA DO NAVEGADOR
// ==========================================
async function queryActiveTab() {
  return new Promise(resolve => {
    if (typeof chrome !== 'undefined' && chrome.tabs && chrome.tabs.query) {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        if (tabs && tabs[0]) {
          resolve({
            url: tabs[0].url || '',
            title: tabs[0].title || '',
          });
        } else {
          resolve({ url: '', title: '' });
        }
      });
    } else {
      resolve({
        url: window.location.href,
        title: document.title,
      });
    }
  });
}

function populateCaptureFields(tabInfo) {
  activeTabInfo = tabInfo;
  const store = detectStore(tabInfo.url) || 'Loja Desconhecida';
  const cleanTitle = cleanProductTitle(tabInfo.title);

  // Badge e preview no topo
  const badgeEl = document.getElementById('detected-store-badge');
  const titleEl = document.getElementById('detected-page-title');
  if (badgeEl) badgeEl.textContent = store;
  if (titleEl) titleEl.textContent = cleanTitle || tabInfo.title || 'Sem título identificado';

  // Modo 1 (Novo)
  const inputName = document.getElementById('input-name');
  const inputStore = document.getElementById('input-store');
  const inputUrl = document.getElementById('input-url');
  if (inputName) inputName.value = cleanTitle;
  if (inputStore) inputStore.value = store;
  if (inputUrl) inputUrl.value = tabInfo.url;

  // Modo 2 (Existente)
  const inputExistingStore = document.getElementById('input-existing-store');
  const inputExistingUrl = document.getElementById('input-existing-url');
  if (inputExistingStore) inputExistingStore.value = store;
  if (inputExistingUrl) inputExistingUrl.value = tabInfo.url;
}

// ==========================================
// REQUISIÇÕES AO SERVIDOR
// ==========================================
async function testTokenAndGetUser(serverUrl, token) {
  const resp = await fetch(`${serverUrl}/api/auth/me`, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Accept': 'application/json',
    },
  });
  if (!resp.ok) {
    throw new Error('Token inválido ou expirado');
  }
  return resp.json();
}

async function loadExistingProducts(serverUrl, token) {
  const sel = document.getElementById('select-existing-product');
  if (!sel) return;
  sel.innerHTML = '<option value="">Carregando produtos cadastrados…</option>';

  try {
    const resp = await fetch(`${serverUrl}/api/products`, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/json',
      },
    });
    if (!resp.ok) throw new Error('Falha ao listar produtos');
    const products = await resp.json();
    if (!Array.isArray(products) || products.length === 0) {
      sel.innerHTML = '<option value="">Nenhum produto cadastrado ainda</option>';
      return;
    }
    sel.innerHTML = products.map(p =>
      `<option value="${p.id}">[${escapeHtml(p.category || 'Notebook')}] ${escapeHtml(p.name)}</option>`
    ).join('');
  } catch (err) {
    sel.innerHTML = '<option value="">Erro ao carregar produtos</option>';
  }
}

// ==========================================
// HANDLERS DE AÇÕES
// ==========================================
async function handleConnectSubmit() {
  clearNotification();
  let serverUrl = document.getElementById('input-server-url').value.trim() || DEFAULT_SERVER_URL;
  if (serverUrl.endsWith('/')) serverUrl = serverUrl.slice(0, -1);

  showLoading('Conectando ao servidor…');
  try {
    let token = '';
    let userEmail = '';
    let userName = '';

    if (currentAuthTab === 'token') {
      token = document.getElementById('input-auth-token').value.trim();
      if (!token) {
        throw new Error('Informe o Token da Extensão copiado da sua conta.');
      }
      const user = await testTokenAndGetUser(serverUrl, token);
      userEmail = user.email || '';
      userName = user.name || '';
    } else {
      const email = document.getElementById('input-auth-email').value.trim();
      const password = document.getElementById('input-auth-pass').value;
      if (!email || !password) {
        throw new Error('Preencha seu e-mail e senha.');
      }

      const resp = await fetch(`${serverUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const msg = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail;
        throw new Error(msg || resp.statusText || 'Credenciais inválidas');
      }

      token = data.token || data.access_token || '';
      userEmail = data.email || email;
      userName = data.name || '';
      if (!token) {
        throw new Error('Servidor não retornou token de acesso');
      }
    }

    await setStoredData({
      authToken: token,
      serverUrl: serverUrl,
      userEmail: userEmail,
      userName: userName,
    });

    document.getElementById('current-server-label').textContent = serverUrl;
    showCaptureView(userEmail);

    const tab = await queryActiveTab();
    populateCaptureFields(tab);
    await loadExistingProducts(serverUrl, token);
    showNotification('Conectado com sucesso!', 'success');
  } catch (err) {
    showNotification(err.message || 'Erro ao conectar. Verifique as informações.', 'error');
  } finally {
    hideLoading();
  }
}

async function handleDisconnect() {
  if (confirm('Deseja desconectar a extensão desta conta?')) {
    await clearStoredAuth();
    showAuthView();
    showNotification('Extensão desconectada.', 'info');
  }
}

async function handleSaveQuickAdd() {
  clearNotification();
  const serverUrl = await getServerUrl();
  const token = await getAuthToken();

  if (!token) {
    showNotification('Extensão desconectada. Faça login novamente.', 'error');
    showAuthView();
    return;
  }

  let payload = {};
  if (currentCaptureMode === 'new') {
    const name = document.getElementById('input-name').value.trim();
    const category = document.getElementById('select-category').value;
    const targetPriceVal = document.getElementById('input-target-price').value.trim();
    const store = document.getElementById('input-store').value.trim();
    const url = document.getElementById('input-url').value.trim();

    if (!name) {
      showNotification('Informe o nome do produto.', 'error');
      return;
    }
    if (!store) {
      showNotification('Informe o nome da loja.', 'error');
      return;
    }
    if (!url) {
      showNotification('Informe a URL da página.', 'error');
      return;
    }

    payload = {
      name,
      category,
      target_price: targetPriceVal ? parseFloat(targetPriceVal) : null,
      store,
      url,
    };
  } else {
    const productSelect = document.getElementById('select-existing-product');
    const productId = productSelect.value;
    const store = document.getElementById('input-existing-store').value.trim();
    const url = document.getElementById('input-existing-url').value.trim();

    if (!productId) {
      showNotification('Selecione um produto cadastrado na lista.', 'error');
      return;
    }
    if (!store) {
      showNotification('Informe o nome da loja.', 'error');
      return;
    }
    if (!url) {
      showNotification('Informe a URL da página.', 'error');
      return;
    }

    payload = {
      product_id: parseInt(productId, 10),
      store,
      url,
    };
  }

  showLoading('Adicionando ao Price Tracker…');
  try {
    const resp = await fetch(`${serverUrl}/api/extension/quick-add`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    });

    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const msg = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail;
      throw new Error(msg || resp.statusText || 'Erro ao adicionar ao rastreador');
    }

    let successMsg = data.message || 'Produto e link adicionados com sucesso!';
    if (data.backfilled || (data.shared_prices_imported && data.shared_prices_imported > 0)) {
      const count = data.shared_prices_imported ? ` (${data.shared_prices_imported} leituras)` : '';
      successMsg += ` ✨ Histórico compartilhado importado na hora!${count}`;
    } else if (data.initial_collected) {
      successMsg += ' 🚀 Preço inicial coletado na hora!';
    } else {
      successMsg += ' 🔄 Primeira coleta iniciada em segundo plano!';
    }

    showNotification(successMsg, 'success');
    const btnSave = document.getElementById('btn-save-extension');
    const btnText = document.getElementById('btn-save-text');
    if (btnSave) {
      btnSave.disabled = true;
      if (btnText) btnText.textContent = 'Adicionado com Sucesso!';
    }
  } catch (err) {
    showNotification(err.message || 'Erro ao salvar. Verifique se o servidor está ativo.', 'error');
  } finally {
    hideLoading();
  }
}

async function handleOpenInSite() {
  const serverUrl = await getServerUrl();
  let url = '';
  let title = '';
  let store = '';

  if (currentCaptureMode === 'new') {
    title = document.getElementById('input-name').value.trim();
    store = document.getElementById('input-store').value.trim();
    url = document.getElementById('input-url').value.trim();
  } else {
    store = document.getElementById('input-existing-store').value.trim();
    url = document.getElementById('input-existing-url').value.trim();
    const sel = document.getElementById('select-existing-product');
    if (sel && sel.selectedIndex >= 0 && sel.value) {
      title = sel.options[sel.selectedIndex].text;
    }
  }

  const targetUrl = `${serverUrl}/#cadastro?url=${encodeURIComponent(url)}&title=${encodeURIComponent(title)}&store=${encodeURIComponent(store)}`;
  if (typeof chrome !== 'undefined' && chrome.tabs && chrome.tabs.create) {
    chrome.tabs.create({ url: targetUrl });
  } else {
    window.open(targetUrl, '_blank');
  }
}

// ==========================================
// INICIALIZAÇÃO E REGISTRO DE EVENTOS (CSP)
// ==========================================
document.addEventListener('DOMContentLoaded', async () => {
  // 1. Registra listeners de botões (estritamente sem onclick no HTML)
  document.getElementById('tab-btn-token')?.addEventListener('click', () => switchAuthSubtab('token'));
  document.getElementById('tab-btn-login')?.addEventListener('click', () => switchAuthSubtab('creds'));
  document.getElementById('btn-mode-new')?.addEventListener('click', () => switchCaptureMode('new'));
  document.getElementById('btn-mode-existing')?.addEventListener('click', () => switchCaptureMode('existing'));
  document.getElementById('btn-toggle-server')?.addEventListener('click', toggleServerConfig);
  document.getElementById('btn-connect')?.addEventListener('click', handleConnectSubmit);
  document.getElementById('btn-header-account')?.addEventListener('click', handleDisconnect);
  document.getElementById('btn-save-extension')?.addEventListener('click', handleSaveQuickAdd);
  document.getElementById('btn-open-site')?.addEventListener('click', handleOpenInSite);

  // Auto-detect store ao colar URL manualmente
  document.getElementById('input-url')?.addEventListener('input', e => {
    const detected = detectStore(e.target.value.trim());
    const storeInput = document.getElementById('input-store');
    if (detected && storeInput && !storeInput.value.trim()) {
      storeInput.value = detected;
    }
  });

  // 2. Carrega configurações e estado salvo
  const stored = await getStoredData();
  const serverUrl = stored.serverUrl || DEFAULT_SERVER_URL;
  const serverLabel = document.getElementById('current-server-label');
  const serverInput = document.getElementById('input-server-url');
  if (serverLabel) serverLabel.textContent = serverUrl;
  if (serverInput) serverInput.value = serverUrl;

  if (stored.authToken) {
    showLoading('Verificando conexão…');
    try {
      const user = await testTokenAndGetUser(serverUrl, stored.authToken);
      showCaptureView(user.email || stored.userEmail || '');
      const tab = await queryActiveTab();
      populateCaptureFields(tab);
      await loadExistingProducts(serverUrl, stored.authToken);
    } catch (err) {
      console.warn('Sessão armazenada expirou:', err);
      showAuthView();
      showNotification('Sua sessão expirou. Conecte-se novamente.', 'info');
    } finally {
      hideLoading();
    }
  } else {
    showAuthView();
    // Preenche com aba ativa já pronta se autenticar
    const tab = await queryActiveTab();
    activeTabInfo = tab;
  }
});
