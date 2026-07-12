"""Cliente de storage S3-compatível (MinIO local ou Cloudflare R2).

A mesma interface serve para os dois — só muda o endpoint/credenciais no .env.
Gera URLs pré-assinadas para o frontend baixar/preview sem expor as chaves.
"""
from __future__ import annotations

import boto3
from botocore.client import Config

from app.config import get_settings

_client = None


def get_s3():
    """Cliente boto3 singleton apontando para MinIO ou R2."""
    global _client
    if _client is None:
        s = get_settings()
        _client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4"),
        )
    return _client


def ensure_bucket() -> None:
    """Cria o bucket se não existir. Idempotente; chamar no startup."""
    s = get_settings()
    client = get_s3()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if s.s3_bucket not in existing:
        client.create_bucket(Bucket=s.s3_bucket)


def upload_file(local_path: str, storage_key: str, content_type: str) -> None:
    """Sobe um arquivo do disco para o bucket."""
    s = get_settings()
    get_s3().upload_file(
        local_path,
        s.s3_bucket,
        storage_key,
        ExtraArgs={"ContentType": content_type},
    )


def upload_bytes(data: bytes, storage_key: str, content_type: str) -> None:
    """Sobe um payload em memória (ex.: upload de imagem vindo da API)."""
    s = get_settings()
    get_s3().put_object(
        Bucket=s.s3_bucket,
        Key=storage_key,
        Body=data,
        ContentType=content_type,
    )


def download_bytes(storage_key: str) -> bytes:
    """Baixa um objeto inteiro para memória (imagens iniciais são pequenas)."""
    s = get_settings()
    obj = get_s3().get_object(Bucket=s.s3_bucket, Key=storage_key)
    return obj["Body"].read()


def presigned_url(storage_key: str) -> str:
    """Gera URL temporária de download para o frontend."""
    s = get_settings()
    return get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": s.s3_bucket, "Key": storage_key},
        ExpiresIn=s.s3_public_url_ttl,
    )
