// ==========================================
// MÓDULO CADASTRO (static/js/cadastro.js)
// ==========================================

function renderCadastroView() {
  populateCadastroSelect();
}

function populateCadastroSelect() {
  const sel = document.getElementById('select-cadastro-product');
  if (!sel) return;

  if (!productsData.length) {
    sel.innerHTML = '<option value="">Cadastre um produto primeiro</option>';
    return;
  }

  sel.innerHTML = productsData.map(p =>
    `<option value="${escapeHtml(p.id)}" ${p.id === selectedProductId ? 'selected' : ''}>[${escapeHtml(p.category || 'Notebook')}] #${escapeHtml(p.id)} ${escapeHtml(p.name)}</option>`
  ).join('');
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
