"""S3-compatible object storage client (ADR 011). MinIO locally, swappable
for real S3 later without touching any calling code -- boto3 speaks the same
API to both."""
import hashlib

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings


def make_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{settings.minio_endpoint}",
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket(client) -> None:
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.minio_bucket)


def storage_key_for_hash(content_hash: str) -> str:
    return f"sha256/{content_hash}"


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload(client, storage_key: str, data: bytes) -> None:
    client.put_object(Bucket=settings.minio_bucket, Key=storage_key, Body=data)


def object_exists_with_hash(client, storage_key: str, expected_hash: str) -> bool:
    """ADR 013's precise UPLOADED invariant: existence alone is not enough --
    re-hash the object and compare."""
    try:
        obj = client.get_object(Bucket=settings.minio_bucket, Key=storage_key)
        data = obj["Body"].read()
    except ClientError:
        return False
    return hash_bytes(data) == expected_hash
