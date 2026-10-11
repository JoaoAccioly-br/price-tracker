# Price Tracker — Resumo do projeto (para agentes)

Rastreador de preços de produtos e periféricos em e-commerces (Mercado Livre, AliExpress, Amazon, Kabum, Pichau, etc.). Faz consultas via API oficial (Mercado Livre) e scraping HTTP otimizado, armazena leituras em banco de dados híbrido (PostgreSQL Neon na nuvem / SQLite local) e exibe uma interface web responsiva em vanilla JS SPA com gráficos históricos e alertas em tempo real via Telegram.

```
cron / botão na UI → collector → scrapers / API ML → DB (Neon / SQLite) → FastAPI → SPA (static/index.html)
                                     ↓
                          Notificador Telegram Bot
```

## Stack
- **Backend**: Python 3.12, FastAPI + Uvicorn, httpx + BeautifulSoup4
- **Banco de Dados Híbrido**: 
  - PostgreSQL (Neon) em produção via `psycopg` (v3) quando `DATABASE_URL` estiver definida.
  - SQLite (`data/tracker.db`) para desenvolvimento local/testes unitários.
  - Sem ORM pesado: camada de abstração em `app/db.py` com compatibilidade de cursores (`Row`/`dict`), transações, migrations automáticas e tabela `app_settings` com `get_setting` / `set_setting`.
- **Scraping & Coleta**:
  - 100% HTTP/JSON assíncrono e ultra-leve. **Playwright e navegadores headless foram totalmente removidos** para economizar memória RAM e rodar com folga no plano gratuito do Render (512MB RAM).
  - **Mercado Livre**: 100% via API Oficial (sem web scraping/HTML fallback para evitar WAF). Suporte a catálogo (`/products/{id}`), ofertas de catálogo (`/products/{id}/items`), anúncios diretos e multiget oficial (`/items?ids={id}`). OAuth2 com renovação automática de tokens via `refresh_token` de 6 meses persistido no banco.
  - **AliExpress**: Extração via parâmetro `pdp_npi` da URL copiada da busca/compartilhamento (ignora o shell CSR vazio).
  - **Amazon**: Parser especializado com seletores modernos (`.priceToPay`, `#price_inside_buybox`, `#newBuyBoxPrice`, `span.apexPriceToPay`), busca de frações em `.a-price-whole`/`.a-price-fraction`, e detecção de desafios de CAPTCHA (`amazon:captcha`).
  - **Fallback ScraperAPI opcional**: Se `SCRAPERAPI_KEY` for configurado, requisições diretas que falharem ou sofrerem 403/CAPTCHA tentam uma segunda leitura via ScraperAPI.
- **Alertas & Notificações**:
  - Módulo `app/notifier.py` com envio de notificações instantâneas exclusivamente via **Telegram Bot API** (HTTPS na porta 443, 100% gratuito e compatível com as restrições de porta de saída do Render). Lógica anti-spam e cooldown de 24h por produto implementada na tabela `products` (`last_alert_at`, `last_alert_price`).
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
| `app/db.py` | Camada de conexão híbrida (Neon PostgreSQL ou SQLite local), schemas DDL, cursores, transações, campos de alerta e tabela `app_settings`. |
| `app/migrate_to_neon.py` | Script utilitário para migrar dados históricos do SQLite local para o PostgreSQL na Neon. |
| `app/scrapers/base.py` | `PriceResult`, `parse_brl()`, `parse_html()`, `fetch_html()`, detecção de cupons e suporte a ScraperAPI. |
| `app/scrapers/__init__.py` | Roteador de scrapers: rota exclusiva para API do Mercado Livre (`scrape_ml`), headers dedicados para Amazon/Kabum, e fallback inteligente. |
| `app/scrapers/amazon.py` | Parser específico para Amazon: seletores modernos, varredura de frações e detecção de CAPTCHA. |
| `app/scrapers/aliexpress.py` | Parser específico para AliExpress: extração de preço e promoção via parâmetro `pdp_npi` da URL e título via `og:title`. |
| `app/scrapers/mercadolivre.py` | Consulta exclusiva via API oficial: multiget `/items?ids=...`, `/products/...`, extração ampla de IDs (`extract_candidate_ids`), e OAuth com auto-refresh de 6 meses. |
| `app/collector.py` | `collect_all()` — itera pelos links ativos, coleta preços, persiste na tabela `prices`, atualiza progresso SSE e dispara alertas no Telegram. |
| `app/notifier.py` | Envio de mensagens de queda de preço para Telegram Bot (`notify_price_drop`, `send_telegram`) com cooldown anti-spam. |
| `app/main.py` | Rotas FastAPI: CRUD de produtos e listings, disparo de coleta, status e SSE em tempo real (`/api/collect/events`), e mount de estáticos. |
| `app/cli.py` | Interface de linha de comando para gerenciar produtos, listings e disparar coleta. |
| `static/` | Frontend modular: `index.html`, `js/router.js`, `js/utils.js`, controladores (`produtos.js`, `painel.js`, `lojas.js`, `alertas.js`, `historico.js`, `cadastro.js`) e templates em `views/*.html`. |
| `Dockerfile` | Multi-stage build com base `python:3.12-slim-bookworm`, sem root, healthcheck nativo com `urllib.request`. |
| `tests/` | Suite com 44 testes unitários cobrindo todos os scrapers, API, banco e notificações. |

---

## Modelo de Dados
- **products**: `id`, `name`, `category`, `target_price`, `last_alert_at`, `last_alert_price`.
- **listings**: `id`, `product_id`, `store`, `url`, `css_selector`, `active`. `UNIQUE(product_id, url)`. Exclusão em cascata configurada.
- **prices**: histórico de leituras. `id`, `listing_id`, `price`, `available`, `coupon_code`, `final_price`, `checked_at` (UTC). Índice em `(listing_id, checked_at)`.
- **app_settings**: chave-valor persistente para parâmetros do sistema e tokens (`key`, `value`, `updated_at`). Usado para guardar `ml_access_token`, `ml_token_expires_at`, `ml_refresh_token` e `scraperapi_key`.

---

## Integração Oficial do Mercado Livre (OAuth2 & Auto-Refresh)
A API do Mercado Livre exige autenticação Bearer para endpoints de catálogo e itens. O `access_token` expira a cada 6 horas, enquanto o `refresh_token` é válido por **6 meses**.

### Fluxo de Renovação Automática
1. No Render ou `.env`, são configuradas as credenciais:
   - `MERCADOLIVRE_CLIENT_ID`
   - `MERCADOLIVRE_CLIENT_SECRET`
   - `MERCADOLIVRE_REFRESH_TOKEN` (refresh token inicial)
2. Durante a coleta, `mercadolivre.get_access_token()`:
   - Verifica se há token válido e não-expirado salvo no banco (`app_settings`).
   - Se estiver expirado ou a 5 minutos de expirar, chama `POST https://api.mercadolibre.com/oauth/token` com `grant_type=refresh_token`.
   - Salva o novo `access_token` e o novo `refresh_token` (estendido por mais 6 meses) no banco de dados Neon.
   - Caso receba `401 Unauthorized` durante uma requisição, invalida o cache do banco automaticamente.
3. Se for fornecido apenas `MERCADOLIVRE_TOKEN` manual, este é usado diretamente.
4. Suporta URLs de catálogo (`/p/MLB...`, `/up/MLBU...`), anúncios individuais e URLs com parâmetros (`pdp_filters=item_id:MLB...`).

---

## Execução e Testes
```bash
# Ambiente virtual
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Rodar os 44 testes unitários
pytest -v

# Rodar servidor de desenvolvimento
uvicorn app.main:app --reload --port 8000
```
