"""Bound upload bodies before multipart parsing, including chunked requests."""
from starlette.responses import JSONResponse
from app.core.config import get_settings

class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or (scope["path"] != "/legal-documents/upload" and not (scope["path"].startswith("/cases/") and scope["path"].endswith("/documents"))):
            return await self.app(scope, receive, send)
        # Allow a small bounded multipart envelope in addition to the file limit.
        settings_provider = scope["app"].dependency_overrides.get(get_settings, get_settings)
        limit = settings_provider().max_upload_size + 65536
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data = message.get("body", b"")
            size += len(data)
            if size > limit:
                return await JSONResponse({"detail": "Corps de requÃªte trop volumineux"}, status_code=413)(scope, receive, send)
            chunks.append(data)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)
