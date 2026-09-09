"""Exceções tipadas.

Toda falha previsível vira uma destas, com mensagem acionável — o usuário da CLI
nunca deve precisar interpretar um traceback ou um status HTTP cru.
"""

from __future__ import annotations


class LinkedInBotError(Exception):
    """Base de todos os erros da ferramenta."""


# --- Autenticação ---------------------------------------------------------


class AuthError(LinkedInBotError):
    pass


class NotConfiguredError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            "Nenhum app LinkedIn configurado. Rode 'linkedin-bot auth setup'."
        )


class NotAuthenticatedError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            "Você não está autenticado. Rode 'linkedin-bot auth login'."
        )


class TokenExpiredError(AuthError):
    def __init__(self, detail: str = "") -> None:
        msg = "Token expirado ou revogado. Rode 'linkedin-bot auth login'."
        super().__init__(f"{msg} ({detail})" if detail else msg)


class StateMismatchError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            "O parâmetro 'state' devolvido não confere com o enviado — possível CSRF. "
            "Nenhuma credencial foi gravada. Refaça o login."
        )


class AuthorizationDeniedError(AuthError):
    def __init__(self, code: str, description: str = "") -> None:
        motivo = {
            "user_cancelled_login": "você cancelou o login no LinkedIn",
            "user_cancelled_authorize": "você negou a permissão ao app",
        }.get(code, code)
        super().__init__(f"Autorização não concluída: {motivo}."
                         + (f" {description}" if description else ""))


class InvalidScopeError(AuthError):
    """O app não tem os produtos que concedem os escopos pedidos."""

    def __init__(self, scopes: tuple[str, ...], description: str = "") -> None:
        super().__init__(
            "O LinkedIn recusou os escopos solicitados ({}).\n\n"
            "Criar o app não concede escopo nenhum — é preciso adicionar os produtos\n"
            "na aba Products do app em https://www.linkedin.com/developers/apps :\n\n"
            "  Share on LinkedIn                        → w_member_social\n"
            "  Sign In with LinkedIn using OpenID Connect → openid, profile\n\n"
            "Os DOIS são necessários. Confirme na aba Auth, seção 'OAuth 2.0 scopes',\n"
            "que os três aparecem provisionados. Se estiverem pendentes, verifique o app\n"
            "com a sua Page em Settings > Verify.".format(", ".join(scopes))
            + (f"\n\nResposta do LinkedIn: {description}" if description else "")
        )


class CallbackTimeoutError(AuthError):
    def __init__(self, seconds: int) -> None:
        super().__init__(
            f"Nenhuma resposta do LinkedIn em {seconds // 60} minutos. Refaça o login."
        )


class PortInUseError(AuthError):
    def __init__(self, port: int) -> None:
        super().__init__(
            f"A porta {port} está em uso. Use '--port <outra>' e cadastre a redirect URI "
            f"correspondente em Auth > Redirect URLs no portal de desenvolvedor."
        )


class OAuthError(AuthError):
    """Erro devolvido por /oauth/v2/accessToken.

    A resposta crua do LinkedIn SEMPRE aparece na mensagem. A interpretação é um
    palpite auxiliar; o texto original é o que permite diagnosticar de verdade.
    """

    def __init__(self, error: str, description: str, status: int) -> None:
        self.error = error
        self.description = description
        self.status = status

        parts = [f"Falha ao obter o token de acesso (HTTP {status})."]
        parts.append(f"\n  LinkedIn respondeu: {error}")
        if description:
            parts.append(f"\n  Descrição:          {description}")
        hint = self._hint()
        if hint:
            parts.append(f"\n\nProvável causa: {hint}")
        super().__init__("".join(parts))

    def _hint(self) -> str:
        d = self.description.lower()
        if "code verifier" in d:
            return (
                "o PKCE. Rode com LINKEDIN_PKCE=0 (padrão) para não enviar\n"
                "  code_challenge/code_verifier."
            )
        if "authorization code" in d and ("not found" in d or "expired" in d):
            return (
                "o código de autorização expirou (vale 30 min, uso único) ou já foi\n"
                "  usado. Rode 'linkedin-bot auth login' novamente."
            )
        if self.error == "invalid_redirect_uri" or "redirect" in d:
            return (
                "a redirect URI não confere com a cadastrada no app. Confirme que\n"
                "  'http://localhost:8765/callback' está em Auth > Redirect URLs."
            )
        if "client" in d and ("authentication" in d or "invalid" in d):
            return (
                "Client ID ou Client Secret incorretos. Rode 'linkedin-bot auth setup'\n"
                "  e cole os valores novamente (atenção a espaços no fim)."
            )
        return ""


# --- API ------------------------------------------------------------------


class ApiError(LinkedInBotError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


class ApiVersionSunsetError(ApiError):
    def __init__(self, version: str) -> None:
        super().__init__(
            426,
            f"a versão {version} da API foi descontinuada. Atualize o pacote ou defina "
            f"a variável de ambiente LINKEDIN_API_VERSION com uma versão ativa "
            f"(formato YYYYMM).",
        )


class RateLimitError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            429,
            "limite de requisições atingido. Os limites resetam à meia-noite UTC; "
            "consulte a aba Analytics do seu app no portal de desenvolvedor.",
        )


# --- Mídia e posts --------------------------------------------------------


class MediaError(LinkedInBotError):
    pass


class ImageNotFoundError(MediaError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Imagem não encontrada: {path}")


class UnsupportedImageFormatError(MediaError):
    def __init__(self, path: str, suffix: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            f"Formato não suportado em {path}: '{suffix or 'sem extensão'}'. "
            f"A LinkedIn aceita apenas {', '.join(allowed)}."
        )


class ImageTooLargeError(MediaError):
    def __init__(self, path: str, pixels: int, limit: int) -> None:
        super().__init__(
            f"{path} tem {pixels:,} pixels; o limite da LinkedIn é {limit:,}. "
            "Redimensione a imagem antes de publicar."
        )


class TooManyImagesError(MediaError):
    def __init__(self, count: int, limit: int) -> None:
        super().__init__(
            f"{count} imagens informadas; um post aceita no máximo {limit}."
        )


class UploadFailedError(MediaError):
    def __init__(self, path: str, status: int, body: str) -> None:
        super().__init__(
            f"Falha ao enviar {path} (HTTP {status}).\n  Resposta: {body[:300]}"
        )


class PostCreationError(LinkedInBotError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        super().__init__(
            f"A LinkedIn recusou a criação do post (HTTP {status}).\n"
            f"  Resposta: {body[:500]}"
        )
