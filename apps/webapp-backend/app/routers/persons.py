import asyncio
import csv
import io
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

from app.config import get_settings
from app.dependencies import CurrentUserDep, get_db, require_roles
from app.encryption import encrypt_text
from app.middleware.audit import write_audit_log
from app.models.user import CurrentUser
from app.responses import envelope
from app.schemas.person import PersonUpdate
from app.services.person_service import extract_single_face_encoding

router = APIRouter()
PERSON_COLUMNS = "id, event_id, full_name, phone, aadhaar_last4, face_image_id, registered_by, entry_status, entry_time, created_at, updated_at"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024


async def prepare_face(upload: UploadFile) -> tuple[bytes, bytes]:
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Face image must be JPEG or PNG")
    raw = await upload.read(MAX_IMAGE_SIZE + 1)
    if not raw or len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Face image must be between 1 byte and 10 MB")

    def normalize() -> bytes:
        output = io.BytesIO()
        with Image.open(io.BytesIO(raw)) as image:
            image.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue()

    try:
        jpeg = await asyncio.to_thread(normalize)
        encoding = await extract_single_face_encoding(jpeg)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return jpeg, encoding


def public_person(row: asyncpg.Record, face_url: str | None = None) -> dict:
    result = dict(row)
    result["aadhaar_last4"] = "****"
    if face_url is not None:
        result["face_image_url"] = face_url
    return result


@router.post("/", status_code=201)
async def create_person(
    request: Request,
    full_name: str = Form(min_length=1, max_length=255),
    phone: str = Form(min_length=3, max_length=20),
    aadhaar_last4: str = Form(pattern=r"^\d{4}$"),
    face_image: UploadFile = File(),
    user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")),
    db: asyncpg.Connection = Depends(get_db),
):
    jpeg, encoding = await prepare_face(face_image)
    object_name = await request.app.state.file_service.upload_file(
        get_settings().minio_bucket_faces, jpeg, "image/jpeg"
    )
    image_id = UUID(object_name.rsplit("/", 1)[-1].split(".", 1)[0])
    try:
        async with db.transaction():
            row = await db.fetchrow(
                f"""INSERT INTO events.registered_persons(full_name, phone, aadhaar_last4, face_encoding, face_image_id, registered_by)
                    VALUES($1, $2, $3, $4, $5, $6) RETURNING {PERSON_COLUMNS}""",
                full_name, phone, encrypt_text(aadhaar_last4), encoding, image_id, user.id,
            )
            await write_audit_log(db, request, user, "create", "events.registered_person", row["id"])
    except Exception:
        await request.app.state.file_service.delete_file(get_settings().minio_bucket_faces, object_name)
        raise
    return envelope(public_person(row))


@router.get("/")
async def list_persons(
    user: CurrentUserDep,
    db: asyncpg.Connection = Depends(get_db),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str | None = None,
):
    search_value = f"%{search}%" if search else None
    where = "($1::text IS NULL OR full_name ILIKE $1 OR phone ILIKE $1)"
    total = await db.fetchval(f"SELECT count(*) FROM events.registered_persons WHERE {where}", search_value)
    rows = await db.fetch(
        f"SELECT {PERSON_COLUMNS} FROM events.registered_persons WHERE {where} ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        search_value, page_size, (page - 1) * page_size,
    )
    return envelope({"items": [public_person(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/export")
async def export_persons(admin: CurrentUser = Depends(require_roles("admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    rows = await db.fetch(
        "SELECT id, full_name, phone, entry_status, entry_time, created_at FROM events.registered_persons ORDER BY created_at"
    )
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["id", "full_name", "phone", "entry_status", "entry_time", "created_at"])
    for row in rows:
        writer.writerow(list(row))
    return StreamingResponse(
        iter([stream.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=registered-persons.csv"},
    )


@router.get("/{person_id}")
async def get_person(person_id: UUID, user: CurrentUserDep, request: Request, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {PERSON_COLUMNS} FROM events.registered_persons WHERE id=$1", person_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    url = None
    if row["face_image_id"]:
        url = await request.app.state.file_service.get_presigned_url(
            get_settings().minio_bucket_faces, f"{row['face_image_id']}.jpg"
        )
    return envelope(public_person(row, url))


@router.patch("/{person_id}")
async def update_person(payload: PersonUpdate, person_id: UUID, request: Request, user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="At least one field is required")
    if "aadhaar_last4" in values:
        values["aadhaar_last4"] = encrypt_text(values["aadhaar_last4"])
    assignments, args = [], []
    for key, value in values.items():
        args.append(value); assignments.append(f"{key}=${len(args)}")
    args.append(person_id)
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE events.registered_persons SET {', '.join(assignments)}, updated_at=NOW() WHERE id=${len(args)} RETURNING {PERSON_COLUMNS}",
            *args,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Registered person not found")
        await write_audit_log(db, request, user, "update", "events.registered_person", person_id, {"fields": list(values)})
    return envelope(public_person(row))


@router.delete("/{person_id}")
async def delete_person(person_id: UUID, request: Request, user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    async with db.transaction():
        row = await db.fetchrow("DELETE FROM events.registered_persons WHERE id=$1 RETURNING face_image_id", person_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Registered person not found")
        await write_audit_log(db, request, user, "delete", "events.registered_person", person_id)
    if row["face_image_id"]:
        await request.app.state.file_service.delete_file(
            get_settings().minio_bucket_faces, f"{row['face_image_id']}.jpg"
        )
    return envelope({"deleted": True})


@router.post("/{person_id}/update-face")
async def update_face(person_id: UUID, request: Request, face_image: UploadFile = File(), user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    old_id = await db.fetchval("SELECT face_image_id FROM events.registered_persons WHERE id=$1", person_id)
    if old_id is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    jpeg, encoding = await prepare_face(face_image)
    object_name = await request.app.state.file_service.upload_file(get_settings().minio_bucket_faces, jpeg, "image/jpeg")
    new_id = UUID(object_name.split(".", 1)[0])
    try:
        async with db.transaction():
            row = await db.fetchrow(
                f"UPDATE events.registered_persons SET face_encoding=$1, face_image_id=$2, updated_at=NOW() WHERE id=$3 RETURNING {PERSON_COLUMNS}",
                encoding, new_id, person_id,
            )
            await write_audit_log(db, request, user, "update_face", "events.registered_person", person_id)
    except Exception:
        await request.app.state.file_service.delete_file(get_settings().minio_bucket_faces, object_name)
        raise
    if old_id:
        await request.app.state.file_service.delete_file(get_settings().minio_bucket_faces, f"{old_id}.jpg")
    return envelope(public_person(row))
