// ==========================================
// MÓDULO PRODUTOS (static/js/produtos.js)
// ==========================================

let produtosCategory = 'todos';
let produtosSearchQuery = '';

function setProdutosCategory(cat) {
  produtosCategory = cat;
  document.querySelectorAll('#produtos-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === cat);
  });
  renderProdutosView();
}

const _debouncedProdutosRender = typeof debounce === 'function' ? debounce(() => renderProdutosView(), 150) : () => renderProdutosView();

function handleProdutosSearch(val) {
  produtosSearchQuery = val;
  _debouncedProdutosRender();
}

function getFilteredProdutos() {
  return productsData.filter(p => {
    const matchCategory = produtosCategory === 'todos' ||
      (p.category || 'Notebook').toLowerCase() === produtosCategory.toLowerCase();
    const query = produtosSearchQuery.trim().toLowerCase();
    const matchQuery = !query ||
      (p.name || '').toLowerCase().includes(query) ||
      (p.category || '').toLowerCase().includes(query);
    return matchCategory && matchQuery;
  });
}

function renderProdutosView() {
  const listRoot = document.getElementById('product-menu-items');
  const countRoot = document.getElementById('prod-list-count');
  if (!listRoot || !countRoot) return;

  const filtered = getFilteredProdutos();
  countRoot.textContent = `${filtered.length} DE ${productsData.length} PRODUTOS`;

  // Preserva valor do input de busca se existir
  const searchInput = document.getElementById('produtos-search-input');
  if (searchInput && produtosSearchQuery) {
    searchInput.value = produtosSearchQuery;
  }

  // Atualiza botões de categoria
  document.querySelectorAll('#produtos-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === produtosCategory);
  });

  if (!productsData.length) {
    listRoot.innerHTML = `<p style="padding:14px; color:var(--text-muted); font-size:12.5px">Nenhum produto cadastrado.</p>`;
    const detailArea = document.getElementById('product-detail-area');
    if (detailArea) {
      detailArea.innerHTML = `<p style="padding:24px; text-align:center; color:var(--text-muted)">Cadastre um produto na aba de Adicionar Produto / Link.</p>`;
    }
    return;
  }

  if (!filtered.length) {
    listRoot.innerHTML = `
      <div style="padding:20px 10px; text-align:center; color:var(--text-muted); font-size:12.5px">
        <i class="fa-solid fa-magnifying-glass" style="font-size:18px; margin-bottom:6px; display:block"></i>
        Nenhum produto encontrado.
        <div style="margin-top:8px">
          <button class="btn-outline" style="padding:4px 8px; font-size:11px" onclick="clearProdutosFilters()">
            Limpar filtros
          </button>
        </div>
      </div>`;
    const detailArea = document.getElementById('product-detail-area');
    if (detailArea) {
      detailArea.innerHTML = `<p style="padding:24px; text-align:center; color:var(--text-muted)">Nenhum produto selecionado.</p>`;
    }
    return;
  }

  // Se o selecionado não existir nos filtrados, seleciona o primeiro filtrado
  if (!selectedProductId || !filtered.find(p => p.id === selectedProductId)) {
    selectedProductId = filtered[0].id;
  }

  // Lista lateral
  listRoot.innerHTML = filtered.map(p => {
    const isActive = p.id === selectedProductId;
    const bestP = p.best_price;
    const target = p.target_price;
    let badge = '';
    if (bestP && target) {
      if (bestP <= target) {
        badge = `<span class="status-badge status-hit"><i class="fa-solid fa-check" style="font-size:9px; margin-right:3px"></i> Alvo atingido</span>`;
      } else {
        const diffPct = (((bestP - target) / target) * 100).toFixed(1);
        badge = `<span class="status-badge status-above">${diffPct}% acima do alvo</span>`;
      }
    }
    let dealBadge = '';
    if (p.analysis && p.analysis.label) {
      dealBadge = `<div style="margin-top:3px"><span class="deal-badge ${escapeHtml(p.analysis.badge_class || '')}" title="${escapeHtml(p.analysis.description || '')}">${escapeHtml(p.analysis.label)}</span></div>`;
    }
    const iconCls = typeof getCategoryIcon === 'function' ? getCategoryIcon(p.category) : 'fa-solid fa-laptop';

    return `
      <div class="product-menu-item ${isActive ? 'active' : ''}" onclick="selectProductInDetail(${p.id})">
        <div class="p-item-title">
          <i class="${iconCls}" style="margin-right:6px; font-size:12px; color:var(--text-muted)"></i>
          ${escapeHtml(p.name)}
        </div>
        <div class="p-item-sub">Melhor <b>${brl(bestP)}</b> · ${escapeHtml(p.best_store || 'loja')}</div>
        <div>${badge}${dealBadge}</div>
      </div>
    `;
  }).join('');

  // Painel direito com detalhes
  renderProductDetailCard();
}

function clearProdutosFilters() {
  produtosCategory = 'todos';
  produtosSearchQuery = '';
  const searchInput = document.getElementById('produtos-search-input');
  if (searchInput) searchInput.value = '';
  document.querySelectorAll('#produtos-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === 'todos');
  });
  renderProdutosView();
}

function selectProductInDetail(pid) {
  selectedProductId = pid;
  renderProdutosView();
}

function selectProductAndNavigate(pid) {
  selectedProductId = pid;
  router.navigate('produtos');
}

function renderProductDetailCard() {
  const detailArea = document.getElementById('product-detail-area');
  if (!detailArea) return;

  const p = productsData.find(item => item.id === selectedProductId) || productsData[0];
  if (!p) return;

  const bestP = p.best_price;
  const target = p.target_price;
  let statusBadge = '';
  if (bestP && target) {
    if (bestP <= target) {
      statusBadge = `<span class="status-badge status-hit"><i class="fa-solid fa-check" style="font-size:10px; margin-right:3px"></i> Alvo atingido</span>`;
    } else {
      const diffPct = (((bestP - target) / target) * 100).toFixed(1);
      statusBadge = `<span class="status-badge status-above">${diffPct}% acima do alvo</span>`;
    }
  }

  let analysisBox = '';
  if (p.analysis && p.analysis.verdict) {
    const a = p.analysis;
    const stats = [];
    if (a.avg_30d) stats.push(`Média 30d: <b>${brl(a.avg_30d)}</b>`);
    if (a.min_30d) stats.push(`Mín 30d: <b>${brl(a.min_30d)}</b>`);
    if (a.avg_60d) stats.push(`Média 60d: <b>${brl(a.avg_60d)}</b>`);
    if (a.min_60d) stats.push(`Mín 60d: <b>${brl(a.min_60d)}</b>`);

    analysisBox = `
      <div class="analysis-card-box">
        <div class="analysis-header">
          <span style="font-weight:700; font-size:12px; color:var(--text-title)"><i class="fa-solid fa-chart-line" style="margin-right:5px; color:var(--accent-red)"></i> Termômetro de Promoção (30 & 60 dias)</span>
          <span class="deal-badge ${escapeHtml(a.badge_class || '')}">${escapeHtml(a.label)}</span>
        </div>
        <div class="analysis-desc">${escapeHtml(a.description)}</div>
        ${stats.length ? `<div class="analysis-stats-row">${stats.join(' · ')}</div>` : ''}
      </div>
    `;
  }

  const iconCls = typeof getCategoryIcon === 'function' ? getCategoryIcon(p.category) : 'fa-solid fa-laptop';

  detailArea.innerHTML = `
    <div class="detail-header">
      <div class="detail-header-left">
        <div class="prod-icon"><i class="${iconCls}"></i></div>
        <div>
          <div style="display:flex; align-items:center; gap:8px">
            <h3>${escapeHtml(p.name)}</h3>
            <span style="font-size:11px; background:#fae9e9; color:var(--accent-red); padding:2px 8px; border-radius:99px; font-weight:700">${escapeHtml(p.category || 'Notebook')}</span>
          </div>
          <div class="sub">Melhor preço: <b>${brl(bestP)}</b> · alvo ${target ? brl(target) : 'não definido'}</div>
        </div>
      </div>
      <div>${statusBadge}</div>
    </div>

    ${analysisBox}

    <table class="listings-table">
      <thead>
        <tr>
          <th>Loja</th>
          <th style="text-align:right">Preço</th>
          <th style="text-align:right">Diferença</th>
          <th style="text-align:center; width:40px">Ações</th>
        </tr>
      </thead>
      <tbody>
        ${(p.listings || []).map(l => {
          const storeCls = getStoreClass(l.store);
          const storeInit = l.store ? l.store[0].toUpperCase() : '?';
          const effP = l.final_price || l.last_price;
          const isBest = bestP && effP && effP === bestP;
          let diffStr = '—';
          if (bestP && effP && effP > bestP) {
            diffStr = `+ ${brl(effP - bestP)}`;
          }
          const hasCoupon = l.coupon_code && l.final_price && l.final_price < l.last_price;

          return `
            <tr>
              <td>
                <div class="store-cell">
                  <span class="store-pill ${storeCls}">
                    <span class="store-avatar">${escapeHtml(storeInit)}</span>
                  </span>
                  <div class="store-info">
                    <a href="${safeUrl(l.url)}" target="_blank" rel="noopener noreferrer">
                      ${escapeHtml(l.store)} <i class="fa-solid fa-arrow-up-right-from-square" style="font-size:11px"></i>
                    </a>
                    ${isBest ? `<span class="tag-menor"><i class="fa-solid fa-tag" style="font-size:9px"></i> Menor preço</span>` : ''}
                    ${l.coupon_code ? `<span class="coupon-chip"><i class="fa-solid fa-ticket" style="font-size:9px"></i> ${escapeHtml(l.coupon_code)}</span>` : ''}
                    <div class="meta">${l.checked_at ? 'lido em ' + escapeHtml(formatDateTimeBR(l.checked_at)) : 'ainda não coletado'}</div>
                  </div>
                </div>
              </td>
              <td class="price-cell">
                ${hasCoupon ? `<span style="text-decoration:line-through; color:var(--text-muted); font-size:12px; margin-right:4px">${brl(l.last_price)}</span>` : ''}
                ${brl(effP)}
                ${l.available === 0 ? `
                  <div style="margin-top: 4px;">
                    <button type="button" class="badge-heal-btn" onclick="handleHealListing(${l.id}, this)" title="Buscar novo anúncio ativo nesta loja com IA">
                      <i class="fa-solid fa-wand-magic-sparkles"></i> Indisponível · Renovar link com IA
                    </button>
                  </div>` : ''}
              </td>
              <td class="diff-cell">${diffStr}</td>
              <td style="text-align:center">
                <div style="display:flex; gap:6px; justify-content:center; align-items:center">
                  <button onclick="handleHealListing(${l.id}, this)" class="btn-outline" style="padding:4px 8px; font-size:11px; color:#229ED9; border-color:#d4ebf7" title="Buscar novo anúncio ativo nesta loja com IA">
                    <i class="fa-solid fa-wand-magic-sparkles"></i>
                  </button>
                  <button onclick="handleDeleteListingDetail(${l.id})" class="btn-outline" style="padding:4px 8px; font-size:11px; color:var(--accent-red); border-color:#fae9e9" title="Excluir este link">
                    <i class="fa-solid fa-trash"></i>
                  </button>
                </div>
              </td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>

    <div style="margin-top: 20px; display:flex; gap:10px; flex-wrap:wrap">
      <button class="btn-primary" onclick="openAIDiscoverModal(${p.id})" style="background: linear-gradient(135deg, var(--accent-red), #d64567);">
        <i class="fa-solid fa-wand-magic-sparkles"></i>
        <span>Buscar em mais lojas com IA</span>
      </button>
      <button class="btn-outline" id="chart-toggle-btn" onclick="toggleProductChart(${p.id})">
        <i class="fa-solid fa-chart-line"></i>
        <span>Ver histórico</span>
      </button>
      <button class="btn-outline" onclick="openEditRuleModal(${p.id})">
        <i class="fa-solid fa-bell"></i>
        <span>Configurar alerta</span>
      </button>
      <button class="btn-outline" onclick="handleDeleteProduct(${p.id})" style="color:var(--accent-red); border-color:#fae9e9; margin-left:auto">
        <i class="fa-solid fa-trash-can"></i>
        <span>Excluir produto</span>
      </button>
    </div>
    <div class="chart-box" id="chart-wrap" hidden style="margin-top:16px">
      <canvas id="product-canvas"></canvas>
    </div>
  `;
}

async function toggleProductChart(pid) {
  const wrap = document.getElementById('chart-wrap');
  const btn = document.getElementById('chart-toggle-btn');
  if (!wrap || !btn) return;

  if (!wrap.hidden) {
    wrap.hidden = true;
    btn.innerHTML = '<i class="fa-solid fa-chart-line"></i> <span>Ver histórico</span>';
    return;
  }
  wrap.hidden = false;
  btn.innerHTML = '<i class="fa-solid fa-eye-slash"></i> <span>Ocultar histórico</span>';

  const rows = await api(`/api/products/${pid}/history`);
  const canvas = document.getElementById('product-canvas');
  if (!canvas) return;

  const labels = [...new Set(rows.map(r => r.checked_at))].sort();
  const stores = [...new Set(rows.map(r => r.store))];
  const priceIndex = new Map();
  rows.forEach(r => {
    priceIndex.set(`${r.store}::${r.checked_at}`, r.final_price || r.price);
  });
  const datasets = stores.map(s => ({
    label: s,
    spanGaps: true,
    tension: 0.25,
    data: labels.map(t => {
      const val = priceIndex.get(`${s}::${t}`);
      return val !== undefined ? val : null;
    }),
  }));

  if (charts['detail']) charts['detail'].destroy();
  charts['detail'] = new Chart(canvas, {
    type: 'line',
    data: {labels, datasets},
    options: {
      scales: {y: {ticks: {callback: v => brl(v)}}},
      plugins: {tooltip: {callbacks: {label: c => `${c.dataset.label}: ${brl(c.parsed.y)}`}}}
    }
  });
}

async function handleDeleteProduct(pid) {
  if (!confirm('Deseja realmente excluir este produto e todos os links/histórico dele?')) return;
  try {
    const res = await fetch(`/api/products/${pid}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao excluir produto');
    selectedProductId = null;
    await load();
    renderProdutosView();
  } catch (err) {
    alert('Erro ao excluir produto: ' + err.message);
  }
}

async function handleDeleteListingDetail(lid) {
  if (!confirm('Deseja excluir este link de compra?')) return;
  try {
    const res = await fetch(`/api/listings/${lid}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao excluir link');
    await load();
    renderProdutosView();
  } catch (err) {
    alert('Erro ao excluir link: ' + err.message);
  }
}

// ==========================================
// AUTO-RECUPERAÇÃO DE LINK COM IA (HEAL)
// ==========================================
async function handleHealListing(lid, btnEl) {
  if (!confirm('Deseja que a IA busque outro anúncio ativo deste produto na mesma loja para substituir este link?')) {
    return;
  }

  const origHtml = btnEl.innerHTML;
  btnEl.disabled = true;
  btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';

  try {
    const res = await api(`/api/listings/${lid}/heal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    await load();
    renderProdutosView();
    const priceMsg = res.price ? `\nPreço coletado: ${brl(res.price)}` : '';
    alert(`Link renovado com sucesso via IA!\n\nNovo anúncio: ${res.title || 'Anúncio ativo'}${priceMsg}`);
  } catch (err) {
    alert('Não foi possível renovar o link com IA: ' + err.message);
    btnEl.disabled = false;
    btnEl.innerHTML = origHtml;
  }
}

// ==========================================
// MODAL DE DESCOBERTA DE LINKS COM IA
// ==========================================
function openAIDiscoverModal(productId) {
  const modal = document.getElementById('modal-ai-discover');
  if (!modal) return;

  const p = productsData.find(item => item.id === productId);
  if (!p) return;

  const idInput = document.getElementById('modal-ai-product-id');
  const nameEl = document.getElementById('modal-ai-product-name');
  const storeSel = document.getElementById('modal-ai-store-select');

  if (idInput) idInput.value = p.id;
  if (nameEl) nameEl.textContent = `[${p.category || 'Notebook'}] ${p.name}`;
  if (storeSel) storeSel.value = '';

  modal.classList.remove('hidden');
  runAIDiscoverInModal();
}

function closeAIDiscoverModal() {
  const modal = document.getElementById('modal-ai-discover');
  if (modal) {
    modal.classList.add('hidden');
  }
}

async function runAIDiscoverInModal() {
  const idInput = document.getElementById('modal-ai-product-id');
  const storeSel = document.getElementById('modal-ai-store-select');
  const container = document.getElementById('modal-ai-results-container');
  if (!idInput || !container) return;

  const productId = +idInput.value;
  const store = storeSel ? storeSel.value.trim() || null : null;
  const p = productsData.find(item => item.id === productId);

  if (typeof performAIDiscover === 'function') {
    await performAIDiscover({
      product_id: productId,
      product_name: p ? p.name : undefined,
      category: p ? p.category : undefined,
      store: store,
    }, '#modal-ai-results-container', '#btn-modal-ai-search');
  }
}

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeAIDiscoverModal();
  }
});


