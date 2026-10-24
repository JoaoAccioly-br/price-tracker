# Price Tracker — Resumo do Projeto (para Agentes)

Rastreador inteligente de preços de produtos e periféricos em e-commerces (Mercado Livre, Shopee, AliExpress, Amazon, KaBuM!, Pichau, etc.). Opera com consultas a APIs oficiais (Mercado Livre BuyBox e Shopee Affiliate GraphQL), scraping HTTP calibrado e pesquisa semântica com Google AI Studio (Gemini). Armazena dados em banco híbrido (PostgreSQL Neon na nuvem ou SQLite local), oferecendo interface SPA moderna em Vanilla JS com Server-Sent Events (SSE), gráficos históricos interativos (Chart.js), alertas individuais no Telegram e extensão de navegador Manifest V3.

```
Extensão Chrome / Botão UI / Cron → Collector / IA Gemini → Scrapers & APIs → DB (Neon / SQLite) → FastAPI → SPA (static/)
                                                                     ↓
                                                       Alertas Individuais Telegram
```

---

## 1. Stack Tecnológica
- **Backend**: Python 3.12, FastAPI + Uvicorn, httpx + BeautifulSoup4.
- **Banco de Dados Híbrido**:
  - PostgreSQL (Neon) em produção via `psycopg` (v3) quando `DATABASE_URL` estiver definida.
  - SQLite local (`data/tracker.db`) para desenvolvimento e suíte de testes.
  - Abstração em `app/db.py` com suporte transparente a cursores (`Row`/`dict`), transações, `RETURNING id`, pool de conexões com ping anti-idle e tabela chave-valor `app_settings`.
- **Autenticação & Segurança**:
  - `app/auth.py`: hash de senha com `scrypt`, sessão via cookie `HttpOnly` com assinatura `HMAC-SHA256` (`pt_session`).
  - Google Identity Services (OAuth2 GIS): autenticação via `POST /api/auth/google` validada diretamente contra `https://oauth2.googleapis.com/tokeninfo`.
  - Extensão Web: autenticação via Bearer token de 90 dias gerado por usuário.
  - Sanitização de saída XSS via `static/js/utils.js` (`escapeHtml`, `safeUrl`) e cabeçalhos de segurança (CSP, CORS, frame-ancestors).
- **Inteligência Artificial (Google AI Studio / Gemini)**:
  - `app/ai.py`: integração REST com Gemini (`gemini-2.5-flash`), utilizando **Google Search Grounding** para descoberta de links na web e curadoria semântica anti-acessórios com fallback heurístico.
- **Frontend**: SPA modular em HTML5 e Vanilla JS (sem frameworks pesados), CSS centralizado (paleta D·Rosa), Chart.js (CDN) e Font Awesome 6.5.2 (CDN).
- **CI/CD & Deploy**:
  - GitHub Actions (`.github/workflows/deploy.yml`): matriz com job `test` executando Pytest contra SQLite e contra PostgreSQL em container de serviço (`postgres:16-alpine`), build multi-stage Docker para GHCR e deploy automático na branch `main`.

---

## 2. Orquestração de Agentes e Modelos
Em conformidade com `AGENTS.md` e `.agents/rules/orchestration.md`:
- **Agente Principal (Tech Lead / Orquestrador)**:
  - Analisa requisitos, projeta a arquitetura, interage com o usuário, define planos e valida suítes de testes e segurança.
- **Subagentes de Implementação (`Model: "flash"`)**:
  - Executados via `invoke_subagent` com Gemini Flash para implementação rápida e eficiente de código.
  - Divisão estrita de escopos para execução paralela sem conflitos (ex.: Backend e Testes sob um subagente; Frontend sob outro subagente responsável por `static/`).

---

## 3. Módulos e Funcionalidades Principais

### A. Contas de Usuário & Autenticação (`app/auth.py`)
- Cadastro (`POST /api/auth/register`), Login (`POST /api/auth/login`), Logout (`POST /api/auth/logout`), Identificação (`GET /api/auth/me`) e Atualização de Perfil (`PATCH /api/auth/profile`).
- Isolamento por usuário em `products.user_id`. Usuários só visualizam e gerenciam seus próprios produtos e listagens.
- Ao registrar o primeiro usuário em uma base legada com produtos sem dono, a aplicação associa os produtos órfãos ao novo usuário automaticamente.

### B. Notificações Individuais no Telegram (`app/notifier.py`)
- Alertas direcionados: o coletor consulta o `telegram_chat_id` do dono do produto e envia o alerta individualmente.
- Descoberta automática de bot: consulta a API `/getMe` do Telegram para obter o `@username` público do bot, montando links diretos `https://t.me/<bot_username>?start=...` para facilitar a ativação.
- Rota de teste (`POST /api/auth/telegram/test`) permitindo validar a configuração do Telegram com um clique.

### C. Inteligência Artificial e Self-Healing Links (`app/ai.py`)
- **Descoberta Inteligente (`POST /api/ai/discover-links`)**:
  1. Busca oficial de ofertas na Shopee (GraphQL) e Mercado Livre (`/products/search`).
  2. Pesquisa ampla na web com Google Search Grounding do Gemini para lojas como Amazon, KaBuM!, Magalu, etc.
  3. Deduplicação canônica e filtragem de URLs já monitoradas pelo produto.
  4. Curadoria semântica via prompt estruturado do Gemini descartando capas, películas, cabos, carregadores e modelos divergentes.
- **Auto-Recuperação de Links Expirados (*Self-Healing*)**:
  - Se um anúncio retornar indisponível ou esgotado durante a rotina do coletor (`app/collector.py`) ou sob chamada manual (`POST /api/listings/{id}/heal`), o sistema descobre um anúncio substituto ativo na mesma loja e atualiza a listagem de forma transparente.

### D. Histórico Compartilhado de Links Canônicos & Extensão
- **Canonicidade (`canonicalize_url`)**: URLs são padronizadas em chaves únicas (`shopee:{shop}:{item}`, `mercadolivre:{MLB}`, `amazon:{ASIN}`, `aliexpress:{item_id}`).
- **Deduplicação de Coleta**: Se vários usuários acompanham a mesma oferta, o coletor faz apenas uma requisição HTTP e replica a inserção de preços.
- **Aproveitamento Imediato de Histórico**: Um novo usuário que cadastra uma URL já monitorada no sistema herda imediatamente todo o histórico de preços existente.
- **Preservação de Dados**: Excluir uma listagem apenas desvincula o produto (`product_id = NULL`), mantendo a chave canônica e o histórico de preços intactos para análises e outros usuários.
- **Extensão de Navegador (`extension/`)**: Extensão Manifest V3 com popup e captura rápida de URL para envio com 1 clique ao backend via `/api/extension/quick-add`.

---

## 4. Estrutura de Arquivos
| Caminho | Responsabilidade |
| :--- | :--- |
| `app/db.py` | Conexões híbridas (Postgres/SQLite), schemas DDL idempotentes, migrações automáticas e chave-valor `app_settings`. |
| `app/auth.py` | Criptografia scrypt, HMAC-SHA256, cookies de sessão, Google GIS e injeção de dependência de usuário. |
| `app/ai.py` | Google AI Studio (Gemini 2.5 Flash), Google Search Grounding, curadoria semântica e auto-recuperação de links. |
| `app/main.py` | Endpoints FastAPI REST, rotas de autenticação, AI, produtos, listagens, extensão, SSE e fallback SPA. |
| `app/collector.py` | Coleta assíncrona, deduplicação por chave canônica, auto-healing de anúncios e despacho de alertas individuais. |
| `app/notifier.py` | Envio de mensagens ao Telegram com formatação MarkdownV2, descoberta de bot e proteção anti-spam. |
| `app/analyzer.py` | Termômetro de promoção (30/60 dias): cálculo estatístico de promoções reais vs metade do dobro. |
| `app/scrapers/` | Scrapers dedicados para Mercado Livre (API oficial), Shopee (GraphQL), AliExpress, Amazon e lojas gerais. |
| `static/` | Frontend Vanilla JS SPA (`app.js`, `router.js`, controladores `produtos.js`, `conta.js`, `alertas.js`, etc.). |
| `extension/` | Extensão Web Manifest V3 para navegadores Chrome/Edge. |
| `tests/` | 99 testes unitários e de integração cobrindo auth, AI, scrapers, banco, links canônicos e notificações. |
| `.github/workflows/deploy.yml` | Pipeline CI/CD: testes em SQLite e Postgres em serviço, build Docker e deploy no Render. |

---

## 5. Modelo de Dados
- **users**: `id`, `email` (UNIQUE), `name`, `password_hash`, `telegram_chat_id`, `created_at`, `updated_at`.
- **products**: `id`, `user_id` (FK users), `name`, `category`, `target_price`, `last_alert_at`, `last_alert_price`, `created_at`.
- **listings**: `id`, `product_id` (FK products, NULLABLE), `store`, `url`, `canonical_key`, `css_selector`, `active`, `created_at`. Índice em `canonical_key`.
- **prices**: `id`, `listing_id` (FK listings), `price`, `available`, `coupon_code`, `final_price`, `checked_at`. Índice em `(listing_id, checked_at)`.
- **app_settings**: `key` (PK), `value`, `updated_at`.

---

## 6. Executando os Testes
```bash
# Executa todos os 99 testes com cobertura completa
.venv/bin/pytest -v
```
