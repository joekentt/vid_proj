"""Configuração central via variáveis de ambiente.

Tudo configurável por .env. Defaults apontam para o docker-compose local
(Redis + MinIO), então o sistema sobe sem nenhuma chave externa.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Redis (fila de jobs) ---
    redis_url: str = "redis://localhost:6379/0"
    job_queue_key: str = "video_jobs:pending"
    job_state_prefix: str = "video_jobs:state:"
    job_ttl_seconds: int = 60 * 60 * 24 * 7  # estado vive 7 dias

    # --- Storage S3-like (MinIO local por padrão) ---
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ai-video-studio"
    s3_region: str = "us-east-1"
    s3_public_url_ttl: int = 60 * 60 * 24  # URLs pré-assinadas: 24h

    # --- Provedores de geração (cloud, opcionais) ---
    # Deixe vazio para usar apenas o worker local do Colab.
    replicate_api_token: str = ""

    # Limite simples de uso por usuário (Fase 6).
    free_jobs_per_day: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
