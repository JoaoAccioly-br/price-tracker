# Notebook Tracker

Acompanha o preço de notebooks em várias lojas, guarda o histórico e mostra tudo numa página com gráfico.

```
cron/GitHub Actions → collector → scrapers → SQLite → FastAPI → static/index.html
```

## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# cadastrar produto e links (ou use o formulário na página)
python -m app.cli add-product "Acer Nitro V15" --target 4500
python -m app.cli add-listing 1 kabum "https://www.kabum.com.br/produto/..."
python -m app.cli add-listing 1 amazon "https://www.amazon.com.br/dp/..."

python -m app.cli collect            # coleta uma vez
uvicorn app.main:app --reload        # abre http://localhost:8000
pytest                               # testes
```

Agendar a coleta (a cada 8h):

```
0 */8 * * *  cd /caminho/notebook-tracker && .venv/bin/python -m app.cli collect
```

## Como o scraper acha o preço

1. **JSON-LD** (`<script type="application/ld+json">`, schema.org) — mais estável.
2. **Meta tags** (`product:price:amount`, `og:price:amount`, `itemprop="price"`).
3. **Seletor CSS** cadastrado no link (`--selector "span.preco"`) — frágil, último recurso.

Site que só mostra o preço via JavaScript: instale Playwright e cadastre o link com `--browser`.
Loja que precisa de tratamento próprio: escreva um parser e registre em `app/scrapers/__init__.py`.

## Boas práticas

- Poucos acessos (a coleta já espera 3–8 s entre requisições).
- Confira `robots.txt` e os termos de cada loja.
- Se uma loja começar a falhar, veja o log: provavelmente mudou o layout ou bloqueou o acesso.

## Próximos passos

- Alerta no Telegram/e-mail em `collector.py` (marcado como ponto de extensão).
- Rodar a coleta no GitHub Actions e commitar o `.db`/JSON para hospedar de graça.
- Trocar SQLite por PostgreSQL se o projeto crescer.
