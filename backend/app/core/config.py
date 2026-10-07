"""
Central configuration for the marine intelligence platform.

REAL DATA SOURCES ONLY. There are no mock-mode toggles here anymore --
every source (LLM, weather, ocean) is expected to be a real, live API.
If a required key is missing, the relevant module raises immediately
rather than silently degrading (see app/core/llm.py, app/fetchers/*).
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"

    # LLM -- OpenAI-compatible chat completions, real calls only
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # Open data sources -- NO registration, NO API keys.
    # Weather + marine: Open-Meteo (open-source, CC BY 4.0)
    open_meteo_base: str = "https://api.open-meteo.com"
    open_meteo_marine_base: str = "https://marine-api.open-meteo.com"
    # Satellite SST + chlorophyll: NOAA CoastWatch ERDDAP (any ERDDAP works)
    erddap_base: str = "https://coastwatch.pfeg.noaa.gov/erddap"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
