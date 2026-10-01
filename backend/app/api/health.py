from fastapi import APIRouter

from app.services.python_sandbox import sandbox_ready


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, object]:
    ready = sandbox_ready()
    return {
        "status": "ok",
        "capabilities": {"python_sandbox": True},
        "sandbox": {"ready": ready},
    }
