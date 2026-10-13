"""Bounded streaming shared by platform media downloads."""

import time


def copy_response_with_limit(response, output, max_bytes: int, label: str, *,
                             deadline: float | None = None) -> int:
    """Check size, completeness and an optional monotonic deadline.

    The transport must also set a socket timeout: a deadline is checked between
    reads and cannot interrupt a blocked socket read.
    """
    content_length = response.headers.get("Content-Length")
    declared_size = None
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label}响应长度无效") from exc
        if declared_size < 0:
            raise RuntimeError(f"{label}响应长度无效")
        if declared_size > max_bytes:
            raise RuntimeError(f"{label}文件过大，超过 {max_bytes / 1024 / 1024:.0f} MB 安全上限")

    def check_deadline() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise RuntimeError(f"{label}下载超过时间上限")

    total = 0
    while True:
        check_deadline()
        chunk = response.read(128 * 1024)
        check_deadline()
        if not chunk:
            if declared_size is not None and total != declared_size:
                raise RuntimeError(f"{label}下载不完整或响应长度不符，请重试备用地址")
            if total == 0:
                raise RuntimeError(f"{label}下载为空")
            return total
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"{label}下载超过 {max_bytes / 1024 / 1024:.0f} MB 安全上限，已停止")
        if declared_size is not None and total > declared_size:
            raise RuntimeError(f"{label}响应长度不符")
        output.write(chunk)
