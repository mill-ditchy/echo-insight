"""Shared error categories and text-response validation for AI requests."""

import re

from requests.exceptions import ConnectionError as RequestConnectionError, Timeout as RequestTimeout, SSLError


class AIRequestError(RuntimeError):
    """Display-safe diagnostics; retry decisions use fields instead of wording."""

    def __init__(self, message, *, category, status_code=None, parameter=None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.parameter = parameter

    @property
    def retryable(self):
        return self.category in {"timeout", "connection", "server", "rate_limit"}


def _causes(error):
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        yield error
        error = error.__cause__


def is_transient_error(error, *, retry_rate_limits=True):
    """Typed errors are authoritative; legacy platform errors retain their policy."""
    causes = list(_causes(error))
    typed = next((cause for cause in causes if isinstance(cause, AIRequestError)), None)
    if typed is not None:
        return typed.retryable and (retry_rate_limits or typed.category != "rate_limit")

    # Compatibility for existing platform exceptions and older integrations.
    # Keep string interpretation here, rather than repeating it in each stage.
    message = "；".join(str(cause) for cause in causes).casefold()
    statuses = [int(code) for code in re.findall(r"\bhttp\s*(\d{3})\b", message)]
    if any(code not in {408, 429} and code < 500 for code in statuses):
        return False
    if not retry_rate_limits and 429 in statuses:
        return False
    if any(isinstance(cause, (SSLError, ValueError)) for cause in causes):
        return False
    if any(marker in message for marker in (
        "api key", "api_key", "凭证", "余额", "额度不足", "模型不可用",
        "model_not_found", "credits", "安全验证", "请先登录", "证书", "certificate",
    )):
        return False
    return (
        any(code in {408, 429} or 500 <= code <= 599 for code in statuses)
        or any(isinstance(cause, (TimeoutError, ConnectionError, RequestTimeout, RequestConnectionError)) for cause in causes)
        or any(marker in message for marker in ("timed out", "timeout", "连接ai接口失败", "连接中断", "连接被重置"))
    )


def is_unsupported_parameter(error, parameter):
    causes = list(_causes(error))
    typed = next((cause for cause in causes if isinstance(cause, AIRequestError)), None)
    if typed is not None:
        return typed.status_code == 400 and typed.parameter == parameter
    message = "；".join(str(cause) for cause in causes).casefold()
    return bool(re.search(r"\bhttp\s*400\b", message)) and parameter.casefold() in message


def http_error(status_code, message, *, error_type="", error_code="", parameter=None):
    """Classify once at the HTTP boundary, retaining the existing display format."""
    markers = (str(error_type) + " " + str(error_code)).casefold()
    exhausted = status_code == 429 and any(marker in message.casefold() for marker in (
        "余额", "额度不足", "insufficient credit", "credits exhausted",
    ))
    if status_code == 402 or exhausted or any(marker in markers for marker in ("creditserror", "insufficient_quota", "insufficient_balance")):
        category = "credits"
    elif any(marker in markers for marker in ("model_not_found", "invalid_api_key", "authentication_error")) or any(
        marker in message.casefold() for marker in ("model_not_found", "model is unavailable", "模型不可用")
    ):
        category = "configuration"
    elif status_code == 408:
        category = "timeout"
    elif status_code == 429:
        category = "rate_limit"
    elif 500 <= status_code <= 599:
        category = "server"
    else:
        category = "configuration"
    return AIRequestError(f"AI API错误 HTTP {status_code}: {message}", category=category,
                          status_code=status_code, parameter=parameter)


def transport_error(error, api_key):
    message = str(error).replace(api_key, "[已隐藏密钥]")[:500]
    certificate_failure = isinstance(error, SSLError) and any(marker in message.casefold() for marker in (
        "certificate", "cert_verify", "hostname", "证书",
    ))
    category = "certificate" if certificate_failure else "timeout" if isinstance(error, RequestTimeout) else "connection"
    return AIRequestError(f"连接AI接口失败: {message}", category=category)


def response_text(response, *, label="摘要"):
    """Accept nonempty text; missing finish_reason remains gateway-compatible."""
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIRequestError("AI 接口没有返回 Chat Completions 生成结果，请检查接口 URL", category="invalid_response")
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish is not None and not isinstance(finish, str):
        raise AIRequestError("AI 接口返回了无效的结束状态", category="invalid_response")
    if finish in {"length", "content_filter"}:
        raise AIRequestError("模型输出被截断或未完整返回，本次结果未保存，请稍后重试", category="incomplete_response")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise AIRequestError(f"AI 接口返回了空{label}或非文本内容", category="invalid_response")
    return content.strip()
