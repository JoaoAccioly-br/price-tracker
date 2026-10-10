# Price Tracker — Resumo do projeto (para agentes)

Rastreador pessoal de preços de notebooks em lojas online. Faz scraping das páginas
de produto, guarda o histórico em SQLite e exibe tudo numa página web com gráfico.

```
cron / botão na UI → collector → scrapers → SQLite (data/tracker.db) → FastAPI → static/index.html
```

## Stack
- Python 3.10+ (usa `float | None`; o `.venv` local é 3.12)
- FastAPI + Uvicorn (API e servidor do frontend estático)
- httpx + BeautifulSoup4 (download e parsing de HTML)
- Playwright (opcional, para sites que renderizam preço via JS)
- SQLite (stdlib `sqlite3`, sem ORM)
- Frontend: HTML/JS puro + Chart.js via CDN
- Testes: pytest + `fastapi.testclient`

## Estrutura
| Caminho | Papel |
|---|---|
| `app/db.py` | `DB_PATH` (`data/tracker.db`), `SCHEMA`, `get_conn()` (row_factory=Row, FKs on), `init_db()` |
| `app/scrapers/base.py` | `PriceResult`, `parse_brl()`, `parse_html()`, `fetch_html()`, `_fetch_with_browser()` |
| `app/scrapers/__init__.py` | `scrape()` público (com retry: até 3 tentativas se o preço vier `None`) + registro `CUSTOM_PARSERS` (domínio → parser) |
| `app/scrapers/amazon.py` | Parser da Amazon (sem JSON-LD): preço "a pagar" em `#corePrice_feature_div .apex-pricetopay-value .a-offscreen`, fallbacks whole+fraction e inputs escondidos; detecta a variante sem preço ("adicione este item ao seu carrinho") → `source="amazon:price-hidden"` |
| `app/collector.py` | `collect_all()` — percorre listings ativos, raspa, grava preço, loga alerta |
| `app/cli.py` | CLI: `add-product`, `add-listing`, `list`, `collect` |
| `app/main.py` | API FastAPI + mount do `static/` em `/` |
| `static/index.html` | UI single-page (lista produtos, formulários, gráfico de histórico). O gráfico fica num wrapper `.chart` que é escondido (Chart.js força `display:block` no canvas, anulando `hidden`) |
| `tests/test_parser.py` | Testes de `parse_brl` e das 3 estratégias de parsing |
| `tests/test_amazon.py` | Testes do parser da Amazon |
| `tests/test_api.py` | Fluxo da API com banco temporário (monkeypatch em `db.DB_PATH`) |

## Modelo de dados (SQLite)
- **products**: `id`, `name`, `target_price` (preço alvo p/ alerta, opcional)
- **listings**: um produto numa loja (uma URL). `product_id`, `store`, `url`,
  `css_selector` (fallback), `use_browser` (0/1), `active` (0/1). `UNIQUE(product_id, url)`
- **prices**: histórico, uma linha por leitura. `listing_id`, `price`, `available`,
  `coupon_code` (código do cupom ativo detectado, ex: 'NOTE10'), `final_price` (preço com cupom aplicado),
  `checked_at` (default `datetime('now')` → **UTC**). Índice em `(listing_id, checked_at)`

## Fluxo de coleta (`collect_all`)
1. `init_db()` e busca todos os listings `active = 1` com nome/alvo do produto.
2. Para cada um (com `sleep` aleatório de 3–8 s entre requisições):
   - `scrapers.scrape(url, css_selector, use_browser)`:
     - `fetch_html` → httpx (UA de Chrome, `Accept-Language: pt-BR`) ou Playwright.
     - Escolhe parser por `urlparse(url).netloc` em `CUSTOM_PARSERS`, senão `parse_html`.
   - Exceção ou preço `None` → `failed += 1`, loga warning e segue.
   - Sucesso → `INSERT INTO prices`, `ok += 1`.
   - Se `target_price` e disponível e `price <= target` → `log.warning("ALERTA ...")`
     (**ponto de extensão** para Telegram/e-mail).
3. Retorna `{"ok": n, "failed": m}`.

## Estratégia de parsing (`parse_html`)
1. **JSON-LD** (`<script type="application/ld+json">`): percorre recursivamente
   (suporta `@graph`, listas, `@type` como lista), acha `Product` com `offers`;
   pega o **menor** `price`/`lowPrice`/`priceSpecification.price`;
   disponível = alguma oferta sem `OutOfStock`. `source="json-ld"`.
2. **Meta/itemprop**: `product:price:amount`, `og:price:amount`, `itemprop="price"`
   (em `<meta>` ou `<span>`). `source="meta"`, sempre `available=True`.
3. **Seletor CSS** do listing (se cadastrado). `source="css:<sel>"`.
4. Nada encontrado → `PriceResult(None, False, None, "not-found")`.

`parse_brl` normaliza `"R$ 3.499,90"`, `"3499.90"`, `"3.499"` (ponto + 3 dígitos = milhar),
`"1.299,00"`, números puros; retorna `None` se não houver dígitos.

## API (`app/main.py`)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/products` | Produtos (ordem por nome) + listings ativos com último preço/disponibilidade/data + `best_price` (menor entre disponíveis) |
| GET | `/api/products/{id}/history` | Todas as leituras (`store, price, available, checked_at`) ordenadas por data |
| POST | `/api/products` | `{name, target_price?}` → 201 `{id}` |
| POST | `/api/listings` | `{product_id, store, url, css_selector?, use_browser?}` → 201 `{id}`; 404 se produto não existe; 409 se duplicado |
| POST | `/api/collect` | Dispara `collect_all` em `BackgroundTasks` → 202 |
| GET | `/api/collect/status` | Retorna estado da coleta (`running`, `current`, `total`, `stats`, `last_run`) |
| GET | `/api/collect/events` | Stream de Server-Sent Events (SSE) para notificações em tempo real (`start`, `progress`, `done`) |
| GET | `/` | `static/index.html` (mount `StaticFiles` — **deve ficar por último**) |

## Frontend (`static/`) - Arquitetura Modular
- **Roteamento SPA limpo**: [`static/js/router.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/router.js) gerencia navegação e hash (`#painel`, `#produtos`, `#cadastro`) carregando assincronamente os templates sob demanda.
- **Telas separadas em HTML**:
  - [`static/views/painel.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/painel.html): Cards de estatísticas e tabela geral de melhores preços.
  - [`static/views/produtos.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/produtos.html): Layout em duas colunas (lista lateral e detalhes comparativos com gráfico).
  - [`static/views/cadastro.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/cadastro.html): Tela de Adicionar Produto / Link.
  - [`static/views/alertas.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/alertas.html): Monitoramento de metas e alertas de preço atingidos.
  - [`static/views/historico.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/historico.html): Linha do tempo completa das leituras de preços.
  - [`static/views/lojas.html`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/views/lojas.html): Resumo e listagem dos e-commerces e links cadastrados.
- **Módulos JS separados**:
  - [`static/js/painel.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/painel.js), [`static/js/produtos.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/produtos.js), [`static/js/cadastro.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/cadastro.js), [`static/js/alertas.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/alertas.js), [`static/js/historico.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/historico.js), [`static/js/lojas.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/lojas.js) e controlador [`static/js/app.js`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/js/app.js).
- **CSS centralizado**: [`static/css/style.css`](file:///home/joao-accioly/Documents/GitHub/price-tracker/static/css/style.css) com variáveis, paleta D·Rosa e responsividade.
- **Ícones**: 100% integrados via **Font Awesome 6.5.2** (CDN oficial).
- **Atualização em tempo real**: SSE (`/api/collect/events`) com status no botão e recarregamento automático da UI.

## Como rodar
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # só se for usar --browser

python -m app.cli add-product "Acer Nitro V15" --target 4500
python -m app.cli add-listing 1 kabum "https://www.kabum.com.br/produto/..."
python -m app.cli add-listing 1 amazon "https://..." --selector "span.preco"   # fallback CSS
python -m app.cli add-listing 1 loja "https://..." --browser                    # Playwright
python -m app.cli list
python -m app.cli collect

uvicorn app.main:app --reload      # http://localhost:8000
pytest                             # 19 testes, todos passando
```
Agendamento: `0 */8 * * * cd /caminho/price-tracker && .venv/bin/python -m app.cli collect`

## Convenções / como estender
- Parser específico de loja: criar `app/scrapers/<loja>.py` com
  `parse(html, css_selector) -> PriceResult` e registrar em `CUSTOM_PARSERS["www.loja.com.br"]`
  (chave = netloc exato, com `www.`).
- Toda função abre/fecha sua própria conexão via `get_conn()`; escrita dentro de `with conn:`.
- Testes de API trocam o banco com `monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")`.
- Código, comentários e mensagens em português.

## Observações / pontos de atenção conhecidos
- `.gitignore` só ignora `.venv` — `data/` (banco) e `__pycache__/` podem acabar commitados.
- `app/scrapers/base.py` tem um `scrape()` próprio que **não** consulta `CUSTOM_PARSERS`;
  o correto é usar `app.scrapers.scrape`.
- `create_listing` converte qualquer exceção em 409 (não só `IntegrityError`).
- Frontend usa `innerHTML` com dados do usuário (nome, loja, URL) sem escapar → risco de XSS.
- Não há endpoints/CLI para editar, desativar (`active`) ou apagar produtos/listings.
- Alertas apenas no log; `checked_at` é UTC (a UI indica isso).
- `playwright` está no `requirements.txt` mas os binários do navegador exigem `playwright install`.

