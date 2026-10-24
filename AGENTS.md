# Regras do Projeto — Price Tracker

## Orquestração Padrão de Subagentes
- O agente principal atua como **Arquiteto e Orquestrador**: analisa requisitos, cria planos, revisa segurança/performance e valida os testes.
- Para implementação de funcionalidades e alterações de código na aplicação, utilize subagentes (`invoke_subagent`) com **`Model: "flash"`** por padrão.
- Ao orquestrar subagentes em paralelo, separe claramente os arquivos sob responsabilidade de cada subagente (ex.: um subagente para `app/` e `tests/`, outro para `static/`).
- Consulte sempre `.agents/project-summary.md` para contexto arquitetural do projeto.

