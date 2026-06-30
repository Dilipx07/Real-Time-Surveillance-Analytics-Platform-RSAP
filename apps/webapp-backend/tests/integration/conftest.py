import os


if os.getenv("RSAP_INTEGRATION") == "1":
    os.environ.update({
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "55432",
        "POSTGRES_DB": "rsap_test",
        "POSTGRES_USER": "rsap_test",
        "POSTGRES_PASSWORD": "integration-postgres-password",
        "REDIS_URL": "redis://:integration-redis-password@127.0.0.1:56379/0",
        "MINIO_ENDPOINT": "127.0.0.1:59000",
        "MINIO_PUBLIC_ENDPOINT": "files.example.test",
        "MINIO_ACCESS_KEY": "integration-access",
        "MINIO_SECRET_KEY": "integration-minio-secret",
        "JWT_SECRET": "integration-jwt-secret-that-is-at-least-32-characters",
        "AES_ENCRYPTION_KEY": "integration-aes-key-material",
        "LICENSE_SIGNING_SECRET": "integration-license-signing-secret",
    })
