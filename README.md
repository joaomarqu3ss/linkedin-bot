# LinkedIn Bot

Automação de publicações no LinkedIn utilizando a API oficial (**LinkedIn REST Posts API**).

O bot publica no **seu perfil pessoal**, assinado por você (`author: urn:li:person:{id}`).

## Estado atual

| Camada | Status |
| --- | --- |
| `auth/` — OAuth 2.0 3-legged, storage, sessão | ✅ pronta |
| `transport/` — clientes HTTP, retry, erros, redação | ✅ pronta |
| `cli/` — `linkedin-bot auth …` | ✅ pronta |
| `media/` — upload de imagem | ✅ pronta |
| `posts/` — criação de posts (texto, imagem, multiImage) | ✅ pronta |
| `media/` — vídeo e documento | ⏳ próxima |

## Instalação

### macOS e Linux

```bash
./install.sh
```

### Windows (PowerShell)

```powershell
.\install.ps1
```

> Se o PowerShell bloquear a execução:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

O instalador faz tudo em cinco etapas: verifica o ambiente, instala o
[uv](https://docs.astral.sh/uv/) se necessário (ele traz o próprio Python), instala
o bot, confere o `PATH` — oferecendo adicionar ao seu perfil de shell — e roda o
diagnóstico. Use `--yes` / `-Yes` para não perguntar nada.

Nada é instalado no Python do sistema: o `uv` mantém a ferramenta isolada, o que
evita o bloqueio `EXTERNALLY-MANAGED` (PEP 668) do Homebrew e das distribuições Linux.

### Verificar o ambiente

```bash
linkedin-bot doctor
```

Checa sistema, Python, instalação, `PATH`, diretório e permissões das credenciais,
porta do callback, alcance da rede, versão da API e estado da sessão. Cada problema
vem com a correção. Sai com código 1 apenas em falhas — avisos não interrompem.

### Instalação manual

```bash
uv tool install --editable .
```

## Uso

```bash
linkedin-bot auth setup     # registra o app (wizard) e autentica em seguida
linkedin-bot auth login     # autentica pelo navegador
linkedin-bot auth status    # identidade, escopos e validade do token
linkedin-bot auth logout    # remove o token (--all remove também o app)

linkedin-bot post "Meu texto" --image foto.jpg
linkedin-bot post --from-file post.txt --image a.jpg --image b.jpg --alt "descrição"
cat post.txt | linkedin-bot post - --image foto.jpg
linkedin-bot post "rascunho" --image foto.jpg --dry-run
```

### `post`

| Flag | Efeito |
| --- | --- |
| `TEXTO` | texto do post; `-` lê de stdin |
| `--from-file ARQUIVO` | lê o texto de um arquivo (exclusivo com `TEXTO`) |
| `--image CAMINHO` | repetível: 1 imagem vira `content.media`, 2–20 viram `content.multiImage` |
| `--alt TEXTO` | repetível: um vale para todas, ou um por `--image` |
| `--visibility public\|connections` | padrão `public` |
| `--dry-run` | valida as imagens e imprime o JSON sem enviar nem publicar |

Imagens são validadas **antes** de qualquer chamada de rede: formato (JPG, PNG, GIF)
e contagem de pixels (< 36.152.320) são lidos do cabeçalho do arquivo, sem
dependência de biblioteca de imagem.

O texto passa por escape do formato `little` automaticamente — `#`, `@`, `(`, `)`,
`*`, `_`, `~` e afins viram literais. **Hashtags e menções clicáveis ainda não são
suportadas**: exigem sintaxe própria e ficaram para a próxima etapa.

> **Não existe rascunho.** `lifecycleState: PUBLISHED` é o único valor aceito na
> criação pela API. Todo post é real; use `--dry-run` antes.

O `auth setup` guia o cadastro do app no [Developer Portal](https://www.linkedin.com/developers/apps).
Você vai precisar de uma **LinkedIn Page** como *publisher* do app — ela é apenas
metadado administrativo e **não** aparece nos seus posts. Se não tiver uma, o wizard
indica onde criar (tipo "Autônomo", tamanho "0-1" são aceitos).

Cadastre `http://localhost:8765/callback` em **Auth > Redirect URLs** no app.

## Ciclo de vida do token

O LinkedIn emite tokens de **60 dias** e não oferece refresh token programático
fora do programa MDP. Renovar exige um round-trip pelo navegador — não existe
caminho headless.

Na prática: o bot roda sozinho por 60 dias e você executa `auth login`
(~30 segundos) cerca de 6 vezes por ano. `auth status` avisa quando faltam 3 dias
ou menos.

Nenhum comando abre o navegador sozinho: se o token morrer durante uma execução
automatizada, a falha é imediata e explícita (`TokenExpiredError`), nunca um
processo travado esperando interação.

## Configuração

| Variável | Padrão | Uso |
| --- | --- | --- |
| `LINKEDIN_API_VERSION` | `202608` | Versão da API (`YYYYMM`). Existe para destravar o bot se a versão embutida for descontinuada. |
| `LINKEDIN_CALLBACK_PORT` | `8765` | Porta do callback local. Precisa bater com a redirect URI cadastrada. |
| `XDG_CONFIG_HOME` | `~/.config` | Raiz de `linkedin-bot/credentials.json` (arquivo `0600`, diretório `0700`). |

## Referência

[`LINKEDIN_API_SPEC.md`](LINKEDIN_API_SPEC.md) — especificação completa da Posts API
compilada da documentação oficial.
