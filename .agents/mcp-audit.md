# Auditoria MCP — linkedin-bot

Data: 2026-10-09  
Escopo: servidor `linkedin-bot` em stdio configurado em `~/.cursor/mcp.json`, descrições de ferramentas carregadas nesta sessão e implementação local do repositório. A descoberta automática do scanner encontrou 21 servidores em vários perfis; servidores remotos e plugins alheios ao projeto não foram contatados.

## Resultado

Não encontrei instruções escondidas ou imperativas suspeitas nas descrições visíveis das ferramentas `linkedin_status`, `linkedin_preview_post` e `linkedin_publish_post`. O servidor do projeto usa transporte stdio; não expõe listener HTTP. As entradas de preview aceitam caminhos absolutos de imagem/vídeo, sem ferramenta que busque URLs, então SSRF não se aplica a essa superfície.

### M-1 — Versão MCP ativa difere do código auditado

**Severidade: média · AML.T0010 / AML.T0053**

A configuração do Cursor executa `/Users/joaomarques/.local/bin/linkedin-bot`; `linkedin-bot doctor` reportou a instalação 0.1.0. O repositório declara 0.2.0 em `pyproject.toml`. A superfície de ferramentas carregada nesta sessão também não coincide com o código 0.2.0: a descrição ativa expõe publicação síncrona e três ferramentas, enquanto o código local documenta publicação por job e inclui `linkedin_publish_status`. Isso é evidência de deriva de versão, não evidência suficiente de rug pull malicioso. O comportamento do binário ativo não ficou confirmado pelo scanner.

**Recomendação:** reinstalar/atualizar o CLI a partir da revisão auditada e reenumerar as ferramentas antes de aprovar a superfície em uso.

### M-2 — Confirmação de publicação não é obrigatória em todos os clientes MCP

**Severidade: alta · AML.T0053 / AML.T0057**

No código local 0.2.0, `ask_confirmation` retorna `None` quando o cliente não anuncia suporte a elicitation (`server.py:277–283`). O corpo de `linkedin_publish_post` só cancela se recebe uma resposta explícita negativa; `None` prossegue até `manager.start(...)` (`server.py:302–316`). Assim, um cliente MCP sem elicitation pode publicar usando um `preview_id` sem que o servidor faça uma confirmação afirmativa. A descrição e as instruções do servidor dizem para obter aprovação explícita, mas a condição não é aplicada pelo servidor em todos os clientes.

**Recomendação:** exigir `confirmation.confirm is True` antes de iniciar publicação, ou recusar publicação quando elicitation não estiver disponível. A versão ativa 0.1.0 não foi inspecionada diretamente; confirmar se ela tem o mesmo controle.

## Cobertura e limitações

- O scanner `mcp-scan` mostrou aviso de que o pacote foi renomeado para `snyk-agent-scan`. A descoberta automática listou 21 servidores e pediu consentimento individual; nenhum servidor foi iniciado nessa varredura ampla.
- A varredura focalizada do `~/.cursor/mcp.json` autorizou o processo local, mas terminou com código 1 sem relatório útil. Portanto, não há resultado do scanner para tool poisoning, toxic flows ou hashes pinados.
- A configuração é stdio e não declara endpoint HTTP remoto; verificação de autenticação/rede não se aplica a este servidor.
- Não foi executado teste dinâmico de SSRF; a implementação auditada não tem ferramenta de fetch por URL.
- Não foi criado baseline de hashes de descrição; detecção futura de rug pull permanece pendente.
- Nenhum servidor remoto foi sondado e nenhuma publicação foi feita.
