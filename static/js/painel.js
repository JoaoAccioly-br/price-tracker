// ==========================================
// MÓDULO PAINEL (static/js/painel.js)
// ==========================================

let painelCategory = 'todos';
let painelSearchQuery = '';

function setPainelCategory(cat) {
  painelCategory = cat;
  document.querySelectorAll('#painel-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === cat);
  });
  renderPainelTable();
}

function handlePainelSearch(val) {
  painelSearchQuery = val;
  renderPainelTable();
}

function getFilteredPainelProducts() {
  return productsData.filter(p => {
    const matchCategory = painelCategory === 'todos' ||
      (p.category || 'Notebook').toLowerCase() === painelCategory.toLowerCase();
    const query = painelSearchQuery.trim().toLowerCase();
    const matchQuery = !query ||
      (p.name || '').toLowerCase().includes(query) ||
      (p.category || '').toLowerCase().includes(query);
    return matchCategory && matchQuery;
  });
}

function renderPainelView() {
  // Atualiza métricas globais dos cards (com base em todos os produtos)
  let hits = 0;
  let hitName = null;
  const storeNames = new Set();

  productsData.forEach(p => {
    (p.listings || []).forEach(l => { if (l.store) storeNames.add(l.store); });
    if (p.best_price && p.target_price && p.best_price <= p.target_price) {
      hits++;
      if (!hitName) hitName = p.name;
    }
  });

  const elHits = document.getElementById('stat-hits');
  const elHitsDesc = document.getElementById('stat-hits-desc');
  const elProds = document.getElementById('stat-prods');
  const elStoresCount = document.getElementById('stat-stores-count');
  const elStoresNames = document.getElementById('stat-stores-names');

  if (elHits) elHits.textContent = `${hits} de ${productsData.length}`;
  if (elHitsDesc) {
    elHitsDesc.textContent = hits > 0
      ? `${hitName || 'Produto'} está abaixo do alvo`
      : 'Nenhum produto abaixo do alvo';
  }
  if (elProds) elProds.textContent = productsData.length;
  if (elStoresCount) elStoresCount.textContent = storeNames.size;
  if (elStoresNames) {
    elStoresNames.textContent = storeNames.size
      ? Array.from(storeNames).slice(0, 4).join(', ')
      : 'Nenhuma loja cadastrada';
  }

  // Preserva o valor do input de pesquisa se existir
  const searchInput = document.getElementById('painel-search-input');
  if (searchInput && painelSearchQuery) {
    searchInput.value = painelSearchQuery;
  }

  // Atualiza estado visual dos botões de categoria
  document.querySelectorAll('#painel-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === painelCategory);
  });

  renderPainelTable();
}

function renderPainelTable() {
  const tbody = document.getElementById('dashboard-tbody');
  if (!tbody) return;

  const filtered = getFilteredPainelProducts();

  if (!productsData.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align:center; padding:36px; color:var(--text-muted)">
          <i class="fa-solid fa-box-open" style="font-size:24px; margin-bottom:8px; display:block"></i>
          Nenhum produto cadastrado ainda. Use a tela de <b>Adicionar Produto / Link</b>.
        </td>
      </tr>`;
    return;
  }

  if (!filtered.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align:center; padding:36px; color:var(--text-muted)">
          <i class="fa-solid fa-magnifying-glass" style="font-size:24px; margin-bottom:8px; display:block"></i>
          Nenhum produto encontrado para o filtro selecionado.
          <div style="margin-top:12px">
            <button class="btn-outline" style="padding:6px 12px; font-size:12px" onclick="clearPainelFilters()">
              Limpar filtros
            </button>
          </div>
        </td>
      </tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(p => {
    const bestP = p.best_price;
    const target = p.target_price;
    let statusBadge = '—';
    if (bestP && target) {
      if (bestP <= target) {
        statusBadge = `<span class="status-badge status-hit"><i class="fa-solid fa-check" style="font-size:10px; margin-right:4px"></i> Alvo atingido</span>`;
      } else {
        const diffPct = (((bestP - target) / target) * 100).toFixed(1);
        statusBadge = `<span class="status-badge status-above">${diffPct}% acima do alvo</span>`;
      }
    } else if (bestP) {
      statusBadge = `<span class="status-badge status-hit">Disponível</span>`;
    }

    const store = p.best_store || (p.listings && p.listings[0] ? p.listings[0].store : '—');
    const storeCls = getStoreClass(store);
    const storeInitial = store ? store[0].toUpperCase() : '?';
    const iconCls = typeof getCategoryIcon === 'function' ? getCategoryIcon(p.category) : 'fa-solid fa-laptop';

    return `
      <tr>
        <td>
          <div class="prod-cell">
            <div class="prod-icon"><i class="${iconCls}"></i></div>
            <div>
              <div style="font-weight:700; color:var(--text-title)">${p.name}</div>
              <span class="cat-badge" style="font-size:11px; color:var(--text-muted)">${p.category || 'Notebook'}</span>
            </div>
          </div>
        </td>
        <td>
          <span class="store-pill ${storeCls}">
            <span class="store-avatar">${storeInitial}</span>
            ${store}
          </span>
        </td>
        <td><span class="price-highlight">${brl(bestP)}</span></td>
        <td>${target ? brl(target) : '—'}</td>
        <td>${statusBadge}</td>
        <td>
          <button class="link-view" onclick="selectProductAndNavigate(${p.id})">
            Ver lojas <i class="fa-solid fa-arrow-right" style="font-size:11px; margin-left:3px"></i>
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function clearPainelFilters() {
  painelCategory = 'todos';
  painelSearchQuery = '';
  const searchInput = document.getElementById('painel-search-input');
  if (searchInput) searchInput.value = '';
  document.querySelectorAll('#painel-category-chips .category-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cat === 'todos');
  });
  renderPainelTable();
}
