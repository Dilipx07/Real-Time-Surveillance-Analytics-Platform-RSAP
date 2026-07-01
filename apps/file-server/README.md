# RSAP File Server

Internal FastAPI service for private MinIO object storage. Every route except
`GET /health` requires the `X-Service-Token` header.

## Object layout

| Bucket | Object key |
| --- | --- |
| `faces` | `{uuid-v4}.jpg` |
| `captures` | `{YYYY-MM-DD}/{uuid-v4}.jpg` |
| `documents` | `{category}/{uuid-v4}.{ext}` |

Original filenames are never persisted. Buckets are made private at startup,
and captures receive a 90-day expiry lifecycle rule.

## Required environment

The values shown with angle brackets are deliberately invalid placeholders.
The service rejects blank, known default, and placeholder credentials at startup.

```text
MINIO_INTERNAL_ENDPOINT=minio:9000
MINIO_INTERNAL_SECURE=false
MINIO_PUBLIC_ENDPOINT=s3.rsap.local
MINIO_PUBLIC_SECURE=true
MINIO_REGION=us-east-1
MINIO_ACCESS_KEY=<required-access-key>
MINIO_SECRET_KEY=<required-secret-key-at-least-16-characters>
MINIO_BUCKET_FACES=faces
MINIO_BUCKET_CAPTURES=captures
MINIO_BUCKET_DOCUMENTS=documents
FILE_SERVER_SERVICE_TOKEN=<at-least-16-characters>
```

Optional settings are `CAPTURE_RETENTION_DAYS` (default `90`) and
`PRESIGNED_URL_DEFAULT_EXPIRY_SECONDS` (default `3600`).
`PRESIGNED_URL_MAX_EXPIRY_SECONDS` (default `86400`) is enforced for every
generated URL, including upload responses and redirects.

## Internal and public MinIO endpoints

The production design uses two MinIO clients:

- `MINIO_INTERNAL_ENDPOINT` handles bucket and object operations over `rsap-net`.
- `MINIO_PUBLIC_ENDPOINT` signs browser-facing URLs without rewriting them.

Production Caddy routes `s3.rsap.local` directly to MinIO's S3 API on port
`9000`, preserving the signed `Host` header. Configure public DNS and a trusted
certificate for the production hostname. The bundled `.local` configuration
uses Caddy's internal CA and is intended for controlled local deployments.

The base Compose file publishes neither MinIO nor file-server ports. Use Caddy
for production access. `infra/docker-compose.dev.yml` publishes MinIO ports
`9000`/`9001` and file-server port `8002`, and signs local development URLs for
`http://localhost:9000`.

Never change a presigned URL's hostname after signing; doing so invalidates its
signature.

## API summary

- `POST /upload/face`
- `POST /upload/capture`
- `POST /upload/document/{category}`
- `GET /files/{bucket}/{file_id}`
- `GET /files/{bucket}/{file_id}/presigned`
- `GET /files/{bucket}`
- `DELETE /files/{bucket}/{file_id}`
- `POST /files/batch-delete`
- `GET /health`

Uploads must be smaller than 10 MiB. Faces and captures accept verified JPEG
content. Documents accept verified PDF, JPEG, PNG, UTF-8 text/CSV, DOCX, and
XLSX content.

## Local validation

```powershell
Set-Location D:\Open-CV\RSAP-Agent-5\apps\file-server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python -m compileall .
pytest -q

$env:RSAP_RUN_MINIO_INTEGRATION = "1"
pytest -m integration -q
Remove-Item Env:RSAP_RUN_MINIO_INTEGRATION
```

Integration tests start an isolated MinIO container on a random host port,
verify external URL retrieval and continuation-token pagination, and remove the
container and its ephemeral data afterward.
