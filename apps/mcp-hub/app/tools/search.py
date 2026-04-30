"""Search tool — web search with pluggable providers.

Pluggable providers:

- `brave` — Brave Web Search API (`BRAVE_SEARCH_API_KEY`, header `X-Subscription-Token`)
- `tavily` / `serper` — require respective API keys
- `duckduckgo` (default) — no key, may fail behind strict firewalls
- `mock` — synthetic results for tests
"""

from __future__ import annotations

import json
import logging
import time
from html import unescape
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.config import get_settings

from .base import BaseTool, ToolContext, ToolResult
from .registry import register

log = logging.getLogger(__name__)


def _format_httpx_error(e: httpx.HTTPError) -> str:
    """httpx often raises timeouts with an empty str(e) — make errors visible in tool UI."""
    name = type(e).__name__
    s = (str(e) or "").strip()
    parts = [f"{name}"] if not s else [f"{name}: {s}"]
    if not s:
        parts.append("empty error text (typical: cannot reach search API from this container)")

    if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
        body = (e.response.text or "")[:500].replace("\n", " ")
        parts.append(f"HTTP {e.response.status_code} {body!r}")
    # ConnectTimeout / ReadTimeout / ConnectError — common behind firewalls or GFW
    if name in {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectError",
        "ReadError",
        "ProxyError",
    }:
        parts.append(
            "outbound to search API may be blocked. If using Docker, set mcp-hub env "
            "HTTPS_PROXY (e.g. http://host.docker.internal:7890) or use Serper/Tavily."
        )
    return " | ".join(parts)


@register
class SearchTool(BaseTool):
    name = "search"
    description = (
        "Search the web and return a ranked list of results (title, url, snippet). "
        "Use for factual lookups, latest news, or when the user asks about external info."
    )
    category = "net"
    mutates = False
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query string."},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "lang": {"type": "string", "description": "Preferred result language, e.g. zh-CN."},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        query = (args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="`query` is required")
        top_k = int(args.get("top_k") or 5)

        started = time.perf_counter()
        provider = settings.search_provider.lower().strip()
        try:
            if provider == "mock":
                results = _provider_mock(query, top_k)
            elif provider in {"brave", "brave_search"}:
                if not settings.brave_search_api_key:
                    return ToolResult(
                        ok=False,
                        error="BRAVE_SEARCH_API_KEY is not set. Add it in .env and set MCP_SEARCH_PROVIDER=brave.",
                    )
                results = await _provider_brave(
                    settings.brave_search_api_key,
                    query,
                    top_k,
                    (args.get("lang") or "").strip() or None,
                )
                provider = "brave"
            elif provider == "tavily" and settings.tavily_api_key:
                results = await _provider_tavily(settings.tavily_api_key, query, top_k)
            elif provider == "serper" and settings.serper_api_key:
                results = await _provider_serper(settings.serper_api_key, query, top_k)
            elif provider in {"duckduckgo", "ddg", ""}:
                results = await _provider_duckduckgo(query, top_k)
                provider = "duckduckgo"
            else:
                # Unknown name: same as default — try DDG once.
                log.warning("search.unknown_provider_fallback_ddg", extra={"provider": provider})
                results = await _provider_duckduckgo(query, top_k)
                provider = "duckduckgo"
        except httpx.HTTPError as e:
            if provider == "mock":
                return ToolResult(ok=False, error=f"search http error: {e}")
            if settings.search_http_error_fallback_mock:
                log.warning(
                    "search.http_error_fallback_mock",
                    extra={"provider": provider, "error": str(e)},
                )
                results = _provider_mock(query, top_k)
                provider = "mock"
            else:
                detail = _format_httpx_error(e)
                log.warning("search.http_error", extra={"provider": provider, "error": detail})
                return ToolResult(
                    ok=False,
                    error=(
                        f"Search request failed ({provider}): {detail}. "
                        "Key/provider: BRAVE_SEARCH_API_KEY + MCP_SEARCH_PROVIDER=brave, or TAVILY/SERPER. "
                        "MCP search uses HTTPS from the mcp-hub container — it must reach the provider. "
                        "Dev-only: MCP_SEARCH_HTTP_ERROR_FALLBACK_MOCK=true."
                    ),
                )

        duration = int((time.perf_counter() - started) * 1000)
        preview_lines = [f"{i + 1}. {r['title']} — {r['url']}" for i, r in enumerate(results)]
        return ToolResult(
            ok=True,
            preview="\n".join(preview_lines) or "(no results)",
            duration_ms=duration,
            output={"provider": provider, "query": query, "results": results},
        )


# ---------- providers ----------


def _provider_mock(query: str, top_k: int) -> list[dict[str, Any]]:
    return [
        {
            "title": f"[MOCK #{i + 1}] Result about: {query}",
            "url": f"https://example.com/mock/{i + 1}?q={query}",
            "snippet": f"Pretend this is the snippet for '{query}' #{i + 1}.",
            "score": 1.0 - i * 0.05,
        }
        for i in range(top_k)
    ]


async def _provider_duckduckgo(query: str, top_k: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )
        r.raise_for_status()
        html = r.text

    link_matches = list(
        re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
    )
    snippet_matches = re.findall(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    snippets = [_clean_html(a or b) for a, b in snippet_matches]

    results: list[dict[str, Any]] = []
    for i, match in enumerate(link_matches[:top_k]):
        raw_url = unescape(match.group("url"))
        results.append(
            {
                "title": _clean_html(match.group("title")),
                "url": _normalize_duckduckgo_url(raw_url),
                "snippet": snippets[i] if i < len(snippets) else "",
                "score": 1.0 - i * 0.05,
            }
        )
    return results


async def _provider_brave(
    api_key: str,
    query: str,
    top_k: int,
    lang: str | None,
) -> list[dict[str, Any]]:
    """Brave Web Search API: https://api.search.brave.com/res/v1/web/search

    httpx follows HTTP_PROXY / HTTPS_PROXY from the process env (e.g. Docker mcp-hub).
    """
    count = min(max(top_k, 1), 20)
    params: dict[str, str | int] = {"q": query, "count": count}
    sl = _brave_search_lang(lang)
    if sl:
        params["search_lang"] = sl

    sec = max(15.0, get_settings().brave_request_timeout_sec)
    timeout = httpx.Timeout(sec, connect=min(25.0, sec))
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        trust_env=True,
    ) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as exc:
            raise httpx.RequestError(
                f"Brave API returned non-JSON: {(r.text or '')[:400]!r}",
                request=r.request,
            ) from exc

    web = data.get("web") or {}
    items = web.get("results") or []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items[:count]):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        snippet = (item.get("description") or item.get("extra_snippets") or "")
        if isinstance(snippet, list):
            snippet = " ".join(str(s) for s in snippet)
        else:
            snippet = str(snippet)
        if not title and not url:
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet.strip(),
                "score": 1.0 - i * 0.05,
            }
        )
    return out


def _brave_search_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    s = lang.strip().lower().replace("_", "-")
    if s in {"zh", "zh-cn", "zh-hans", "chinese"}:
        return "zh-hans"
    if s in {"zh-tw", "zh-hk", "zh-hant"}:
        return "zh-hant"
    if len(s) == 2:
        return s
    if s.startswith("zh-hans") or s.startswith("zh-hant"):
        return s
    if len(s) == 5 and s[2] == "-":
        return s[:2]
    return None


async def _provider_tavily(api_key: str, query: str, top_k: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": top_k},
        )
        r.raise_for_status()
        data = r.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "score": item.get("score", 0.0),
        }
        for item in data.get("results", [])
    ]


async def _provider_serper(api_key: str, query: str, top_k: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "content-type": "application/json"},
            json={"q": query, "num": top_k},
        )
        r.raise_for_status()
        data = r.json()
    organic = data.get("organic", [])[:top_k]
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            "score": 1.0 - i * 0.05,
        }
        for i, item in enumerate(organic)
    ]


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return url
