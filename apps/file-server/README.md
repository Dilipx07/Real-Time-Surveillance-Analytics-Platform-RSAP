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

```text
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=<access-key>
MINIO_SECRET_KEY=<secret-key>
MINIO_BUCKET_FACES=faces
MINIO_BUCKET_CAPTURES=captures
MINIO_BUCKET_DOCUMENTS=documents
MINIO_SECURE=false
FILE_SERVER_SERVICE_TOKEN=<at-least-16-characters>
```

Optional settings are `CAPTURE_RETENTION_DAYS` (default `90`) and
`PRESIGNED_URL_MAX_EXPIRY_SECONDS` (default `86400`).

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
```
