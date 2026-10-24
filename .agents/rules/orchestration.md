---
trigger: always_on
---
# Regra de Orquestração de Modelos e Subagentes

- **Agente Principal (Orquestrador / Tech Lead)**: Responsável por planejar a arquitetura, esclarecer requisitos com o usuário, revisar segurança/performance e validar a suíte final de testes.
- **Subagentes de Implementação (`Model: "flash"`)**: Sempre que houver tarefas de implementação ou alterações de código em múltiplos arquivos/camadas, delegue a codificação via `invoke_subagent` configurando `Model: "flash"` (Gemini Flash).
- Divida o trabalho em subagentes com escopos de arquivos disjuntos (ex.: Backend vs Frontend) para permitir execução paralela sem conflitos de escrita.

