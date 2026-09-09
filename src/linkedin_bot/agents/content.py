"""Conteúdo da skill — fonte única de verdade.

O mesmo corpo alimenta todos os alvos; muda apenas o invólucro (frontmatter,
marcadores de seção) e o caminho de destino. Escrito em inglês porque é a língua
franca do ferramental de agentes, e os modelos casam o gatilho entre idiomas.
"""

NAME = "linkedin-bot"

# Context pointer: o que decide se o agente alcança o corpo. Três gatilhos, um
# por ramo real do documento.
DESCRIPTION = (
    "Publish LinkedIn posts with the linkedin-bot CLI. Use when the user asks to "
    "post or publish on LinkedIn, attach images to a LinkedIn post, or fix "
    "LinkedIn authentication."
)

BODY = """\
# linkedin-bot

Publishes to the user's own LinkedIn profile from the terminal. `linkedin-bot --help`
carries the current command surface.

## Publishing is irreversible

The LinkedIn API has no draft state — `PUBLISHED` is the only value it accepts on
creation. A post is live the moment the command returns.

So publish in three steps, every time:

1. Run the exact command with `--dry-run` and show the user the JSON it prints.
2. Get explicit approval for that text.
3. Re-run the same command without `--dry-run`.

Publish the text the user approved, verbatim.

## Check the session first

```
linkedin-bot doctor
```

Reports the environment and whether the token is still valid. Tokens last 60 days and
renewing one needs a browser, so a dead session belongs to the user: tell them to run
`linkedin-bot auth login`, and stop there.

## Composing the text

Pass the text as an argument, or use `--from-file` when it has line breaks. Reserved
characters are escaped for you — write the text plainly.

`#tag` and `@name` publish as literal text rather than links. When the user's text
leans on a working hashtag or mention, say so before publishing.

## Images

`--image` accepts JPG, PNG and GIF under 36,152,320 pixels, and repeats for up to 20
files. One image attaches as media; two or more become a multi-image post. Pair each
`--image` with an `--alt`, or pass a single `--alt` that describes them all.

Images are validated before any upload begins, so a bad file fails before anything
reaches LinkedIn.

## Exit codes

`0` on success, `1` on failure. Failures print the fix on stderr — read it before
retrying.
"""


def skill_md() -> str:
    """Formato SKILL.md (Claude Code): frontmatter YAML + corpo."""
    return f"---\nname: {NAME}\ndescription: {DESCRIPTION}\n---\n\n{BODY}"


def mdc() -> str:
    """Formato .mdc (Cursor): frontmatter próprio + corpo."""
    return f"---\ndescription: {DESCRIPTION}\nalwaysApply: false\n---\n\n{BODY}"


def plain() -> str:
    """Markdown puro, para diretórios de regras."""
    return BODY
