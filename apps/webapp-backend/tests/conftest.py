import os


TEST_ENV = {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_DB": "rsap_test",
    "POSTGRES_USER": "rsap_test",
    "POSTGRES_PASSWORD": "test-password",
    "REDIS_URL": "redis://:test-password@localhost:6379/15",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "test-access",
    "MINIO_SECRET_KEY": "test-secret",
    "JWT_SECRET": "test-jwt-secret-that-is-at-least-32-characters",
    "AES_ENCRYPTION_KEY": "test-aes-key-material-that-is-not-production",
    "LICENSE_SIGNING_SECRET": "test-license-signing-key",
}

for name, value in TEST_ENV.items():
    os.environ.setdefault(name, value)
