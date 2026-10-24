# 🛒 Extensão Price Tracker — Manual do Usuário

Bem-vindo à extensão oficial do **Price Tracker**! Com ela, você pode adicionar e monitorar produtos das suas lojas favoritas em **1 clique**, diretamente da página do produto enquanto navega.

---

## 📋 Sumário
1. [Navegadores Compatíveis](#1-navegadores-compatíveis)
2. [Instalação Passo a Passo](#2-instalação-passo-a-passo)
3. [Como Conectar sua Conta](#3-como-conectar-sua-conta)
4. [Como Adicionar Produtos em 1 Clique](#4-como-adicionar-produtos-em-1-clique)
5. [Dicas e Resolução de Problemas](#5-dicas-e-resolução-de-problemas)

---

## 1. Navegadores Compatíveis
A extensão utiliza a especificação moderna **Manifest V3** e é 100% compatível com todos os navegadores baseados em Chromium:
- **Google Chrome**
- **Microsoft Edge**
- **Brave Browser**
- **Opera** e **Opera GX**
- **Vivaldi**

---

## 2. Instalação Passo a Passo

### Passo 1: Extrair o arquivo compactado
1. Localize o arquivo `price-tracker-extension.zip` baixado.
2. Clique com o botão direito e selecione **"Extrair Tudo..."** (ou descompacte usando seu utilitário favorito).
3. Uma pasta chamada `price-tracker-extension` será criada. Guarde-a em um local permanente (como sua pasta `Documentos` ou `Programas`), pois o navegador usará os arquivos dessa pasta diretamente.

### Passo 2: Acessar a página de Extensões do Navegador
Abra seu navegador e acesse a página de gerenciamento de extensões:
- **Google Chrome**: digite `chrome://extensions` na barra de endereços e pressione Enter.
- **Microsoft Edge**: digite `edge://extensions` e pressione Enter.
- **Brave**: digite `brave://extensions` e pressione Enter.
- **Opera**: digite `opera://extensions` e pressione Enter.

### Passo 3: Ativar o "Modo do desenvolvedor"
No canto superior direito da tela de extensões, localize e ative a chave seletora **Modo do desenvolvedor** (*Developer mode*).

### Passo 4: Carregar a Extensão
1. Com o Modo do desenvolvedor ativado, novos botões aparecerão no topo da tela.
2. Clique no botão **"Carregar sem compactação"** (ou *"Load unpacked"* no Edge/Brave em inglês).
3. Na janela de seleção de pastas, escolha a pasta `price-tracker-extension` descompactada no Passo 1 e clique em **Selecionar pasta**.
4. Pronto! O ícone do **Price Tracker** aparecerá na sua lista de extensões.

> 💡 **Dica de Usabilidade**: Clique no ícone de quebra-cabeça (🧩) na barra de ferramentas do seu navegador e clique no alfinete (📌) ao lado do **Price Tracker** para fixar o ícone sempre visível na barra superior.

---

## 3. Como Conectar sua Conta

Ao clicar no ícone da extensão pela primeira vez, será solicitada a conexão com a sua conta do Price Tracker. Você tem duas opções simples:

### Opção A: Conexão via Token (Recomendada — 1 Clique)
Ideal para todos os usuários, especialmente quem utiliza o **Login com Google**:
1. No painel web do Price Tracker, acesse a aba **Minha Conta**.
2. No card **Extensão do Navegador**, clique no botão **"Gerar e Copiar Token da Extensão"**.
3. O token será gerado e copiado automaticamente para sua área de transferência.
4. Abra a extensão no navegador, certifique-se de que a aba **Token da Extensão** está selecionada, cole o token no campo correspondente e clique em **Conectar Extensão**.

### Opção B: E-mail e Senha
1. Abra o popup da extensão e clique na aba **E-mail e Senha**.
2. Digite o mesmo e-mail e senha cadastrados no Price Tracker.
3. Clique em **Conectar Extensão**.

> 🌐 **URL do Servidor**: Se você baixou o `.zip` diretamente do seu servidor do Price Tracker, a URL da API já vem configurada automaticamente! Caso queira alterá-la para outro domínio ou endereço local (ex.: `http://localhost:8000`), basta clicar em *Servidor Price Tracker* no rodapé da tela de conexão e informar a nova URL.

---

## 4. Como Adicionar Produtos em 1 Clique

### Lojas Homologadas com Reconhecimento Automático:
- **Amazon Brasil** (`amazon.com.br`)
- **Mercado Livre** (`mercadolivre.com.br`)
- **Shopee Brasil** (`shopee.com.br`)
- **Kabum** (`kabum.com.br`)
- **AliExpress** (`pt.aliexpress.com` / `aliexpress.com`)

### Passo a passo para salvar um anúncio:
1. Navegue normalmente até a página do produto desejado em qualquer loja compatível.
2. Clique no ícone do **Price Tracker** na barra do navegador.
3. A extensão reconhecerá a loja e o link automaticamente.
4. Você pode escolher:
   - **Novo Produto**: cria um novo produto monitorado. O nome da página já vem preenchido automaticamente, você pode ajustá-lo, definir um **Preço Alvo (R$)** e escolher ou criar uma **Categoria**.
   - **Adicionar a Produto Existente**: vincula este link como mais uma loja concorrente de um produto que você já rastreia (ex.: já monitora o produto na Amazon e agora quer rastrear também a oferta dele no Mercado Livre).
5. Clique em **"Salvar e Monitorar Preço"**.
6. Pronto! O produto é salvo instantaneamente e o sistema já dispara uma coleta imediata em segundo plano para capturar o preço atualizado.

---

## 5. Dicas e Resolução de Problemas

- **A extensão mostra "Desconectado" ou erro de conexão?**
  Verifique se o seu servidor do Price Tracker está ligado e acessível. Abra as configurações de servidor clicando no texto do rodapé do popup para checar se a URL está correta.
- **Mudei a pasta da extensão de lugar no computador?**
  Se você mover a pasta de arquivos, o navegador indicará erro de diretório. Basta acessar `chrome://extensions`, remover a extensão antiga e clicar novamente em "Carregar sem compactação" apontando para o novo local.
- **Como atualizar a extensão?**
  Basta baixar o novo `.zip`, substituir os arquivos na mesma pasta e clicar no botão de recarregar (ícone de seta circular 🔄) no card da extensão em `chrome://extensions`.
