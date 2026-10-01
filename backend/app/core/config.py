from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_DIR = BACKEND_DIR / "app" / "storage"


def _default_database_url() -> str:
    db_path = DEFAULT_STORAGE_DIR / "labai.sqlite3"
    return f"sqlite:///{db_path.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    app_name: str = "LabAI Backend"
    openai_compat_api_key: str | None = None
    openai_compat_base_url: str | None = None
    openai_compat_default_model: str | None = None
    openai_compat_supports_stream: bool = True
    openai_compat_supports_vision: bool = False

    backend_storage_dir: Path = Field(default=DEFAULT_STORAGE_DIR)
    database_url: str = Field(default_factory=_default_database_url)
    max_image_upload_mb: int = 10
    max_file_upload_mb: int = 25
    max_files_skill_context_chars: int = 60000
    files_skill_preview_chars: int = 4000
    files_skill_upload_summary_chars: int = 12000
    files_skill_per_file_model_chars: int = 30000
    labai_sandbox_python: Path | None = None
    sandbox_timeout_seconds: int = Field(default=90, ge=1)
    sandbox_max_code_kb: int = Field(default=100, ge=1)
    sandbox_max_stdio_kb: int = Field(default=64, ge=1)
    sandbox_max_output_file_mb: int = Field(default=25, ge=1)
    sandbox_max_output_total_mb: int = Field(default=50, ge=1)
    sandbox_max_output_files: int = Field(default=20, ge=1)
    sandbox_max_input_mb: int = Field(default=200, ge=1)
    sandbox_max_input_files: int = Field(default=50, ge=1)
    sandbox_max_tool_calls: int = Field(default=6, ge=1)
    sandbox_max_concurrent_runs: int = Field(default=2, ge=1)
    sandbox_max_memory_mb: int = Field(default=1536, ge=64)
    sandbox_network_enabled: bool = False
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173"
    )

    @property
    def storage_dir(self) -> Path:
        return self.backend_storage_dir

    @property
    def image_upload_dir(self) -> Path:
        return self.storage_dir / "uploads" / "images"

    @property
    def file_upload_dir(self) -> Path:
        return self.storage_dir / "uploads" / "files"

    @property
    def generated_file_dir(self) -> Path:
        return self.storage_dir / "generated"

    @property
    def sandbox_runs_dir(self) -> Path:
        return self.storage_dir / "sandbox" / "runs"

    @property
    def max_image_upload_bytes(self) -> int:
        return self.max_image_upload_mb * 1024 * 1024

    @property
    def max_file_upload_bytes(self) -> int:
        return self.max_file_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
