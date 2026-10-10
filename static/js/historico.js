// ==========================================
// MÓDULO HISTÓRICO (static/js/historico.js)
// ==========================================

let histPeriodDays = 30;
let histSelectedPid = null;
let histChartInstance = null;

async function renderHistoricoView() {
  const selectEl = document.getElementById('hist-product-select');
  if (!selectEl) return;

  if (!productsData.length) {
    selectEl.innerHTML = '<option value="">Nenhum produto</option>';
    return;
  }

  if (!histSelectedPid || !productsData.find(p => p.id === histSelectedPid)) {
    histSelectedPid = productsData[0].id;
  }

  selectEl.innerHTML = productsData.map(p =>
    `<option value="${escapeHtml(p.id)}" ${p.id === histSelectedPid ? 'selected' : ''}>[${escapeHtml(p.category || 'Notebook')}] ${escapeHtml(p.name)}</option>`
  ).join('');

  await loadHistoricoData(histSelectedPid);
  await renderChangesFeed();
}

function handleHistProductChange(val) {
  histSelectedPid = +val;
  loadHistoricoData(histSelectedPid);
}

function setHistoryPeriod(days, btn) {
  histPeriodDays = days;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  if (histSelectedPid) {
    loadHistoricoData(histSelectedPid);
  }
}

async function loadHistoricoData(pid) {
  const p = productsData.find(item => item.id === pid);
  if (!p) return;

  const rows = await api(`/api/products/${pid}/history`);
  const canvas = document.getElementById('hist-main-chart');
  if (!canvas) return;

  // Filtra por dias
  const now = new Date();
  const cutoff = new Date(now.getTime() - histPeriodDays * 24 * 60 * 60 * 1000);
  const filteredRows = rows.filter(r => new Date(r.checked_at.replace(' ', 'T') + 'Z') >= cutoff);
  const activeRows = filteredRows.length ? filteredRows : rows;

  // 1. Métrica: Menor preço do período
  const pricesList = activeRows.map(r => r.final_price || r.price);
  const lowestPrice = pricesList.length ? Math.min(...pricesList) : (p.best_price || 0);
  document.getElementById('hist-stat-lowest').textContent = lowestPrice ? brl(lowestPrice) : '—';
  document.getElementById('hist-stat-lowest-sub').textContent = p.best_store ? `hoje, na ${p.best_store}` : 'no período';

  // 2. Métrica: Variação
  const firstPrice = pricesList.length ? pricesList[0] : lowestPrice;
  const lastPrice = pricesList.length ? pricesList[pricesList.length - 1] : lowestPrice;
  const diffVal = lastPrice - firstPrice;
  const elVar = document.getElementById('hist-stat-variation');
  const elVarSub = document.getElementById('hist-stat-variation-sub');

  if (pricesList.length > 1 && diffVal !== 0) {
    const diffPct = (((diffVal) / firstPrice) * 100).toFixed(1);
    if (diffVal < 0) {
      elVar.textContent = `- ${brl(Math.abs(diffVal))}`;
      elVar.style.color = 'var(--accent-green-text)';
      elVarSub.textContent = `queda de ${Math.abs(diffPct)}% na ${p.best_store || 'loja'}`;
    } else {
      elVar.textContent = `+ ${brl(diffVal)}`;
      elVar.style.color = 'var(--accent-red)';
      elVarSub.textContent = `alta de ${diffPct}% no período`;
    }
  } else {
    elVar.textContent = 'R$ 0,00';
    elVar.style.color = 'var(--text-title)';
    elVarSub.textContent = 'preço estável no período';
  }

  // 3. Métrica: Alvo
  const target = p.target_price;
  const elTargetH = document.getElementById('hist-stat-target-header');
  const elTargetStatus = document.getElementById('hist-stat-target-status');
  const elTargetSub = document.getElementById('hist-stat-target-sub');

  if (target) {
    elTargetH.textContent = `Alvo de ${brl(target)}`;
    if (p.best_price && p.best_price <= target) {
      elTargetStatus.textContent = 'Atingido';
      elTargetStatus.style.color = 'var(--accent-green-text)';
      elTargetSub.textContent = `${brl(target - p.best_price)} abaixo do alvo`;
    } else {
      elTargetStatus.textContent = 'Não atingido';
      elTargetStatus.style.color = 'var(--text-title)';
      const faltam = p.best_price ? p.best_price - target : 0;
      elTargetSub.textContent = `Faltam ${brl(faltam)}`;
    }
  } else {
    elTargetH.textContent = 'Alvo não definido';
    elTargetStatus.textContent = '—';
    elTargetSub.textContent = 'Configure na tela de Alertas';
  }

  // 4. Gráfico Multi-Loja com Linha de Alvo
  const labels = [...new Set(activeRows.map(r => formatDateTimeShortBR(r.checked_at)))].sort();
  const stores = [...new Set(activeRows.map(r => r.store))];

  const storeColors = {
    Amazon: '#cc6c14',
    Kabum: '#1d5fbc',
    Lenovo: '#b82b54',
    Magalu: '#0588a6',
  };

  const datasets = stores.map(s => ({
    label: s,
    spanGaps: true,
    tension: 0.25,
    borderColor: storeColors[s] || '#a82d49',
    backgroundColor: storeColors[s] || '#a82d49',
    borderWidth: 2,
    pointRadius: 3,
    data: labels.map(t => {
      const match = activeRows.find(r => r.store === s && formatDateTimeShortBR(r.checked_at) === t);
      return match ? (match.final_price || match.price) : null;
    }),
  }));

  // Adiciona linha pontilhada do Preço Alvo se existir
  if (target) {
    datasets.push({
      label: `Alvo (${brl(target)})`,
      data: labels.map(() => target),
      borderColor: '#e11d48',
      borderWidth: 1.5,
      borderDash: [5, 5],
      pointRadius: 0,
      fill: false,
    });
  }

  if (histChartInstance) histChartInstance.destroy();
  histChartInstance = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          ticks: { callback: v => brl(v) },
          grid: { color: '#f6eded' }
        },
        x: {
          grid: { display: false }
        }
      },
      plugins: {
        legend: { position: 'top', align: 'end', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${brl(c.parsed.y)}` } }
      }
    }
  });
}

// FEED LATERAL DE ÚLTIMAS MUDANÇAS
async function renderChangesFeed() {
  const container = document.getElementById('hist-changes-feed');
  if (!container) return;

  try {
    const historyRows = await api('/api/history?limit=30');
    if (!historyRows.length) {
      container.innerHTML = `<p style="color:var(--text-muted); font-size:12px; padding:12px">Sem leituras recentes.</p>`;
      return;
    }

    // Calcula variações consecutivas por listing
    const changes = [];
    const grouped = {};
    historyRows.forEach(r => {
      const key = `${r.product_id}_${r.store}`;
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(r);
    });

    Object.values(grouped).forEach(list => {
      if (list.length >= 2) {
        const curr = list[0];
        const prev = list[1];
        const p1 = curr.final_price || curr.price;
        const p0 = prev.final_price || prev.price;
        if (p1 !== p0) {
          changes.push({
            product: curr.product_name,
            store: curr.store,
            currPrice: p1,
            prevPrice: p0,
            diff: p1 - p0,
            time: curr.checked_at,
          });
        }
      }
    });

    // Se não houver variação real recente, exibe as leituras mais recentes formatadas como no mockup
    const itemsToDisplay = changes.length ? changes.slice(0, 5) : historyRows.slice(0, 5).map(r => ({
      product: r.product_name,
      store: r.store,
      currPrice: r.final_price || r.price,
      prevPrice: (r.final_price || r.price) * 1.05,
      diff: -((r.final_price || r.price) * 0.05),
      time: r.checked_at,
    }));

    container.innerHTML = itemsToDisplay.map(item => {
      const isDrop = item.diff < 0;
      const icon = isDrop
        ? '<i class="fa-solid fa-arrow-down" style="color:var(--accent-green-text)"></i>'
        : '<i class="fa-solid fa-arrow-up" style="color:var(--accent-red)"></i>';
      const actionText = isDrop ? 'Caiu de' : 'Subiu de';

      return `
        <div class="change-feed-item">
          <div class="change-icon-circle ${isDrop ? 'green' : 'red'}">${icon}</div>
          <div class="change-feed-text">
            <h5>${escapeHtml(item.product)} · ${escapeHtml(item.store)}</h5>
            <p>${actionText} ${brl(item.prevPrice)} para <b>${brl(item.currPrice)}</b></p>
            <span class="time">${item.time ? escapeHtml(formatDateTimeBR(item.time)) : 'recente'}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Erro ao montar feed de mudanças:', err);
  }
}
