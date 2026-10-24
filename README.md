# Price Tracker

Rastreador inteligente de preços de produtos e periféricos em e-commerces, com histórico em banco de dados híbrido, descoberta automática de links e auto-recuperação com IA (Google Gemini), extensão para navegadores (Chrome/Edge), contas de usuário com isolamento, alertas individuais no Telegram e interface web moderna com gráficos interativos.

```
Navegador / Extensão Chrome / Cron → Coletor / IA Gemini → Scrapers & APIs → DB (Neon / SQLite) → FastAPI → Dashboard SPA
                                                                 ↓
                                                   Alertas Individuais Telegram
```

---

## Recursos Principais

### 1. Contas de Usuário & Autenticação Segura (`app/auth.py`)
* **Cadastro e Login Criptográfico:** Autenticação com derivação de chave `scrypt` (resistente a ataques de hardware/GPU) e cookies de sessão seguros (`HttpOnly`, `SameSite=Lax`) assinados via `HMAC-SHA256` (`pt_session` com validade configurável de 7 a 30 dias).
* **Login com Google (OAuth2 GIS):** Integração com Google Identity Services via token JWT verificado pelo backend sem expor credenciais sensíveis (dispensa Client Secret no servidor).
* **Painel "Minha Conta":** Edição completa de perfil (nome, e-mail, Chat ID do Telegram), alteração de senha e modal de confirmação de logout.
* **Isolamento de Dados por Usuário:** Produtos e listagens cadastrados são vinculados ao usuário autenticado (`user_id`). Caso a aplicação seja usada sem autenticação ou com um único usuário inicial, produtos órfãos legados são adotados automaticamente.

### 2. Alertas Individuais no Telegram (`app/notifier.py`)
* **Notificação Direta por Usuário:** Cada usuário cadastra seu próprio `telegram_chat_id` e recebe notificações instantâneas no seu celular apenas quando os produtos de seu interesse atingirem o preço-alvo.
* **Descoberta Automática do Bot:** Detecção automática do `@username` do bot através da API `/getMe` do Telegram, permitindo iniciar conversas com 1 clique direto no painel web.
* **Disparo de Teste & Mini-Manual:** Instruções passo a passo integradas na interface com botão de teste para validar a entrega de alertas imediatamente.
* **Proteção Anti-Spam:** Cooldown inteligente de 24 horas por produto e chancela estatística de queda de preço para evitar notificações repetitivas.

### 3. Inteligência Artificial com Google AI Studio / Gemini (`app/ai.py`)
* **Descoberta Inteligente de Links (`POST /api/ai/discover-links`):** Varredura híbrida para encontrar as melhores ofertas de um produto combinando as APIs oficiais da Shopee e Mercado Livre com a capacidade de pesquisa em tempo real do **Google Search Grounding** do Gemini (`gemini-2.5-flash`).
* **Curadoria Anti-Acessórios com IA:** Análise semântica via Gemini que descarta automaticamente acessórios irrelevantes (capas, películas, cabos, fontes e suportes) ou modelos divergentes, com algoritmo de fallback heurístico inteligente para funcionamento offline ou sem chave de API.
* **Auto-Recuperação de Links (*Self-Healing Links*):** Quando um anúncio é pausado, esgotado ou removido pelo vendedor (`404` ou sem estoque), o sistema busca automaticamente um anúncio equivalente e ativo durante a coleta de rotina em `app/collector.py` ou sob demanda (`POST /api/listings/{id}/heal`).

### 4. Histórico Compartilhado de Links Canônicos & Extensão de Navegador
* **Canonicidade e Deduplicação (`canonicalize_url`):** URLs de produtos de todas as lojas suportadas são normalizadas para chaves canônicas (ex: `mercadolivre:MLB12345`, `shopee:1234:5678`, `amazon:B08N5WRWNW`).
* **Economia de Scraping & Histórico Imediato:** Se múltiplos usuários monitoram o mesmo produto/loja, a coleta é executada apenas uma vez e os preços são compartilhados. Um novo usuário que cadastre um link já monitorado obtém instantaneamente todo o histórico de preços acumulado.
* **Preservação de Histórico:** A exclusão de um produto por um usuário não apaga o histórico de preços do anúncio (`product_id = NULL`), beneficiando outros monitores e análises estatísticas.
* **Extensão Manifest V3 para Chrome e Edge (`extension/`):** Adiciona produtos e links ao Price Tracker com 1 clique diretamente da página do anúncio, autenticada via token Bearer de 90 dias gerado na tela Minha Conta.

### 5. Coleta Especializada & Livre de Bloqueios
* **Mercado Livre:** 100% via **API Oficial** com suporte a catálogo BuyBox (`/products/{id}`), ofertas individuais e multiget de itens. Renovação automática de OAuth2 com refresh token de 6 meses persistido no banco.
* **Shopee Brasil:** 100% via **API Oficial de Afiliados** (GraphQL `productOfferV2`) autenticada com HMAC-SHA256.
* **AliExpress:** Extração do preço em BRL diretamente do parâmetro `pdp_npi` da URL copiada do app ou navegador.
* **Amazon Brasil:** Seletores hierárquicos para o novo padrão de Buybox (`.priceToPay`, `#corePriceDisplay`, inputs de precificação) e detecção de desafios de CAPTCHA.
* **Lojas Gerais:** Extração robusta via JSON-LD (`schema.org/Offer`), meta tags (`og:price:amount`, `product:price:amount`) ou seletores CSS personalizados.

### 6. Termômetro de Promoção (30 & 60 dias)
* Análise estatística de histórico que classifica cada oferta em:
  * **Promoção Real:** Desconto genuíno em relação à média histórica ponderada.
  * **Metade do Dobro (Fake Deal):** Detecta quando o lojista inflou o preço artificialmente nos dias que antecederam a suposta promoção.
  * **Menor Preço em 60 dias:** Destaca oportunidades excepcionais de compra.

---

## Configuração do Ambiente (`.env`)

Copie o arquivo de exemplo e configure suas variáveis:

```bash
cp .env.example .env
```

| Variável | Obrigatória? | Descrição |
| :--- | :---: | :--- |
| `DATABASE_URL` | Não | String de conexão do PostgreSQL (Neon). Se omitida, usa SQLite local em `data/tracker.db`. |
| `APP_SECRET_KEY` / `SECRET_KEY` | Não | Chave secreta para assinar cookies HMAC de sessão (gerada e persistida automaticamente no banco se vazia). |
| `SESSION_TTL_DAYS` | Não | Tempo de validade do cookie de sessão em dias (padrão: `30`). |
| `GOOGLE_CLIENT_ID` | Não | Client ID do Google Identity Services (OAuth2) para habilitar botão "Entrar com o Google". |
| `TELEGRAM_BOT_TOKEN` | Sim* | Token do Bot criado com o [@BotFather](https://t.me/BotFather) no Telegram (necessário para alertas). |
| `TELEGRAM_CHAT_ID` | Não | Chat ID global de fallback caso o usuário não informe um chat_id individual. |
| `TELEGRAM_BOT_USERNAME` | Não | Username do bot sem `@` (descoberto automaticamente via `/getMe` se omitido). |
| `GEMINI_API_KEY` | Não | Chave gratuita do [Google AI Studio](https://aistudio.google.com/) para habilitar busca inteligente e auto-healing. |
| `GEMINI_MODEL` | Não | Modelo Gemini a utilizar (padrão: `gemini-2.5-flash`). |
| `MERCADOLIVRE_CLIENT_ID` | Não | Client ID da sua aplicação no [Mercado Livre Developers](https://developers.mercadolivre.com.br/). |
| `MERCADOLIVRE_CLIENT_SECRET` | Não | Client Secret da sua aplicação no Mercado Livre. |
| `MERCADOLIVRE_REFRESH_TOKEN` | Não | Refresh Token inicial do Mercado Livre (o sistema renova automaticamente a cada 6 meses). |
| `MERCADOLIVRE_TOKEN` | Não | Token de acesso estático opcional (válido por apenas 6 horas). |
| `SHOPEE_APP_ID` | Não | App ID da sua conta no [Shopee Affiliate Open API](https://affiliate.shopee.com.br/open_api). |
| `SHOPEE_APP_SECRET` | Não | Secret Key da sua conta no Shopee Affiliate. |
| `APP_USERNAME` / `APP_PASSWORD` | Não | Autenticação HTTP Basic legada para proteção adicional da aplicação. |

---

## Executando Localmente

```bash
# 1. Crie e ative o ambiente virtual Python
python3 -m venv .venv && source .venv/bin/activate

# 2. Instale as dependências
pip install -r requirements.txt -r requirements-dev.txt

# 3. Execute a suíte de testes automatizados (99 testes unitários e de integração)
pytest -v

# 4. Inicie o servidor FastAPI
uvicorn app.main:app --reload --port 8000
```

Acesse o painel web no navegador em **`http://localhost:8000`**.

---

## Como Instalar a Extensão no Chrome / Edge

1. No navegador, acesse `chrome://extensions` (Chrome) ou `edge://extensions` (Edge).
2. Ative o **Modo do desenvolvedor** (chave no canto superior direito).
3. Clique em **Carregar sem compactação** e selecione a pasta `extension/` deste repositório.
4. No painel web do Price Tracker, acesse a página **Minha Conta**, gere um **Token da Extensão** e cole no popup da extensão.
5. Pronto! Ao navegar em qualquer produto suportado, clique no ícone da extensão para monitorá-lo com 1 clique.

---

## Comandos via Terminal (CLI)

O Price Tracker também pode ser operado via terminal:

```bash
# Cadastrar um produto e links
python -m app.cli add-product "Mouse Attack Shark X11" --target 120.00
python -m app.cli add-listing 1 aliexpress "https://pt.aliexpress.com/item/..."
python -m app.cli add-listing 1 mercadolivre "https://www.mercadolivre.com.br/..."
python -m app.cli add-listing 1 shopee "https://shopee.com.br/product/..."

# Executar a rotina de coleta manualmente
python -m app.cli collect
```

---

## Boas Práticas e Arquitetura de Coleta

* **Respeito aos Servidores:** A coleta aplica um intervalo de 3 a 8 segundos aleatórios entre requisições de lojas diferentes para evitar bloqueios ou sobrecargas.
* **Agendamento Recomendado:** A coleta automática periódica pode ser configurada via Cron a cada 6 ou 8 horas.
* **Sem Headless Pesado:** Arquitetura 100% assíncrona baseada em HTTP e APIs oficiais, consumindo menos de 80 MB de memória RAM e operando com folga no plano gratuito do Render.
