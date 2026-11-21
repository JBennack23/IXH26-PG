from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

BASE_DIR = Path(__file__).resolve().parent
"""Backblock project root directory."""
CFG_DIR = BASE_DIR / "cfg" if (BASE_DIR / "cfg").exists() else BASE_DIR.parent / "cfg"
"""Backblock configuration directory."""


class FastapiSettings(BaseModel):
    app_name: str
    version: str
    description: str


class Settings(BaseSettings):
    fastapi: FastapiSettings
    model_config = SettingsConfigDict(
        toml_file=CFG_DIR / "common.toml",
        env_nested_delimiter="__",  # Allows to use cross toml sections using "__"
        extra="ignore",  # Ignore keys missing from toml or env files
    )

    # Changes the order used to read the files:
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,  # Priority 1: direct Settings() params
            env_settings,  # Priority 2: Env Variables (override TOML)
            TomlConfigSettingsSource(settings_cls),  # Priority 3: config.toml file
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore


# We introduce the FastAPI dependency alias "SettingsDep", automatically injected by requesters
SettingsDep = Annotated[Settings, Depends(get_settings)]
