import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import webapp
from src.security import request_limits


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, chunks, headers=(), disconnected=False):
        messages = [{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1 or disconnected}
                    for i, chunk in enumerate(chunks)]
        if disconnected:
            messages.append({"type": "http.disconnect"})
        receive = AsyncMock(side_effect=messages)
        send = AsyncMock()
        delivered = []

        async def app(scope, receive, send):
            delivered.append(await receive())

        with patch.object(request_limits, "MAX_API_BODY_BYTES", 8):
            await request_limits.ApiBodyLimitMiddleware(app)(
                {"type": "http", "path": "/api/test", "headers": list(headers)}, receive, send,
            )
        return delivered, send, receive

    async def test_chunked_request_is_rejected_before_route_execution(self):
        delivered, send, _ = await self.invoke([b"1234", b"56789"])
        self.assertEqual(delivered, [])
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)

    async def test_false_content_length_cannot_bypass_limit(self):
        delivered, send, _ = await self.invoke([b"123456789"], [(b"content-length", b"1")])
        self.assertEqual(delivered, [])
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)

    async def test_declared_oversize_is_rejected_without_reading_body(self):
        delivered, send, receive = await self.invoke([], [(b"content-length", b"9" * 5000)])
        self.assertEqual(delivered, [])
        receive.assert_not_called()
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)

    async def test_invalid_or_duplicate_length_is_rejected(self):
        for headers in ([(b"content-length", b"-1")], [(b"content-length", b"bad")],
                        [(b"content-length", b"1"), (b"content-length", b"2")]):
            delivered, send, receive = await self.invoke([], headers)
            self.assertEqual(delivered, [])
            receive.assert_not_called()
            self.assertEqual(send.call_args_list[0].args[0]["status"], 400)

    async def test_body_at_limit_is_delivered_unchanged(self):
        delivered, send, _ = await self.invoke([b"1234", b"5678"])
        self.assertEqual(delivered, [{"type": "http.request", "body": b"12345678", "more_body": False}])
        send.assert_not_called()

    async def test_disconnect_does_not_execute_route_with_partial_body(self):
        delivered, send, _ = await self.invoke([b"1234"], disconnected=True)
        self.assertEqual(delivered, [])
        send.assert_not_called()


class RequestLimitIntegrationTests(unittest.TestCase):
    def test_rejection_keeps_api_security_headers(self):
        with patch.object(request_limits, "MAX_API_BODY_BYTES", 8):
            response = TestClient(webapp.app).post(
                "/api/sources", content=b"private request", headers={"X-Cyber-Request": "1"},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("private", response.text)

    def test_local_access_guard_runs_before_body_buffering(self):
        response = TestClient(webapp.app).post(
            "/api/sources", headers={"Content-Length": str(request_limits.MAX_API_BODY_BYTES + 1)},
        )
        self.assertEqual(response.status_code, 403)

    def test_reference_image_has_lower_limit(self):
        with patch.object(request_limits, "MAX_IMAGE_BODY_BYTES", 8):
            response = TestClient(webapp.app).post(
                "/api/knowledge/reference-image", content=b"123456789", headers={"X-Cyber-Request": "1"},
            )
        self.assertEqual(response.status_code, 413)
