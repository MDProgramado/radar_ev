"""
http_client.py — Cliente HTTP assíncrono com retry e logging estruturado.

Abstração sobre httpx com:
- Retry automático com backoff exponencial (tenacity).
- Logging estruturado de requisições e respostas.
- Tratamento específico de timeout e erros HTTP.
- Rate limit awareness (não retenta 429, delega ao orquestrador).
"""

from typing import Any, Dict, Optional

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

_API_REQUEST_COUNT = 0


def get_api_request_count() -> int:
    """Número de requisições HTTP reais feitas desde o último reset."""
    return _API_REQUEST_COUNT


def reset_api_request_count() -> None:
    """Zera o contador (chamar no início de cada execução do pipeline)."""
    global _API_REQUEST_COUNT
    _API_REQUEST_COUNT = 0


class HTTPClientError(Exception):
    """Erro genérico do cliente HTTP."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class RateLimitError(HTTPClientError):
    """Erro específico para rate limit (HTTP 429)."""

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message, status_code=429)


class ApiError(HTTPClientError):
    """Erro retornado pela API-Football no corpo JSON (HTTP 200 + errors).

    A API-Football responde 200 com ``results: 0`` e campo ``errors`` também
    para falhas não relacionadas a limite. Este é o tipo base dos erros de
    classificação; a subclasse indica o motivo e o exit code correspondente:
    quota (2), plano (3), genérico (4).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=200)


class ApiQuotaExhaustedError(ApiError):
    """Cota diária da API-Football esgotada (exit 2).

    Não adianta retentar no mesmo dia; o orquestrador encerra o processo com
    código de saída próprio (2), distinto de crash (1).
    """


class ApiPlanInsufficientError(ApiError):
    """Plano da conta sem acesso ao recurso/saída pedido (exit 3).

    Ex: "Free plans do not have access to this season". Resolver com
    upgrade de plano; encerrar com código próprio (3).
    """


def _errors_text(errors: object) -> str:
    """Serializa o campo ``errors`` para texto pesquisável."""
    if isinstance(errors, dict):
        return " ".join(str(v) for v in errors.values())
    return str(errors)


def raise_for_api_errors(data: dict, endpoint: str) -> None:
    """Classifica e levanta o erro de API-Football vindo no corpo JSON.

    A API-Football responde HTTP 200 com campo ``errors`` para COTA DIÁRIA
    esgotada, PLANO insuficiente e outros erros. Classifica por palavra-chave
    para não rotular tudo como "quota exhausted":

    - "limit of request by day" / "quota" / "rate limit" → ApiQuotaExhaustedError (exit 2)
    - "plan" / "do not have access" / "subscription" → ApiPlanInsufficientError (exit 3)
    - qualquer outro errors não-vazio → ApiError (exit 4)

    Args:
        data: Resposta JSON da API-Football.
        endpoint: Endpoint consultado (para o log estruturado).

    Raises:
        ApiQuotaExhaustedError, ApiPlanInsufficientError ou ApiError.
    """
    errors = data.get("errors")
    if not errors:
        return
    text = _errors_text(errors).lower()

    if "limit of request by day" in text or "quota" in text or "rate limit" in text:
        logger.error(
            "api_football_quota_exhausted",
            endpoint=endpoint,
            errors=errors,
            result_count=data.get("results"),
        )
        raise ApiQuotaExhaustedError(
            f"Cota diária da API-Football esgotada ({endpoint}): {errors}"
        )

    if "plan" in text or "do not have access" in text or "subscription" in text:
        logger.error(
            "api_football_plan_insufficient",
            endpoint=endpoint,
            errors=errors,
            result_count=data.get("results"),
        )
        raise ApiPlanInsufficientError(
            f"Plano da API-Football sem acesso ({endpoint}): {errors}"
        )

    logger.error(
        "api_football_error",
        endpoint=endpoint,
        errors=errors,
        result_count=data.get("results"),
    )
    raise ApiError(f"Erro da API-Football ({endpoint}): {errors}")


class HTTPClient:
    """Cliente HTTP assíncrono com retry e logging.

    Características:
    - Retry automático (3 tentativas) com backoff exponencial para erros de rede.
    - NÃO retenta erros 429 (rate limit) — delega controle ao orquestrador.
    - Log de cada requisição e resposta para observabilidade.

    Usage:
        client = HTTPClient(base_url="https://api.example.com", headers={...})
        data = await client.get("/endpoint", params={"key": "value"})
        await client.close()
    """

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.headers = headers or {}
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers=self.headers,
            timeout=timeout,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.RequestError),
        reraise=True,
    )
    async def get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Faz uma requisição GET e retorna o JSON da resposta.

        Args:
            endpoint: Caminho relativo ao base_url (ex: "/fixtures").
            params: Parâmetros de query string.

        Returns:
            Dicionário com o JSON da resposta.

        Raises:
            RateLimitError: Se a API retornar 429 (HTTP real).
            ApiError: Se o corpo JSON tiver ``errors`` preenchido (200):
                ApiQuotaExhaustedError (cota), ApiPlanInsufficientError
                (plano) ou ApiError (genérico).
            HTTPClientError: Para outros erros HTTP (4xx, 5xx).
            httpx.RequestError: Para erros de rede (retentados automaticamente).
        """
        log = logger.bind(method="GET", endpoint=endpoint, params=params)
        log.info("http_request_start")

        try:
            # Contabiliza a requisição real enviada à API (1 = 1 request do
            # plano; retries de rede contam cada tentativa real).
            global _API_REQUEST_COUNT
            _API_REQUEST_COUNT += 1
            response = await self.client.get(endpoint, params=params)

            # Rate limit — não retenta, levanta exceção específica
            if response.status_code == 429:
                log.warning("rate_limit_hit", status=429)
                raise RateLimitError(
                    f"Rate limit atingido em {endpoint}"
                )

            response.raise_for_status()
            
            data = response.json()

            # API-Football responde 200 OK com field "errors" para cota
            # esgotada, plano insuficiente ou outros erros. Classifica e
            # levanta a exceção específica (quota=2, plano=3, genérico=4).
            raise_for_api_errors(data, endpoint)

            log.info(
                "http_request_success",
                status=response.status_code,
                url=str(response.url),
            )
            return data

        except httpx.TimeoutException:
            log.error("http_timeout", url=endpoint)
            raise

        except httpx.HTTPStatusError as exc:
            log.error(
                "http_status_error",
                status=exc.response.status_code,
                url=endpoint,
                body=exc.response.text[:500],
            )
            raise HTTPClientError(
                f"HTTP {exc.response.status_code} em {endpoint}",
                status_code=exc.response.status_code,
            ) from exc

        except RateLimitError:
            raise

        except ApiError:
            raise

        except Exception:
            log.exception("http_unexpected_error", url=endpoint)
            raise

    async def close(self) -> None:
        """Fecha o cliente HTTP e libera recursos."""
        await self.client.aclose()
        logger.info("http_client_closed", base_url=self.base_url)

    async def __aenter__(self) -> "HTTPClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
