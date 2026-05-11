"""Validator + miner settings (TOML + env, via pydantic-settings)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_toml(path: Path) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(path.read_text())
    import tomli

    return tomli.loads(path.read_text())


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------


class NetworkConfig(BaseModel):
    name: str
    netuid: int
    validator_hotkey: str
    wallet_name: str = "default"
    wallet_path: str | None = None


class PolarisConfig(BaseModel):
    base_url: str
    public_key_hex: str
    fetch_timeout_secs: float = 20.0


class HttpConfig(BaseModel):
    listen_host: str = "0.0.0.0"
    listen_port: int = 9333
    bearer_token_env: str = "CATHEDRAL_BEARER"


class WeightsConfig(BaseModel):
    interval_secs: int = 1200
    disabled: bool = False
    burn_uid: int = 204
    forced_burn_percentage: float = 98.0


class PublisherConfig(BaseModel):
    """Where the validator pulls signed eval-runs from."""

    url: str = "https://api.cathedral.computer"
    public_key_env: str = "CATHEDRAL_PUBLIC_KEY_HEX"
    pull_interval_secs: float = 30.0
    api_token_env: str | None = None


class StorageConfig(BaseModel):
    database_path: str = "data/validator.db"


class WorkerConfig(BaseModel):
    poll_interval_secs: float = 5.0
    max_concurrent_verifications: int = 4


class StallConfig(BaseModel):
    after_secs: int = 600


class ValidatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CATHEDRAL_", env_nested_delimiter="__")

    network: NetworkConfig
    polaris: PolarisConfig
    http: HttpConfig = Field(default_factory=HttpConfig)
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    publisher: PublisherConfig = Field(default_factory=PublisherConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    stall: StallConfig = Field(default_factory=StallConfig)

    @classmethod
    def from_toml(cls, path: str | Path) -> ValidatorSettings:
        data = _load_toml(Path(path))
        return cls.model_validate(data)


# --------------------------------------------------------------------------
# Miner
# --------------------------------------------------------------------------


class MinerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CATHEDRAL_MINER_", env_nested_delimiter="__")

    miner_hotkey: str
    owner_wallet: str
    validator_url: str
    validator_bearer_env: str = "CATHEDRAL_VALIDATOR_BEARER"

    @classmethod
    def from_toml(cls, path: str | Path) -> MinerSettings:
        data = _load_toml(Path(path))
        return cls.model_validate(data)
