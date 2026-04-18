import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Optional, Dict, Any

logger = structlog.get_logger()

class HTTPClient:
    """Cliente HTTP assíncrono com retry e logging."""

    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0):
        self.base_url = base_url
        self.headers = headers or {}
        self.timeout = timeout
        self.client = httpx.AsyncClient(base_url=base_url, headers=self.headers, timeout=timeout)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.RequestError)
    )
    async def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Faz uma requisição GET e retorna o JSON."""
        try:
            logger.info("http_request", method="GET", url=f"{self.base_url}{endpoint}", params=params)
            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            logger.info("http_response", status=response.status_code, url=response.url)
            return response.json()
        except httpx.TimeoutException:
            logger.exception("timeout", url=endpoint)
            raise
        except httpx.HTTPStatusError as e:
            logger.exception("http_error", status=e.response.status_code, url=endpoint, response=e.response.text)
            raise
        except Exception:
            logger.exception("unexpected_error", url=endpoint)
            raise

    async def close(self):
        await self.client.aclose()
