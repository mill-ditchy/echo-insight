"""Restrict platform downloads and keep credentials out of CDN requests."""

from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


BILIBILI_DOWNLOAD_DOMAINS = (
    "bilibili.com", "bilivideo.com", "bilivideo.cn", "hdslb.com",
)


def validate_https_url(url: str, domains: tuple[str, ...] | None = None) -> str:
    """Validate before connecting; never include a potentially secret URL in errors."""
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url) or "\\" in url:
        raise ValueError("请求地址格式不安全")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise ValueError("请求地址格式不安全") from exc
    if (
        parsed.scheme != "https" or not host
        or parsed.username is not None or parsed.password is not None
        or port not in {None, 443} or parsed.fragment
    ):
        raise ValueError("请求地址必须使用 HTTPS，且不能包含账号或非标准端口")
    if domains and not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise ValueError("拒绝连接平台以外的下载地址")
    return host


class _BilibiliRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(newurl, BILIBILI_DOWNLOAD_DOMAINS)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalize_bilibili_download_url(url: str) -> str:
    """Upgrade trusted media URLs and reject unsafe candidates before selecting a CDN."""
    parsed = urlsplit(url)
    media_domains = ("bilivideo.com", "bilivideo.cn", "hdslb.com")
    host = parsed.hostname or ""
    # Some player responses still advertise HTTP CDN URLs. Upgrade trusted media to TLS.
    if (
        parsed.scheme == "http" and parsed.port in {None, 80}
        and any(host == domain or host.endswith("." + domain) for domain in media_domains)
    ):
        authority = parsed.netloc[:-3] if parsed.port == 80 else parsed.netloc
        url = urlunsplit(("https", authority, parsed.path, parsed.query, parsed.fragment))
    validate_https_url(url, BILIBILI_DOWNLOAD_DOMAINS)
    return url


def open_bilibili_request(request: Request, timeout: float = 20):
    """Allow trusted HTTPS hosts; only the API receives the login Cookie."""
    url = normalize_bilibili_download_url(request.full_url)
    host = urlsplit(url).hostname
    headers = dict(request.header_items())
    cookie = next((value for name, value in headers.items() if name.lower() == "cookie"), "")
    headers = {
        name: value for name, value in headers.items()
        if name.lower() not in {"cookie", "authorization", "proxy-authorization"}
    }
    safe_request = Request(
        url, data=request.data, headers=headers,
        method=request.get_method(),
    )
    if cookie and host == "api.bilibili.com":
        # urllib copies ordinary headers to redirects. This header stays on the initial request.
        safe_request.add_unredirected_header("Cookie", cookie)
    return build_opener(_BilibiliRedirectHandler()).open(safe_request, timeout=timeout)
