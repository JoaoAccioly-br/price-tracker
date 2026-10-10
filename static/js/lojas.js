// ==========================================
// MÓDULO LOJAS (static/js/lojas.js)
// ==========================================

function renderLojasView() {
  const cardsRoot = document.getElementById('lojas-summary-cards');
  const tbody = document.getElementById('lojas-tbody');
  if (!cardsRoot || !tbody) return;

  // Agrupa listings por loja
  const storesMap = {};
  const allListings = [];

  productsData.forEach(p => {
    p.listings.forEach(l => {
      const storeName = l.store || 'Outra';
      if (!storesMap[storeName]) {
        storesMap[storeName] = { count: 0, items: [] };
      }
      storesMap[storeName].count++;
      storesMap[storeName].items.push({ ...l, product_name: p.name });
      allListings.push({ ...l, product_name: p.name });
    });
  });

  // Render cards superiores
  const storeNames = Object.keys(storesMap);
  if (!storeNames.length) {
    cardsRoot.innerHTML = `<div class="stat-card"><div class="stat-desc">Nenhuma loja cadastrada ainda.</div></div>`;
  } else {
    cardsRoot.innerHTML = storeNames.map(s => {
      const cls = getStoreClass(s);
      const count = storesMap[s].count;
      return `
        <div class="stat-card">
          <div class="stat-header">
            <span class="store-pill ${cls}" style="margin-left: -4px">
              <span class="store-avatar">${escapeHtml(s[0].toUpperCase())}</span>
              ${escapeHtml(s)}
            </span>
          </div>
          <div class="stat-value">${count}</div>
          <div class="stat-desc">${count === 1 ? 'produto monitorado' : 'produtos monitorados'}</div>
        </div>
      `;
    }).join('');
  }

  // Render tabela
  if (!allListings.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:24px; color:var(--text-muted)">Nenhum link de loja cadastrado ainda.</td></tr>`;
    return;
  }

  tbody.innerHTML = allListings.map(l => {
    const cls = getStoreClass(l.store);
    const storeInit = l.store ? l.store[0].toUpperCase() : '?';
    const effPrice = l.final_price || l.last_price;

    return `
      <tr>
        <td>
          <span class="store-pill ${cls}">
            <span class="store-avatar">${escapeHtml(storeInit)}</span>
            ${escapeHtml(l.store)}
          </span>
        </td>
        <td>
          <div class="prod-cell">
            <div class="prod-icon" style="width:28px; height:28px; font-size:12px"><i class="fa-solid fa-laptop"></i></div>
            <span>${escapeHtml(l.product_name)}</span>
          </div>
        </td>
        <td><b>${brl(effPrice)}</b></td>
        <td>
          <span style="font-size:12px; color: ${l.available === 0 ? 'var(--accent-red)' : 'var(--text-muted)'}">
            ${l.available === 0 ? '<i class="fa-solid fa-triangle-exclamation"></i> Indisponível' : '<i class="fa-solid fa-check"></i> Ativo'}
          </span>
        </td>
        <td style="color:var(--text-muted); font-size:12px">
          ${l.checked_at ? escapeHtml(l.checked_at.replace(/-/g, '/')) + ' UTC' : 'Pendente'}
        </td>
        <td>
          <div style="display:flex; gap:6px; align-items:center">
            <a href="${safeUrl(l.url)}" target="_blank" rel="noopener noreferrer" class="btn-outline" style="padding:4px 10px; font-size:12px; text-decoration:none">
              Visitar <i class="fa-solid fa-arrow-up-right-from-square" style="font-size:10px; margin-left:4px"></i>
            </a>
            <button onclick="handleDeleteListing(${l.id})" class="btn-outline" style="padding:4px 8px; font-size:12px; color:var(--accent-red); border-color:#fae9e9" title="Excluir link">
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

async function handleDeleteListing(lid) {
  if (!confirm('Deseja realmente excluir este link do rastreador?')) return;
  try {
    const res = await fetch(`/api/listings/${lid}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Erro ao excluir link');
    await load();
    renderLojasView();
  } catch (err) {
    alert('Erro ao excluir link: ' + err.message);
  }
}

