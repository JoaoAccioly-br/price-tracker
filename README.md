# Price Tracker

Rastreador de preços de produtos e periféricos em e-commerces, com histórico em banco de dados, alertas instantâneos no Telegram e interface web moderna com gráficos interativos.

```
Cron / Agendador / Botão Web → Coletor → Scrapers & APIs → DB (Neon / SQLite) → FastAPI → Dashboard SPA
                                            ↓
                                 Alertas Telegram Bot
```

---

## Recursos Principais

* **Lojas com Tratamento Especial:**
  * **Mercado Livre:** 100% via **API Oficial** com renovação automática de OAuth2 (suporta anúncios de catálogo `/p/`, `/up/` e ofertas individuais). Livre de bloqueios WAF.
  * **Shopee Brasil:** 100% via **API Oficial de Afiliados** (GraphQL `productOfferV2`) com autenticação criptográfica SHA256. Consulta direta sem bloqueios.
  * **AliExpress:** Extração do preço promocional em BRL diretamente do parâmetro `pdp_npi` da URL.
  * **Amazon Brasil:** Parser calibrado para blocos modernos de preço (`.priceToPay`, `#price_inside_buybox`), varredura de frações e detecção de CAPTCHA.
* **Termômetro de Promoção (30 & 60 dias):** Detecção estatística de **Promoções Falsas** ("metade do dobro" / inflação pré-desconto), identificando se a oferta atual é uma *Promoção Real*, *Menor Preço em 60 dias* ou se o preço foi inflado artificialmente nos últimos dias.
* **Alertas no Telegram com Análise:** Disparo instantâneo e gratuito via Telegram Bot API quando o preço atingir ou for menor que o valor-alvo definido, incluindo a chancela estatística de 30/60 dias e proteção contra spam (cooldown de 24h).
* **Banco de Dados Híbrido:** Suporte automático a **PostgreSQL (Neon)** na nuvem e **SQLite** local.
* **Leve e Otimizado:** 100% HTTP/JSON sem navegadores pesados, rodando perfeitamente dentro dos 512 MB de RAM do plano gratuito do Render.
* **Interface Responsiva:** Dashboard SPA em Vanilla JS com acompanhamento de progresso em tempo real via Server-Sent Events (SSE) e gráficos históricos via Chart.js.

---

## Configuração do Ambiente (`.env`)

Copie o arquivo de exemplo e preencha as variáveis necessárias:

```bash
cp .env.example .env
```

| Variável | Descrição |
| :--- | :--- |
| `DATABASE_URL` | String de conexão do PostgreSQL (Neon). Se omitida, usa SQLite local em `data/tracker.db`. |
| `TELEGRAM_BOT_TOKEN` | Token do Bot criado com o [@BotFather](https://t.me/BotFather) no Telegram. |
| `TELEGRAM_CHAT_ID` | Seu ID de usuário ou grupo (obtido via [@userinfobot](https://t.me/userinfobot)). |
| `MERCADOLIVRE_CLIENT_ID` | App ID da sua aplicação no [Mercado Livre Developers](https://developers.mercadolivre.com.br/). |
| `MERCADOLIVRE_CLIENT_SECRET` | Secret Key da sua aplicação no Mercado Livre. |
| `MERCADOLIVRE_REFRESH_TOKEN` | Refresh Token inicial do Mercado Livre (o robô renova automaticamente a cada 6 meses). |
| `SHOPEE_APP_ID` | App ID da sua aplicação no [Shopee Affiliate Open API](https://affiliate.shopee.com.br/open_api). |
| `SHOPEE_APP_SECRET` | Secret Key da sua aplicação de Afiliados na Shopee. |
| `APP_USERNAME` / `APP_PASSWORD` | Autenticação HTTP Basic opcional para proteger o painel web. |

---

## Executando Localmente

```bash
# 1. Crie e ative o ambiente virtual
python -m venv .venv && source .venv/bin/activate

# 2. Instale as dependências
pip install -r requirements.txt -r requirements-dev.txt

# 3. Rode os testes unitários (54 testes)
pytest -v

# 4. Inicie o servidor web
uvicorn app.main:app --reload --port 8000
```

Acesse o painel em **`http://localhost:8000`**.

---

## Comandos via Terminal (CLI)

Você pode gerenciar tudo pela interface web ou pelo terminal:

```bash
# Cadastrar um produto e links
python -m app.cli add-product "Mouse Attack Shark X11" --target 120.00
python -m app.cli add-listing 1 aliexpress "https://pt.aliexpress.com/item/..."
python -m app.cli add-listing 1 mercadolivre "https://www.mercadolivre.com.br/..."
python -m app.cli add-listing 1 shopee "https://shopee.com.br/product/..."

# Disparar coleta manual
python -m app.cli collect
```

---

## Como o Scraper Acha o Preço

1. **Mercado Livre:** Consulta direta aos endpoints `/products/{id}` e multiget `/items?ids={id}` da API Oficial com token Bearer.
2. **Shopee Brasil:** Consulta direta à **Shopee Affiliate Open API** (GraphQL `productOfferV2`) com assinatura criptográfica SHA256.
3. **AliExpress:** Parse de parâmetros de precificação do produto (`pdp_npi`).
4. **Amazon Brasil:** Seletores hierárquicos de Buybox (`.priceToPay`, `#corePriceDisplay`, inputs de precificação).
5. **Lojas Gerais:**
   * **JSON-LD** (`<script type="application/ld+json">`, `schema.org/Offer`) — método mais estável.
   * **Meta tags** (`og:price:amount`, `product:price:amount`, `itemprop="price"`).
   * **Seletor CSS customizado** configurado na listagem.

---

## Boas Práticas de Coleta

* **Intervalos saudáveis:** A coleta aguarda um atraso aleatório de 3 a 8 segundos entre cada link para não sobrecarregar os servidores.
* **Agendamento recomendado:** Configure a rotina de coleta automática a cada 6 ou 8 horas (evita acionar defesas de rate limiting como o CAPTCHA da Amazon).
