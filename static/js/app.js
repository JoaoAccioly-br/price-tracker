// ==========================================
// APP PRINCIPAL (static/js/app.js)
// ==========================================

let currentUser = null;
let productsData = [];
let selectedProductId = null;
const charts = {};
let eventSource = null;
let routerInitialized = false;

const brl = v => v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

async function api(path, opts = {}) {
  const mergedOpts = {
    credentials: 'same-origin',
    ...opts,
  };
  const r = await fetch(path, mergedOpts);
  if (r.status === 401 && path !== '/api/auth/me' && path !== '/api/auth/login') {
    showAuthOverlay('login');
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    // erros de validação do FastAPI vêm como lista: [{msg: "..."}]
    const detail = Array.isArray(body.detail) ? body.detail.map(d => d.msg).join('; ') : body.detail;
    throw new Error(detail || r.statusText);
  }
  return r.json();
}

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

function getStoreClass(store) {
  const s = (store || '').toLowerCase();
  if (s.includes('kabum')) return 'store-kabum';
  if (s.includes('amazon')) return 'store-amazon';
  if (s.includes('lenovo')) return 'store-lenovo';
  if (s.includes('magalu')) return 'store-magalu';
  if (s.includes('shopee')) return 'store-shopee';
  if (s.includes('mercado') || s.includes('ml')) return 'store-ml';
  if (s.includes('ali') || s.includes('aliexpress')) return 'store-ali';
  return 'store-kabum';
}

function getCategoryIcon(cat) {
  const c = (cat || '').toLowerCase();
  if (c.includes('notebook') || c.includes('laptop')) return 'fa-solid fa-laptop';
  if (c.includes('monitor') || c.includes('tela') || c.includes('tv')) return 'fa-solid fa-desktop';
  if (c.includes('periférico') || c.includes('periferico') || c.includes('teclado') || c.includes('mouse') || c.includes('fone')) return 'fa-solid fa-keyboard';
  if (c.includes('hardware') || c.includes('placa') || c.includes('gpu') || c.includes('cpu') || c.includes('ssd') || c.includes('memoria')) return 'fa-solid fa-microchip';
  if (c.includes('smart') || c.includes('celular') || c.includes('telefone')) return 'fa-solid fa-mobile-screen';
  return 'fa-solid fa-box';
}

// Configuração das telas do aplicativo
const routes = {
  painel: {
    template: '/views/painel.html',
    title: 'Painel',
    subtitle: 'Os melhores preços encontrados hoje, entre todas as lojas.',
    onRender: () => renderPainelView(),
  },
  produtos: {
    template: '/views/produtos.html',
    title: 'Produtos',
    subtitle: 'Compare o preço de cada produto, loja por loja.',
    onRender: () => renderProdutosView(),
  },
  cadastro: {
    template: '/views/cadastro.html',
    title: 'Adicionar Produto / Link',
    subtitle: 'Cadastre novos notebooks e vincule lojas para acompanhamento.',
    onRender: () => renderCadastroView(),
  },
  alertas: {
    template: '/views/alertas.html',
    title: 'Alertas de Preço',
    subtitle: 'Acompanhe a margem de cada produto em relação ao seu preço alvo.',
    onRender: () => renderAlertasView(),
  },
  historico: {
    template: '/views/historico.html',
    title: 'Histórico de Leituras',
    subtitle: 'Registro cronológico de todas as coletas de preços realizadas.',
    onRender: () => renderHistoricoView(),
  },
  lojas: {
    template: '/views/lojas.html',
    title: 'Lojas Monitoradas',
    subtitle: 'Relação dos e-commerces e links atualmente rastreados.',
    onRender: () => renderLojasView(),
  },
  conta: {
    template: '/views/conta.html',
    title: 'Minha Conta',
    subtitle: 'Gerencie seus dados pessoais, notificações do Telegram e senha de acesso.',
    onRender: () => renderContaView(),
  },
};

const router = new ViewRouter(routes, 'painel');

async function load() {
  try {
    productsData = await api('/api/products');
    updateLastRead();
    if (productsData.length && (!selectedProductId || !productsData.find(p => p.id === selectedProductId))) {
      selectedProductId = productsData[0].id;
    }
    router.refreshCurrent();
  } catch (err) {
    console.error('Erro ao buscar produtos:', err);
  }
}

async function updateLastRead() {
  try {
    const s = await api('/api/collect/status');
    const el = document.getElementById('last-read-val');
    if (el) {
      if (s.last_run) {
        el.textContent = formatDateTimeBR(s.last_run);
      } else {
        el.textContent = 'Aguardando coleta';
      }
    }
  } catch (err) {}
}

// ==========================================
// CONEXÃO SSE (TEMPO REAL)
// ==========================================
function initSSE() {
  if (eventSource) {
    try {
      eventSource.close();
    } catch (err) {}
    eventSource = null;
  }

  const collectBtn = document.getElementById('collect-btn');
  try {
    eventSource = new EventSource('/api/collect/events');

    eventSource.onmessage = e => {
      try {
        const data = JSON.parse(e.data);
        if (!collectBtn) return;
        const span = collectBtn.querySelector('span');

        if (data.event === 'start' || (data.event === 'status' && data.running)) {
          collectBtn.disabled = true;
          if (span) span.textContent = data.total ? `Coletando… (${data.current}/${data.total})` : 'Coletando…';
        } else if (data.event === 'progress') {
          collectBtn.disabled = true;
          if (span) span.textContent = `Coletando… (${data.current}/${data.total})`;
        } else if (data.event === 'done') {
          collectBtn.disabled = false;
          if (span) span.textContent = 'Atualizar preços agora';
          load();
        }
      } catch (err) {}
    };

    eventSource.onerror = () => {
      // Reconexão automática pelo navegador
    };
  } catch (err) {
    console.warn('Não foi possível inicializar SSE:', err);
  }
}

const collectBtn = document.getElementById('collect-btn');
if (collectBtn) {
  collectBtn.onclick = async () => {
    collectBtn.disabled = true;
    const span = collectBtn.querySelector('span');
    if (span) span.textContent = 'Iniciando coleta…';
    try {
      await post('/api/collect', {});
    } catch (err) {
      alert('Erro ao disparar coleta: ' + err.message);
      collectBtn.disabled = false;
      if (span) span.textContent = 'Atualizar preços agora';
    }
  };
}

// ==========================================
// AUTENTICAÇÃO E PERFIL DO USUÁRIO
// ==========================================

function updateSidebarUser() {
  const box = document.getElementById('sidebar-user-box');
  const nameEl = document.getElementById('sidebar-user-name');
  const emailEl = document.getElementById('sidebar-user-email');
  const avatarEl = document.getElementById('sidebar-user-avatar');

  if (currentUser) {
    if (box) box.style.display = 'flex';
    const displayName = currentUser.name || currentUser.email || 'Usuário';
    if (nameEl) nameEl.textContent = displayName;
    if (emailEl) emailEl.textContent = currentUser.email || '';
    if (avatarEl) {
      const initial = (currentUser.name ? currentUser.name[0] : (currentUser.email ? currentUser.email[0] : 'U')).toUpperCase();
      avatarEl.textContent = initial;
    }
  } else {
    if (box) box.style.display = 'none';
    if (nameEl) nameEl.textContent = '';
    if (emailEl) emailEl.textContent = '';
    if (avatarEl) avatarEl.textContent = 'U';
  }
}

function showAuthOverlay(tab = 'login') {
  const overlay = document.getElementById('auth-overlay');
  if (overlay) {
    overlay.classList.remove('hidden');
  }
  switchAuthTab(tab);
}

function hideAuthOverlay() {
  const overlay = document.getElementById('auth-overlay');
  if (overlay) {
    overlay.classList.add('hidden');
  }
}

function switchAuthTab(tab) {
  const formLogin = document.getElementById('form-login');
  const formRegister = document.getElementById('form-register');
  const tabLogin = document.getElementById('tab-btn-login');
  const tabRegister = document.getElementById('tab-btn-register');
  const errorEl = document.getElementById('auth-error-msg');

  if (errorEl) {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  }

  if (tab === 'register') {
    if (formLogin) formLogin.classList.add('hidden');
    if (formRegister) formRegister.classList.remove('hidden');
    if (tabLogin) tabLogin.classList.remove('active');
    if (tabRegister) tabRegister.classList.add('active');
  } else {
    if (formRegister) formRegister.classList.add('hidden');
    if (formLogin) formLogin.classList.remove('hidden');
    if (tabRegister) tabRegister.classList.remove('active');
    if (tabLogin) tabLogin.classList.add('active');
  }
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const submitBtn = document.getElementById('login-submit-btn');
  const errorEl = document.getElementById('auth-error-msg');

  if (errorEl) {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  }

  const email = form.email.value.trim();
  const password = form.password.value;

  if (submitBtn) {
    submitBtn.disabled = true;
    const span = submitBtn.querySelector('span');
    if (span) span.textContent = 'Entrando…';
  }

  try {
    currentUser = await api('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    form.reset();
    hideAuthOverlay();
    updateSidebarUser();
    initSSE();
    if (!routerInitialized) {
      await router.init();
      routerInitialized = true;
    }
    await load();
  } catch (err) {
    if (errorEl) {
      errorEl.textContent = err.message || 'Erro ao realizar login. Verifique suas credenciais.';
      errorEl.classList.remove('hidden');
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      const span = submitBtn.querySelector('span');
      if (span) span.textContent = 'Entrar';
    }
  }
}

async function handleRegisterSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const submitBtn = document.getElementById('register-submit-btn');
  const errorEl = document.getElementById('auth-error-msg');

  if (errorEl) {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  }

  const name = form.name.value.trim();
  const email = form.email.value.trim();
  const password = form.password.value;
  const telegramInput = form.telegram_chat_id ? form.telegram_chat_id.value.trim() : '';

  if (submitBtn) {
    submitBtn.disabled = true;
    const span = submitBtn.querySelector('span');
    if (span) span.textContent = 'Criando conta…';
  }

  try {
    const payload = { name, email, password };
    if (telegramInput) {
      payload.telegram_chat_id = telegramInput;
    }
    currentUser = await api('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    form.reset();
    hideAuthOverlay();
    updateSidebarUser();
    initSSE();
    if (!routerInitialized) {
      await router.init();
      routerInitialized = true;
    }
    await load();
  } catch (err) {
    if (errorEl) {
      errorEl.textContent = err.message || 'Erro ao criar conta. Tente novamente.';
      errorEl.classList.remove('hidden');
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      const span = submitBtn.querySelector('span');
      if (span) span.textContent = 'Criar Conta';
    }
  }
}

// ==========================================
// AUTENTICAÇÃO COM GOOGLE
// ==========================================
let googleAuthInitialized = false;

async function initGoogleAuth() {
  try {
    const res = await fetch('/api/auth/config', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.google_client_id) return;

    const section = document.getElementById('google-auth-section');
    if (section) section.classList.remove('hidden');

    // Aguarda o script da Google carregar (com retry)
    let attempts = 0;
    while (attempts < 30) {
      if (window.google && window.google.accounts && window.google.accounts.id) {
        break;
      }
      await new Promise(r => setTimeout(r, 150));
      attempts++;
    }

    if (!window.google || !window.google.accounts || !window.google.accounts.id) {
      console.warn('Google Identity Services script não carregou a tempo.');
      return;
    }

    if (!googleAuthInitialized) {
      window.google.accounts.id.initialize({
        client_id: data.google_client_id,
        callback: handleGoogleCredentialResponse,
      });
      googleAuthInitialized = true;
    }

    const btnContainer = document.getElementById('google-signin-btn');
    if (btnContainer) {
      btnContainer.innerHTML = '';
      window.google.accounts.id.renderButton(btnContainer, {
        theme: 'outline',
        size: 'large',
        width: 320,
        text: 'continue_with',
        shape: 'rectangular',
        locale: 'pt-BR',
      });
    }
  } catch (err) {
    console.warn('Erro ao inicializar Google Auth:', err);
  }
}

async function handleGoogleCredentialResponse(response) {
  const errorEl = document.getElementById('auth-error-msg');
  if (errorEl) {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  }

  try {
    const res = await fetch('/api/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ credential: response.credential }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = Array.isArray(body.detail) ? body.detail.map(d => d.msg).join('; ') : body.detail;
      throw new Error(detail || res.statusText || 'Erro ao autenticar com Google');
    }

    const user = await res.json();
    currentUser = user;
    hideAuthOverlay();
    updateSidebarUser();
    initSSE();
    if (!routerInitialized) {
      await router.init();
      routerInitialized = true;
    }
    await load();
  } catch (err) {
    console.error('Falha no login com Google:', err);
    if (errorEl) {
      errorEl.textContent = err.message || 'Erro ao autenticar com Google. Tente novamente.';
      errorEl.classList.remove('hidden');
    }
  }
}

async function handleLogout() {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    });
  } catch (err) {
    console.warn('Erro ao deslogar no servidor:', err);
  } finally {
    currentUser = null;
    productsData = [];
    if (eventSource) {
      try {
        eventSource.close();
      } catch (err) {}
      eventSource = null;
    }
    updateSidebarUser();
    const container = document.getElementById('view-container');
    if (container) {
      container.innerHTML = `
        <div style="padding: 40px; text-align: center; color: var(--text-muted)">
          Faça login para visualizar seus produtos e alertas.
        </div>`;
    }
    showAuthOverlay('login');
  }
}

// ==========================================
// MODAL DE CONFIRMAÇÃO DE LOGOUT
// ==========================================
function openLogoutModal() {
  const modal = document.getElementById('modal-logout-confirm');
  if (modal) {
    modal.classList.remove('hidden');
  }
}

function closeLogoutModal() {
  const modal = document.getElementById('modal-logout-confirm');
  if (modal) {
    modal.classList.add('hidden');
  }
}

async function confirmLogout() {
  closeLogoutModal();
  await handleLogout();
}

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeLogoutModal();
    if (typeof closeAIDiscoverModal === 'function') {
      closeAIDiscoverModal();
    }
  }
});

async function checkAuthAndLoad() {
  try {
    currentUser = await api('/api/auth/me');
    hideAuthOverlay();
    updateSidebarUser();
    initSSE();
    if (!routerInitialized) {
      await router.init();
      routerInitialized = true;
    }
    await load();
  } catch (err) {
    currentUser = null;
    updateSidebarUser();
    showAuthOverlay('login');
  }
}

// Inicialização segura
window.addEventListener('DOMContentLoaded', async () => {
  initGoogleAuth();
  await checkAuthAndLoad();
});
