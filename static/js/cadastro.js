// ==========================================
// MÓDULO CADASTRO (static/js/cadastro.js)
// ==========================================

function detectStoreNameFromUrl(url) {
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

function renderCadastroView() {
  populateCadastroSelect();
  if (typeof populateAICadastroSelect === 'function') {
    populateAICadastroSelect();
  }

  const formListing = document.getElementById('f-listing');
  const formProduct = document.getElementById('f-product');

  if (formListing) {
    const urlInput = formListing.querySelector('input[name="url"]');
    const storeInput = formListing.querySelector('input[name="store"]');
    if (urlInput && storeInput) {
      urlInput.addEventListener('input', () => {
        const val = urlInput.value.trim();
        if (val && !storeInput.value.trim()) {
          const detected = detectStoreNameFromUrl(val);
          if (detected) storeInput.value = detected;
        }
      });
    }
  }

  // Preenchimento automático por query parameters da rota (#cadastro?url=...&title=...&store=...)
  if (window.router && router.queryParams) {
    const url = router.queryParams.get('url');
    const title = router.queryParams.get('title');
    const store = router.queryParams.get('store');

    if (url || title || store) {
      if (formListing) {
        const urlInput = formListing.querySelector('input[name="url"]');
        const storeInput = formListing.querySelector('input[name="store"]');
        if (url && urlInput) urlInput.value = url;
        if (storeInput) {
          storeInput.value = store || (url ? detectStoreNameFromUrl(url) : '');
        }
      }
      if (formProduct && title) {
        const nameInput = formProduct.querySelector('input[name="name"]');
        if (nameInput) nameInput.value = title;
      }
      // Limpa os parâmetros da hash após aplicar para não sobrescrever em navegações futuras
      router.queryParams = new URLSearchParams();
    }
  }
}

function populateCadastroSelect() {
  const sel = document.getElementById('select-cadastro-product');
  if (!sel) return;

  if (!productsData.length) {
    sel.innerHTML = '<option value="">Cadastre um produto primeiro</option>';
    return;
  }

  sel.innerHTML = productsData.map(p =>
    `<option value="${escapeHtml(p.id)}" ${p.id === selectedProductId ? 'selected' : ''}>[${escapeHtml(p.category || 'Notebook')}] ${escapeHtml(p.name)}</option>`
  ).join('');
}

function populateAICadastroSelect() {
  const sel = document.getElementById('ai-cadastro-product-select');
  if (!sel) return;

  const queryInput = document.getElementById('ai-cadastro-query-input');
  if (queryInput && (queryInput.value.toLowerCase() === 'undefined' || queryInput.value.toLowerCase() === 'null')) {
    queryInput.value = '';
  }

  if (!productsData.length) {
    sel.innerHTML = '<option value="">Nenhum produto cadastrado ainda</option>';
    return;
  }

  sel.innerHTML = '<option value="">-- Escolha um produto ou digite ao lado --</option>' +
    productsData.map(p =>
      `<option value="${escapeHtml(p.id)}" ${p.id === selectedProductId ? 'selected' : ''}>[${escapeHtml(p.category || 'Notebook')}] ${escapeHtml(p.name)}</option>`
    ).join('');

  if (selectedProductId) {
    const selProd = productsData.find(p => p.id === selectedProductId);
    if (selProd && queryInput && (!queryInput.value || queryInput.value.toLowerCase() === 'undefined')) {
      queryInput.value = selProd.name;
    }
  }
}

function handleAICadastroProductChange() {
  const sel = document.getElementById('ai-cadastro-product-select');
  const queryInput = document.getElementById('ai-cadastro-query-input');
  if (!sel || !queryInput) return;

  const pid = +sel.value;
  const p = productsData.find(item => item.id === pid);
  if (p) {
    queryInput.value = p.name;
  } else {
    queryInput.value = '';
  }
}

async function handleCreateProduct(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;

  try {
    const res = await post('/api/products', {
      name: f.get('name'),
      category: f.get('category') || 'Notebook',
      target_price: f.get('target_price') ? +f.get('target_price') : null,
    });
    alert('Produto cadastrado com sucesso!');
    e.target.reset();
    selectedProductId = res.id;
    await load();
    router.navigate('produtos');
  } catch (err) {
    alert('Erro ao cadastrar produto: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}

async function handleCreateProductWithAI(e) {
  e.preventDefault();
  const form = document.getElementById('f-product');
  if (!form) return;

  const nameInput = form.name;
  if (!nameInput || !nameInput.value.trim()) {
    alert('Por favor informe o nome do produto antes de buscar com IA.');
    if (nameInput) nameInput.focus();
    return;
  }

  const f = new FormData(form);
  const prodName = nameInput.value.trim();
  const prodCategory = (f.get('category') || 'Notebook').toString().trim() || 'Notebook';

  const aiBtn = e.currentTarget || e.target;
  const origHtml = aiBtn.innerHTML;
  aiBtn.disabled = true;
  aiBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Cadastrando…';

  try {
    const res = await post('/api/products', {
      name: f.get('name'),
      category: f.get('category') || 'Notebook',
      target_price: f.get('target_price') ? +f.get('target_price') : null,
    });

    selectedProductId = res.id;
    form.reset();
    await load();
    populateCadastroSelect();
    populateAICadastroSelect();

    const finalName = (res && res.name && res.name !== 'undefined') ? res.name : prodName;
    const finalCategory = (res && res.category) ? res.category : prodCategory;

    // Rola até o card da IA e executa a busca
    const cardAI = document.getElementById('card-ai-discover');
    if (cardAI) {
      cardAI.scrollIntoView({ behavior: 'smooth' });
    }

    const aiSel = document.getElementById('ai-cadastro-product-select');
    const aiQuery = document.getElementById('ai-cadastro-query-input');
    if (aiSel) aiSel.value = res.id;
    if (aiQuery) aiQuery.value = finalName;

    await performAIDiscover({
      product_id: res.id,
      product_name: finalName,
      category: finalCategory,
    }, '#ai-cadastro-results-container', '#btn-ai-discover-submit');
  } catch (err) {
    alert('Erro ao cadastrar produto: ' + err.message);
  } finally {
    aiBtn.disabled = false;
    aiBtn.innerHTML = origHtml;
  }
}

async function handleCreateListing(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;

  try {
    await post('/api/listings', {
      product_id: +f.get('product_id'),
      store: f.get('store'),
      url: f.get('url'),
      css_selector: f.get('css_selector') || null,
    });
    alert('Link adicionado com sucesso!');
    e.target.reset();
    await load();
    router.navigate('produtos');
  } catch (err) {
    alert('Erro ao adicionar link: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}

async function handleAIDiscoverSubmit(e) {
  e.preventDefault();
  const sel = document.getElementById('ai-cadastro-product-select');
  const queryInput = document.getElementById('ai-cadastro-query-input');
  const storeSel = document.getElementById('ai-cadastro-store-select');

  const productId = sel && sel.value ? +sel.value : null;
  let productName = queryInput ? queryInput.value.trim() : '';
  if (productName.toLowerCase() === 'undefined' || productName.toLowerCase() === 'null') {
    productName = '';
    if (queryInput) queryInput.value = '';
  }
  let category = undefined;
  if (productId) {
    const p = productsData.find(item => item.id === productId);
    if (p) {
      if (!productName && p.name) {
        productName = p.name;
        if (queryInput) queryInput.value = p.name;
      }
      category = p.category || undefined;
    }
  }
  const store = storeSel ? storeSel.value.trim() || null : null;

  if (!productId && !productName) {
    alert('Por favor escolha um produto cadastrado ou digite o termo a ser buscado.');
    if (queryInput) queryInput.focus();
    return;
  }

  await performAIDiscover({
    product_id: productId,
    product_name: productName || undefined,
    category: category,
    store: store,
  }, '#ai-cadastro-results-container', '#btn-ai-discover-submit');
}

async function performAIDiscover(payload, containerSelector, btnSelector) {
  const container = document.querySelector(containerSelector);
  const btn = btnSelector ? document.querySelector(btnSelector) : null;
  if (!container) return;

  const origBtnHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Buscando com IA…';
  }

  container.innerHTML = `
    <div style="padding: 32px; text-align: center; color: var(--text-muted); font-size: 13.5px;">
      <i class="fa-solid fa-wand-magic-sparkles fa-spin" style="font-size: 26px; margin-bottom: 12px; display: block; color: var(--accent-red);"></i>
      A IA (Gemini) está navegando e analisando as lojas para encontrar links compatíveis…
    </div>
  `;

  try {
    const data = await api('/api/ai/discover-links', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const results = data.results || [];
    if (!results.length) {
      container.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-muted); background: #fbf6f6; border-radius: 12px; border: 1px dashed var(--card-border);">
          <i class="fa-regular fa-face-frown" style="font-size: 24px; margin-bottom: 8px; display: block;"></i>
          Nenhum anúncio compatível foi retornado pela IA para <b>${escapeHtml(data.query || payload.product_name || 'este produto')}</b>. Tente refinar a busca.
        </div>
      `;
      return;
    }

    container.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
        <span style="font-size: 12.5px; font-weight: 700; color: var(--text-muted);">
          <i class="fa-solid fa-check" style="color: var(--accent-green-text); margin-right: 4px;"></i>
          ${results.length} links encontrados para monitoramento
        </span>
      </div>
      <div class="ai-results-grid">
        ${results.map(r => renderSingleAIResultCard(r, payload.product_id)).join('')}
      </div>
    `;
  } catch (err) {
    container.innerHTML = `
      <div class="tg-feedback-box error" style="margin-top: 10px;">
        <i class="fa-solid fa-triangle-exclamation"></i>
        <span>Erro na busca com IA: ${escapeHtml(err.message || 'Falha ao consultar API')}</span>
      </div>
    `;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origBtnHtml;
    }
  }
}

function renderSingleAIResultCard(r, productId) {
  const storeCls = typeof getStoreClass === 'function' ? getStoreClass(r.store) : 'store-kabum';
  const confCls = (r.confidence || 'alta').toLowerCase();
  const pid = productId || '';

  return `
    <div class="ai-result-card">
      <div>
        <div class="ai-result-header">
          <span class="store-pill ${storeCls}">
            <span class="store-avatar">${escapeHtml(r.store ? r.store[0].toUpperCase() : '?')}</span>
            <span style="margin-left: 5px; font-size: 11.5px; font-weight: 700;">${escapeHtml(r.store)}</span>
          </span>
          <span class="ai-confidence-badge ${confCls}">${escapeHtml(r.confidence || 'alta')}</span>
        </div>

        <h4 class="ai-result-title">
          <a href="${safeUrl(r.url)}" target="_blank" rel="noopener noreferrer">
            ${escapeHtml(r.title)}
            <i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 10px; margin-left: 3px;"></i>
          </a>
        </h4>

        <div class="ai-result-reason">
          <i class="fa-solid fa-wand-magic-sparkles" style="color: var(--accent-red); margin-right: 4px; font-size: 11px;"></i>
          ${escapeHtml(r.reason || 'Compatível com o modelo')}
        </div>
      </div>

      <div class="ai-result-footer">
        <div class="ai-result-price">
          ${r.price ? (typeof brl === 'function' ? brl(r.price) : 'R$ ' + r.price) : '<span style="font-size:12px; color:var(--text-muted); font-weight:600">Ver no site</span>'}
        </div>
        <button type="button" class="btn-primary btn-monitor-ai" onclick="handleMonitorAILink(${pid || 'null'}, '${escapeHtml(r.store)}', '${encodeURIComponent(r.url)}', this)" style="padding: 7px 12px; font-size: 12px;">
          <i class="fa-solid fa-plus"></i>
          <span>Monitorar link</span>
        </button>
      </div>
    </div>
  `;
}

async function handleMonitorAILink(productId, store, encodedUrl, btnEl) {
  let targetPid = productId;
  if (!targetPid) {
    const sel = document.getElementById('ai-cadastro-product-select');
    if (sel && sel.value) targetPid = +sel.value;
  }
  if (!targetPid) {
    alert('Por favor selecione um produto cadastrado acima para vincular este link.');
    const sel = document.getElementById('ai-cadastro-product-select');
    if (sel) sel.focus();
    return;
  }

  const origHtml = btnEl.innerHTML;
  btnEl.disabled = true;
  btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Vinculando…';

  try {
    const url = decodeURIComponent(encodedUrl);
    await post('/api/listings', {
      product_id: targetPid,
      store: store,
      url: url,
    });

    btnEl.className = 'btn-outline';
    btnEl.style.color = 'var(--accent-green-text)';
    btnEl.style.borderColor = 'var(--accent-green-border)';
    btnEl.style.background = 'var(--accent-green-bg)';
    btnEl.innerHTML = '<i class="fa-solid fa-check"></i> <span>✓ Adicionado</span>';
    btnEl.disabled = true;

    await load();
  } catch (err) {
    alert('Erro ao vincular link: ' + err.message);
    btnEl.disabled = false;
    btnEl.innerHTML = origHtml;
  }
}
