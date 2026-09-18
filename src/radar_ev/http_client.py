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


class ApiQuotaExhaustedError(HTTPClientError):
    """Cota diária da API-Football esgotada.

    A API-Football retorna HTTP 200 com ``results: 0`` e campo ``errors``
    preenchido quando o limite diário de requisições é atingido. Esse erro é
    DISTINTO de rate limit (429): não adianta retentar no mesmo dia, o
    orquestrador deve encerrar o processo com código de saída próprio (2).
    """

    def __init__(self, message: str = "Cota diária da API-Football esgotada") -> None:
        super().__init__(message, status_code=200)


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
            RateLimitError: Se a API retornar 429 ou soft rate limit.
            ApiQuotaExhaustedError: Se a cota diária estiver esgotada
                (200 com ``errors`` de "limit of request by day").
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
            
            # API-Football retorna 200 OK mas com erro de limite no JSON
            # (soft rate limit) ou cota diária esgotada (results: 0 + errors).
            errors = data.get("errors", {})
            if isinstance(errors, dict) and errors:
                msg = " ".join(str(v) for v in errors.values()).lower()
                if "limit of request by day" in msg or "quota" in msg:
                    log.error(
                        "api_football_quota_exhausted",
                        errors=errors,
                        results=data.get("results"),
                    )
                    raise ApiQuotaExhaustedError(
                        f"Cota diária da API-Football esgotada: {errors}"
                    )
                if "requests" in errors or "rateLimit" in errors:
                    log.warning("rate_limit_hit", errors=errors)
                    raise RateLimitError(f"Rate limit da API atingido: {errors}")

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

        except ApiQuotaExhaustedError:
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
