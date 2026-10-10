// ==========================================
// ROTEADOR SPA (static/js/router.js)
// ==========================================

class ViewRouter {
  constructor(routes, defaultRoute = 'painel') {
    this.routes = routes;
    this.defaultRoute = defaultRoute;
    this.currentRoute = null;
    this.templateCache = {};
    this.container = document.getElementById('view-container');
    this.titleEl = document.getElementById('view-title');
    this.subtitleEl = document.getElementById('view-subtitle');

    window.addEventListener('hashchange', () => this.handleHashChange());
  }

  async init() {
    const initialRoute = window.location.hash.replace('#', '') || this.defaultRoute;
    await this.navigate(initialRoute, false);
  }

  handleHashChange() {
    const route = window.location.hash.replace('#', '') || this.defaultRoute;
    if (route !== this.currentRoute) {
      this.navigate(route, false);
    }
  }

  async navigate(routeName, updateHash = true) {
    const routeConfig = this.routes[routeName] || this.routes[this.defaultRoute];
    const actualRouteName = this.routes[routeName] ? routeName : this.defaultRoute;

    if (updateHash) {
      window.location.hash = actualRouteName;
    }

    this.currentRoute = actualRouteName;

    // Atualiza classes ativas na sidebar
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.route === actualRouteName);
    });

    // Atualiza cabeçalho
    if (this.titleEl) this.titleEl.textContent = routeConfig.title;
    if (this.subtitleEl) this.subtitleEl.textContent = routeConfig.subtitle;

    // Carrega o arquivo HTML da tela
    try {
      if (!this.templateCache[routeConfig.template]) {
        const resp = await fetch(routeConfig.template);
        if (!resp.ok) throw new Error(`Falha ao carregar tela ${routeConfig.template}`);
        this.templateCache[routeConfig.template] = await resp.text();
      }

      this.container.innerHTML = this.templateCache[routeConfig.template];

      // Executa o handler de renderização específico da tela
      if (typeof routeConfig.onRender === 'function') {
        routeConfig.onRender();
      }
    } catch (err) {
      console.error('Erro no roteador:', err);
      this.container.innerHTML = `
        <div style="padding: 30px; text-align: center; color: var(--accent-red)">
          <i class="fa-solid fa-triangle-exclamation" style="font-size: 28px; margin-bottom: 10px; display: block"></i>
          Erro ao carregar tela: ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  refreshCurrent() {
    if (this.currentRoute && this.routes[this.currentRoute]) {
      const config = this.routes[this.currentRoute];
      if (typeof config.onRender === 'function') {
        config.onRender();
      }
    }
  }
}

