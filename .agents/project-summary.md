# Price Tracker — Resumo do projeto (para agentes)

Rastreador de preços de produtos e periféricos em e-commerces (Mercado Livre, Amazon, Kabum, Lenovo, etc.). Faz scraping HTTP e consultas via API oficial (Mercado Livre), armazena leituras em banco de dados híbrido (PostgreSQL Neon na nuvem / SQLite local) e exibe uma interface web responsiva em vanilla JS SPA com gráficos históricos e alertas.

```
cron / botão na UI → collector → scrapers / API ML → DB (Neon / SQLite) → FastAPI → SPA (static/index.html)
```

## Stack
- **Backend**: Python 3.12, FastAPI + Uvicorn, httpx + BeautifulSoup4
- **Banco de Dados Híbrido**: 
  - PostgreSQL (Neon) em produção via `psycopg` (v3) quando `DATABASE_URL` estiver definida.
  - SQLite (`data/tracker.db`) para desenvolvimento local/testes unitários.
  - Sem ORM pesado: camada de abstração em `app/db.py` com compatibilidade de cursores (`Row`/`dict`), transações e migrations automáticas.
- **Scraping & Coleta**:
  - 100% HTTP/JSON assíncrono e leve. **Playwright e navegadores headless foram totalmente removidos** para economizar memória RAM e rodar com folga no plano gratuito do Render (512MB RAM).
  - API oficial do Mercado Livre com suporte a OAuth2 e renovação automática de token.
- **Frontend**: Single-Page Application (SPA) em HTML5/Vanilla JS modular, CSS centralizado (paleta D·Rosa), Chart.js (CDN) e Font Awesome 6.5.2 (CDN).
- **Segurança**:
  - Sanitização rigorosa contra XSS com `static/js/utils.js` (`escapeHtml` e `safeUrl`).
  - Imagem Docker rodando sob usuário não-privilegiado `app (UID 10001)`.
  - Autenticação HTTP Basic opcional via `APP_USERNAME` e `APP_PASSWORD`.
- **CI/CD & Deploy**:
  - GitHub Actions (`.github/workflows/deploy.yml`): testes automatizados, build multi-stage Docker para GHCR, trigger de deploy no Render via webhook.
  - Dependabot (`.github/dependabot.yml`): atualizações automáticas semanais para pip, docker e actions.

---

## Estrutura do Projeto
| Caminho | Papel |
|---|---|
| `app/db.py` | Camada de conexão híbrida (Neon PostgreSQL ou SQLite local), schemas DDL, cursores, transações e tabela `app_settings` com `get_setting` / `set_setting`. |
| `app/migrate_to_neon.py` | Script utilitário para migrar dados históricos do SQLite local para o PostgreSQL na Neon. |
| `app/scrapers/base.py` | `PriceResult`, `parse_brl()`, `parse_html()`, `fetch_html()` (sem dependência de navegador). |
| `app/scrapers/__init__.py` | Roteador de scrapers: prioriza API oficial para Mercado Livre com `get_access_token()`, seguido de scrapers específicos (`CUSTOM_PARSERS`) e genérico com retry. |
| `app/scrapers/amazon.py` | Parser específico para páginas da Amazon (lida com blocos dinâmicos e preços ocultos). |
| `app/scrapers/aliexpress.py` | Parser específico para AliExpress: extração de preço e desconto via parâmetro `pdp_npi` da URL e título via `og:title`. |
| `app/scrapers/mercadolivre.py` | Integração híbrida ML: consulta endpoints `/products/{MLB_ID}` (catálogo com `buy_box_winner.price`) e `/items/{MLB_ID}`, gerenciamento de OAuth2 e renovação automática de tokens via `refresh_token`, e fallback para HTML. |
| `app/collector.py` | `collect_all()` — itera pelos links ativos, coleta preços, persiste na tabela `prices`, atualiza progresso SSE e despacha alertas com deduplicação. |
| `app/notifier.py` | Módulo de notificações: e-mail gratuito via SMTP (Gmail App Password) e Telegram Bot, com suporte a múltiplos destinatários, deduplicação e cooldown. |
| `app/main.py` | Rotas FastAPI: CRUD de produtos e listings (inclui exclusão `DELETE`), disparo de coleta, status e SSE em tempo real (`/api/collect/events`), e mount de arquivos estáticos. |
| `app/cli.py` | Interface de linha de comando para gerenciar produtos, listings e disparar coleta. |
| `static/` | Frontend modular: `index.html`, `js/router.js`, `js/utils.js` (XSS helpers), controladores (`produtos.js`, `painel.js`, `lojas.js`, `alertas.js`, `historico.js`, `cadastro.js`) e templates em `views/*.html`. |
| `Dockerfile` | Multi-stage build com base `python:3.12-slim-bookworm`, sem root, healthcheck nativo com `urllib.request`. |
| `.github/workflows/deploy.yml` | Pipeline de CI/CD para GitHub Packages (GHCR) e Render. |

---

## Modelo de Dados
- **products**: `id`, `name`, `category` (ex.: Notebook, Periférico), `target_price`, `last_alert_at`, `last_alert_price`.
- **listings**: `id`, `product_id`, `store`, `url`, `css_selector`, `use_browser` (descontinuado), `active`. `UNIQUE(product_id, url)`. Exclusão em cascata configurada.
- **prices**: histórico de leituras. `id`, `listing_id`, `price`, `available`, `coupon_code`, `final_price`, `checked_at` (UTC). Índice em `(listing_id, checked_at)`.
- **app_settings**: chave-valor persistente para parâmetros do sistema e tokens (`key`, `value`, `updated_at`). Usado para guardar `ml_access_token`, `ml_token_expires_at` e `ml_refresh_token`.

---

## Integração Oficial do Mercado Livre (OAuth2 & Auto-Refresh)
A API do Mercado Livre bloqueia IPs de datacenter em requisições de scraping convencionais e exige autenticação Bearer para endpoints de catálogo e itens. O token de acesso (`access_token`) expira a cada 6 horas.

### Fluxo de Renovação Automática
1. No Render ou `.env`, são configuradas as credenciais:
   - `MERCADOLIVRE_CLIENT_ID`
   - `MERCADOLIVRE_CLIENT_SECRET`
   - `MERCADOLIVRE_REFRESH_TOKEN` (refresh token inicial)
2. Durante a coleta, `mercadolivre.get_access_token()`:
   - Verifica se há token válido e não-expirado salvo no banco (`app_settings`).
   - Se estiver expirado ou a 5 minutos de expirar, chama `POST https://api.mercadolibre.com/oauth/token` com `grant_type=refresh_token`.
   - Salva o novo `access_token` e o novo `refresh_token` rotacionado no banco de dados Neon.
   - Caso receba `401 Unauthorized` durante uma requisição, invalida o cache do banco automaticamente.
3. Se for fornecido apenas `MERCADOLIVRE_TOKEN` manual, este é usado diretamente.
4. Suporta URLs tanto de catálogo (`/p/MLB...` via `/products/{id}`) quanto de anúncios individuais (`/items/{id}`).

---

## API REST (`app/main.py`)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/products` | Lista produtos com listings e melhor preço atual |
| POST | `/api/products` | Cria novo produto |
| DELETE | `/api/products/{id}` | Remove produto e todos os links/preços associados |
| POST | `/api/listings` | Adiciona link a um produto |
| DELETE | `/api/listings/{id}` | Remove um link específico |
| GET | `/api/products/{id}/history` | Histórico cronológico de leituras do produto |
| POST | `/api/collect` | Dispara coleta assíncrona de preços |
| GET | `/api/collect/status` | Retorna status da coleta em andamento |
| GET | `/api/collect/events` | Stream SSE com progresso da coleta para a UI |

---

## Execução e Testes
```bash
# Ambiente virtual
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Inicializar banco e rodar testes (25 testes)
pytest -v

# Rodar servidor de desenvolvimento
uvicorn app.main:app --reload --port 8000
```
