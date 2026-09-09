"""Escape do formato `little`, usado no campo `commentary`.

A LinkedIn reserva um conjunto de caracteres para menções, hashtags e templates.
A documentação é explícita: *todo* caractere reservado precisa ser escapado com
barra invertida, mesmo quando não faz parte de um elemento.

Sem isso, um post com `taxa de 50% (bruto)` ou `C:\\dev` seria rejeitado ou
renderizado errado.
"""

from __future__ import annotations

# Ordem irrelevante: a substituição é feita caractere a caractere, e a barra
# invertida é tratada no mesmo passo (nunca há dupla passagem).
RESERVED = frozenset("\\|{}@[]()<>#*_~")


def escape(text: str) -> str:
    """Escapa todo caractere reservado, tornando o texto literal.

    Menções (`@[Nome](urn:li:person:x)`) e hashtags clicáveis usam sintaxe própria
    e NÃO passam por aqui — quem quiser esses elementos precisa construí-los
    explicitamente.
    """
    return "".join("\\" + c if c in RESERVED else c for c in text)
