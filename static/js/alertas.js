// ==========================================
// MÓDULO ALERTAS (static/js/alertas.js)
// ==========================================

let editingProduct = null;

function renderAlertasView() {
  const container = document.getElementById('rules-list-container');
  const countEl = document.getElementById('alertas-rules-count');
  if (!container) return;

  const rulesWithTarget = productsData.filter(p => p.target_price != null);
  if (countEl) {
    countEl.textContent = `${rulesWithTarget.length} regras · ${rulesWithTarget.length} ativas`;
  }

  if (!productsData.length) {
    container.innerHTML = `
      <div style="padding:32px; text-align:center; color:var(--text-muted)">
        <i class="fa-solid fa-bell-slash" style="font-size:24px; margin-bottom:8px; display:block"></i>
        Nenhum produto cadastrado ainda.
      </div>`;
    renderDisparosRecentemente([]);
    return;
  }

  container.innerHTML = productsData.map(p => {
    const hasTarget = p.target_price != null;
    const targetVal = p.target_price || 0;
    const bestP = p.best_price;
    const isHit = bestP && hasTarget && bestP <= targetVal;

    let statusPill = '';
    if (isHit) {
      statusPill = `<span class="rule-status-pill hit">Disparado hoje</span>`;
    } else if (hasTarget) {
      statusPill = `<span class="rule-status-pill waiting">Aguardando</span>`;
    } else {
      statusPill = `<span class="rule-status-pill muted">Sem alvo</span>`;
    }

    const descText = hasTarget
      ? `Preço abaixo de ${brl(targetVal)} em qualquer loja`
      : 'Defina um alvo para ativar avisos automáticos';

    const iconCls = typeof getCategoryIcon === 'function' ? getCategoryIcon(p.category) : 'fa-solid fa-laptop';

    return `
      <div class="rule-card-item">
        <div class="rule-card-left">
          <label class="switch" onclick="event.stopPropagation()">
            <input type="checkbox" ${hasTarget ? 'checked' : ''} onchange="toggleRuleActive(${p.id}, this.checked)">
            <span class="slider"></span>
          </label>
          <div class="prod-icon" style="width:34px; height:34px; border-radius:10px; background:#fae9e9; color:var(--accent-red); display:flex; align-items:center; justify-content:center; font-size:14px; flex-shrink:0">
            <i class="${iconCls}"></i>
          </div>
          <div class="rule-info">
            <h4>${escapeHtml(p.name)}</h4>
            <p>${escapeHtml(descText)}</p>
          </div>
        </div>

        <div class="rule-card-right">
          <span class="channel-tag">E-mail</span>
          ${statusPill}
          <button class="rule-edit-btn" title="Editar regra" onclick="openEditRuleModal(${p.id})">
            <i class="fa-solid fa-pen"></i>
          </button>
        </div>
      </div>
    `;
  }).join('');

  renderDisparosRecentemente(productsData);
}

function renderDisparosRecentemente(prods) {
  const container = document.getElementById('disparos-list-container');
  if (!container) return;

  const hits = prods.filter(p => p.best_price && p.target_price && p.best_price <= p.target_price);

  if (!hits.length) {
    container.innerHTML = `
      <div style="padding:16px; text-align:center; color:var(--text-muted); font-size:12.5px">
        Nenhum alerta disparado nas últimas 24h.
      </div>`;
    return;
  }

  container.innerHTML = hits.map(p => {
    const store = p.best_store || 'loja';
    return `
      <div class="disparo-item">
        <div class="disparo-dot"></div>
        <div class="disparo-content">
          <h5>${escapeHtml(p.name)} bateu o alvo</h5>
          <div class="meta">${brl(p.best_price)} na ${escapeHtml(store)}</div>
          <div class="time">Hoje · Verificado recentemente</div>
        </div>
      </div>
    `;
  }).join('');
}

// TOGGLE SWITCH NA LISTA PRINCIPAL
async function toggleRuleActive(pid, isChecked) {
  const p = productsData.find(item => item.id === pid);
  if (!p) return;

  try {
    if (!isChecked) {
      await fetch(`/api/products/${pid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_price: null }),
      });
    } else {
      const newTarget = p.best_price ? Math.round(p.best_price * 0.95) : 3000;
      await fetch(`/api/products/${pid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_price: newTarget }),
      });
    }
    await load();
  } catch (err) {
    alert('Erro ao atualizar regra: ' + err.message);
  }
}

// FORMATADOR DE RESUMO DE PREÇOS POR LOJA
function formatStorePricesHint(p) {
  if (!p) return 'Menor preço agora: R$ —';
  const validListings = (p.listings || []).filter(l => l.last_price != null);
  if (!validListings.length) {
    return p.best_price ? `Menor preço agora: ${brl(p.best_price)} na ${p.best_store || 'loja'}` : 'Aguardando primeira leitura de preços';
  }
  const parts = validListings.map(l => `${l.store} ${brl(l.final_price || l.last_price)}`);
  return `Menor preço agora: ${parts.join(' | ')}`;
}

// CHIP DE LOJA INTERATIVO
function toggleStoreChip(btn, storeName) {
  btn.classList.toggle('active');
  const icon = btn.querySelector('i');
  if (btn.classList.contains('active')) {
    if (icon) icon.className = 'fa-solid fa-check';
  } else {
    if (icon) icon.className = 'fa-solid fa-plus';
  }
}

// GATILHOS (TRIGGER CARDS)
function selectNewRuleTrigger(type, el) {
  const group = document.getElementById('new-rule-trigger-group');
  if (group) {
    group.querySelectorAll('.trigger-card').forEach(c => c.classList.remove('active'));
  }
  if (el) el.classList.add('active');

  const inputType = document.getElementById('new-rule-trigger-type');
  if (inputType) inputType.value = type;

  const prefix = document.getElementById('rule-value-prefix');
  const label = document.getElementById('rule-value-label');
  const input = document.getElementById('rule-value-input');

  if (type === 'drop') {
    if (prefix) prefix.textContent = '%';
    if (label) label.textContent = 'Queda percentual';
    if (input) {
      input.placeholder = '5';
      input.value = '5';
    }
  } else {
    if (prefix) prefix.textContent = 'R$';
    if (label) label.textContent = 'Valor';
    updateRuleProductPreview();
  }
}

function selectEditRuleTrigger(type, el) {
  const group = document.getElementById('edit-rule-trigger-group');
  if (group) {
    group.querySelectorAll('.trigger-card').forEach(c => c.classList.remove('active'));
  }
  if (el) el.classList.add('active');

  const inputType = document.getElementById('edit-rule-trigger-type');
  if (inputType) inputType.value = type;

  const prefix = document.getElementById('edit-rule-value-prefix');
  const label = document.getElementById('edit-rule-value-label');
  const input = document.getElementById('edit-rule-price-input');

  if (type === 'drop') {
    if (prefix) prefix.textContent = '%';
    if (label) label.textContent = 'Queda percentual';
    if (input) {
      input.placeholder = '5';
      input.value = '5';
    }
  } else {
    if (prefix) prefix.textContent = 'R$';
    if (label) label.textContent = 'Valor';
    if (input && editingProduct) {
      input.value = editingProduct.target_price || (editingProduct.best_price ? Math.round(editingProduct.best_price * 0.95) : '');
    }
  }
}

// MODAL NOVO ALERTA
function openNewRuleModal(defaultPid = null) {
  const modal = document.getElementById('modal-new-rule');
  const sel = document.getElementById('rule-product-select');
  if (!modal || !sel) return;

  if (!productsData.length) {
    alert('Nenhum produto cadastrado. Cadastre um produto primeiro.');
    router.navigate('cadastro');
    return;
  }

  sel.innerHTML = productsData.map(p =>
    `<option value="${escapeHtml(p.id)}" ${defaultPid && p.id === defaultPid ? 'selected' : ''}>[${escapeHtml(p.category || 'Notebook')}] ${escapeHtml(p.name)}</option>`
  ).join('');

  if (defaultPid) sel.value = defaultPid;

  // Reseta trigger para 'below'
  const firstTrigger = document.querySelector('#new-rule-trigger-group .trigger-card');
  if (firstTrigger) selectNewRuleTrigger('below', firstTrigger);

  updateRuleProductPreview();
  modal.classList.remove('hidden');
}

function closeRuleModal() {
  const modal = document.getElementById('modal-new-rule');
  if (modal) modal.classList.add('hidden');
}

function updateRuleProductPreview() {
  const sel = document.getElementById('rule-product-select');
  const hint = document.getElementById('rule-current-price-hint');
  const input = document.getElementById('rule-value-input');
  if (!sel) return;

  const pid = +sel.value;
  const p = productsData.find(item => item.id === pid);
  if (p) {
    if (hint) hint.textContent = formatStorePricesHint(p);
    if (input) {
      input.value = p.target_price || (p.best_price ? Math.round(p.best_price * 0.95) : '');
    }
  }
}

async function handleSaveNewRule(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const pid = +f.get('product_id');
  const triggerType = f.get('trigger_type') || 'below';
  const rawVal = parseFloat(f.get('rule_value'));

  if (isNaN(rawVal) || rawVal <= 0) {
    alert('Por favor informe um valor válido.');
    return;
  }

  let finalTarget = rawVal;
  if (triggerType === 'drop') {
    const p = productsData.find(item => item.id === pid);
    if (p && p.best_price) {
      finalTarget = Math.round(p.best_price * (1 - (rawVal / 100)));
    }
  }

  try {
    await fetch(`/api/products/${pid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_price: finalTarget }),
    });
    closeRuleModal();
    await load();
  } catch (err) {
    alert('Erro ao salvar alerta: ' + err.message);
  }
}

// MODAL EDITAR REGRA
function openEditRuleModal(pid) {
  const p = productsData.find(item => item.id === pid);
  if (!p) return;
  editingProduct = p;

  const modal = document.getElementById('modal-edit-rule');
  if (!modal) return;

  document.getElementById('edit-product-id').value = p.id;
  document.getElementById('edit-rule-prod-name').textContent = p.name;

  const iconEl = document.getElementById('edit-rule-icon');
  if (iconEl) {
    const iconCls = typeof getCategoryIcon === 'function' ? getCategoryIcon(p.category) : 'fa-solid fa-laptop';
    iconEl.innerHTML = `<i class="${iconCls}"></i>`;
  }

  const subEl = document.getElementById('edit-rule-prod-sub');
  const toggleEl = document.getElementById('edit-rule-active-toggle');
  const hasTarget = p.target_price != null;

  if (toggleEl) toggleEl.checked = hasTarget;
  if (subEl) {
    subEl.textContent = hasTarget ? `Alvo atual: ${brl(p.target_price)}` : 'Regra inativa (sem alvo definido)';
  }

  const priceInput = document.getElementById('edit-rule-price-input');
  if (priceInput) {
    priceInput.value = p.target_price || (p.best_price ? Math.round(p.best_price * 0.95) : '');
    priceInput.disabled = !hasTarget;
  }

  const hintEl = document.getElementById('edit-rule-current-hint');
  if (hintEl) {
    hintEl.textContent = formatStorePricesHint(p);
  }

  // Reseta trigger para 'below'
  const firstTrigger = document.querySelector('#edit-rule-trigger-group .trigger-card');
  if (firstTrigger) selectEditRuleTrigger('below', firstTrigger);

  modal.classList.remove('hidden');
}

function closeEditRuleModal() {
  const modal = document.getElementById('modal-edit-rule');
  if (modal) modal.classList.add('hidden');
}

function handleEditRuleToggle(isChecked) {
  const subEl = document.getElementById('edit-rule-prod-sub');
  const priceInput = document.getElementById('edit-rule-price-input');
  if (priceInput) priceInput.disabled = !isChecked;

  if (subEl) {
    if (!isChecked) {
      subEl.textContent = 'Regra desativada';
    } else {
      subEl.textContent = editingProduct && editingProduct.target_price
        ? `Alvo atual: ${brl(editingProduct.target_price)}`
        : 'Regra ativa';
    }
  }
}

async function handleUpdateRule(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const pid = +f.get('edit_product_id');
  const toggleEl = document.getElementById('edit-rule-active-toggle');
  const isActive = toggleEl ? toggleEl.checked : true;

  try {
    if (!isActive) {
      await fetch(`/api/products/${pid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_price: null }),
      });
    } else {
      const rawVal = parseFloat(f.get('edit_target_price'));
      if (isNaN(rawVal) || rawVal <= 0) {
        alert('Por favor informe um valor válido.');
        return;
      }
      const triggerType = f.get('edit_trigger_type') || 'below';
      let finalTarget = rawVal;
      if (triggerType === 'drop' && editingProduct && editingProduct.best_price) {
        finalTarget = Math.round(editingProduct.best_price * (1 - (rawVal / 100)));
      }

      await fetch(`/api/products/${pid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_price: finalTarget }),
      });
    }

    closeEditRuleModal();
    await load();
  } catch (err) {
    alert('Erro ao atualizar regra: ' + err.message);
  }
}

async function handleDeleteRule() {
  if (!editingProduct) return;
  if (!confirm(`Remover o alerta de ${editingProduct.name}?`)) return;

  try {
    await fetch(`/api/products/${editingProduct.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_price: null }),
    });
    closeEditRuleModal();
    await load();
  } catch (err) {
    alert('Erro ao excluir regra: ' + err.message);
  }
}

// FECHAR MODAIS AO PRESSIONAR ESC
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeRuleModal();
    closeEditRuleModal();
  }
});
