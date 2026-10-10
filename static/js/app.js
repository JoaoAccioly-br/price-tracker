// ==========================================
// APP PRINCIPAL (static/js/app.js)
// ==========================================

let productsData = [];
let selectedProductId = null;
const charts = {};

const brl = v => v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

async function api(path, opts) {
  const r = await fetch(path, opts);
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
        el.textContent = s.last_run.replace(/-/g, '/') + ' UTC';
      } else {
        el.textContent = 'Aguardando coleta';
      }
    }
  } catch (err) {}
}

// ==========================================
// CONEXÃO SSE (TEMPO REAL)
// ==========================================
const collectBtn = document.getElementById('collect-btn');
const eventSource = new EventSource('/api/collect/events');

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

// Inicialização
window.addEventListener('DOMContentLoaded', async () => {
  await router.init();
  await load();
});

