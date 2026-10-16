"""Limit API request bodies before JSON parsing or route execution."""

from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_API_BODY_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BODY_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BODY_BYTES = 64 * 1024 * 1024
VIDEO_UPLOAD_PATH = "/api/video-lab/videos"


class ApiBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        video_upload = scope["path"] == VIDEO_UPLOAD_PATH and scope.get("method") == "POST"
        limit = (MAX_VIDEO_BODY_BYTES if video_upload else
                 MAX_IMAGE_BODY_BYTES if scope["path"] == "/api/knowledge/reference-image"
                 else MAX_API_BODY_BYTES)

        async def reject(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                await reject(400, "请求长度格式不正确")
                return
            # Compare digit strings first; don't parse arbitrarily large integers.
            length = lengths[0].lstrip(b"0") or b"0"
            bound = str(limit).encode("ascii")
            if len(length) > len(bound) or (len(length) == len(bound) and length > bound):
                await reject(413, "请求内容超过大小上限")
                return

        if video_upload:
            # Videos are written to a temporary file by the route. Keep the
            # ordinary API buffering policy without duplicating large media in RAM.
            received = 0

            async def limited_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(413, "视频超过64 MB上限")
                return message

            await self.app(scope, limited_receive, send)
            return

        # Count actual bytes as well: chunked requests and false Content-Length
        # values must not bypass the cap. Don't execute routes on a partial body.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await reject(413, "请求内容超过大小上限")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        buffered = bytes(body)
        del body
        delivered = False

        async def replay() -> dict:
            nonlocal delivered, buffered
            if not delivered:
                delivered = True
                message = {"type": "http.request", "body": buffered, "more_body": False}
                buffered = b""
                return message
            return await receive()

        await self.app(scope, replay, send)
