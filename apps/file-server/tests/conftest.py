import os


os.environ.setdefault("MINIO_INTERNAL_ENDPOINT", "minio:9000")
os.environ.setdefault("MINIO_PUBLIC_ENDPOINT", "s3.test.local")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-access-key")
os.environ.setdefault("MINIO_SECRET_KEY", "test-secret-key-value")
os.environ.setdefault("FILE_SERVER_SERVICE_TOKEN", "test-service-token-value")
