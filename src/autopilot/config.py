"""Runtime configuration.

Configuration is environment-driven, validated at startup, and fails closed
when a live provider is requested without an explicit opt-in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path


class ConfigError(ValueError):
    """Raised when the runtime configuration is unsafe or inconsistent."""


class Settings:
    """Validated settings for one Autopilot process."""

    db_path: Path
    webhook_secret: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    max_body_bytes: int = 1_048_576
    provider: str = "fake"
    allow_live_provider: bool = False
    openai_base_url: str = ""
    openai_api_key: str = ""
    model: str = "gpt-4o-mini"
    model_timeout_seconds: float = 20.0
    daily_request_limit: int = 200
    daily_cost_limit_usd: float = 0.0
    monthly_cost_limit_usd: float = 0.0
    proposal_ttl_minutes: int = 60
    retention_days: int = 14
    allow_remote: bool = False
    admin_token: str = ""
    trust_proxy: bool = False
    cache_model_outputs: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        base = Path(source.get("AUTOPILOT_HOME", ".")).resolve()
        db_path = Path(source.get("AUTOPILOT_DB", str(base / "var" / "autopilot.db")))
        settings = cls(
            db_path=db_path,
            webhook_secret=source.get("AUTOPILOT_WEBHOOK_SECRET", ""),
            host=source.get("AUTOPILOT_HOST", "127.0.0.1"),
            port=_int(source.get("AUTOPILOT_PORT"), 8000),
            max_body_bytes=_int(source.get("AUTOPILOT_MAX_BODY_BYTES"), 1_048_576),
            provider=source.get("AUTOPILOT_PROVIDER", "fake").strip().lower(),
            allow_live_provider=_bool(source.get("AUTOPILOT_ALLOW_LIVE_PROVIDER")),
            openai_base_url=source.get("AUTOPILOT_OPENAI_BASE_URL", "").rstrip("/"),
            openai_api_key=source.get("AUTOPILOT_OPENAI_API_KEY", ""),
            model=source.get("AUTOPILOT_MODEL", "gpt-4o-mini"),
            model_timeout_seconds=_float(source.get("AUTOPILOT_MODEL_TIMEOUT_SECONDS"), 20.0),
            daily_request_limit=_int(source.get("AUTOPILOT_DAILY_REQUEST_LIMIT"), 200),
            daily_cost_limit_usd=_float(source.get("AUTOPILOT_DAILY_COST_LIMIT_USD"), 0.0),
            monthly_cost_limit_usd=_float(source.get("AUTOPILOT_MONTHLY_COST_LIMIT_USD"), 0.0),
            proposal_ttl_minutes=_int(source.get("AUTOPILOT_PROPOSAL_TTL_MINUTES"), 60),
            retention_days=_int(source.get("AUTOPILOT_RETENTION_DAYS"), 14),
            allow_remote=_bool(source.get("AUTOPILOT_ALLOW_REMOTE")),
            admin_token=source.get("AUTOPILOT_ADMIN_TOKEN", ""),
            trust_proxy=_bool(source.get("AUTOPILOT_TRUST_PROXY")),
            cache_model_outputs=_bool(source.get("AUTOPILOT_CACHE_MODEL_OUTPUTS"), True),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.provider not in {"fake", "openai_compatible"}:
            raise ConfigError(f"unsupported provider: {self.provider}")
        if self.provider != "fake":
            if not self.allow_live_provider:
                raise ConfigError(
                    "live providers require AUTOPILOT_ALLOW_LIVE_PROVIDER=1; "
                    + "the default $0 path uses the deterministic fake provider"
                )
            if not self.openai_base_url or not self.openai_api_key:
                raise ConfigError("live provider requires base URL and API key")
            if self.daily_cost_limit_usd <= 0 or self.monthly_cost_limit_usd <= 0:
                raise ConfigError("live provider requires positive daily and monthly cost caps")
        if self.max_body_bytes < 1024:
            raise ConfigError("max body size is too small")
        if self.max_body_bytes > 8 * 1024 * 1024:
            raise ConfigError("max body size exceeds the 8 MiB safety ceiling")
        if self.daily_request_limit < 1:
            raise ConfigError("daily request limit must be positive")
        if self.proposal_ttl_minutes < 1 or self.proposal_ttl_minutes > 7 * 24 * 60:
            raise ConfigError("proposal TTL must be between 1 minute and 7 days")
        if self.retention_days < 1 or self.retention_days > 365:
            raise ConfigError("retention must be between 1 and 365 days")
        if self.allow_remote and not self.admin_token:
            raise ConfigError("remote binding requires AUTOPILOT_ADMIN_TOKEN")

    def require_webhook_secret(self) -> str:
        if len(self.webhook_secret) < 16:
            raise ConfigError("AUTOPILOT_WEBHOOK_SECRET must be at least 16 characters")
        return self.webhook_secret

    def with_db(self, db_path: Path) -> Settings:
        return replace(self, db_path=db_path)
