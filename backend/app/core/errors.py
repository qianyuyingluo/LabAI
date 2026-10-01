from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
MODEL_CAPABILITY_MISMATCH = "MODEL_CAPABILITY_MISMATCH"
MODEL_TOOL_CALLING_UNSUPPORTED = "MODEL_TOOL_CALLING_UNSUPPORTED"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
IMAGE_REQUIRED_VISION_MODEL = "IMAGE_REQUIRED_VISION_MODEL"
PROVIDER_STREAM_ERROR = "PROVIDER_STREAM_ERROR"
PROVIDER_ERROR = "PROVIDER_ERROR"
DATABASE_ERROR = "DATABASE_ERROR"
CHAT_RUNNING = "CHAT_RUNNING"


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            payload["details"] = self.details
        return payload


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
