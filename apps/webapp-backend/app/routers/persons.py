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
from app.responses import PaginatedEnvelope, SuccessEnvelope, envelope
from app.schemas.person import PersonUpdate
from app.services.cleanup_service import enqueue_external_cleanup, process_external_cleanup_once
from app.services.person_service import extract_single_face_encoding
from app.services.rbac_service import authorize

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


@router.post("/", status_code=201, response_model=SuccessEnvelope)
async def create_person(
    request: Request,
    full_name: str = Form(min_length=1, max_length=255),
    phone: str = Form(min_length=3, max_length=20),
    aadhaar_last4: str = Form(pattern=r"^\d{4}$"),
    face_image: UploadFile = File(),
    user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")),
    db: asyncpg.Connection = Depends(get_db),
):
    authorize(user, "persons", "create", owner_id=user.id)
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
        await enqueue_external_cleanup(db, get_settings().minio_bucket_faces, object_name)
        raise
    return envelope(public_person(row))


@router.get("/", response_model=PaginatedEnvelope)
async def list_persons(
    user: CurrentUserDep,
    db: asyncpg.Connection = Depends(get_db),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str | None = None,
):
    permission = authorize(user, "persons", "read")
    search_value = f"%{search}%" if search else None
    owner_only = bool((permission or {}).get("constraints", {}).get("owner_only"))
    where = "($1::text IS NULL OR full_name ILIKE $1 OR phone ILIKE $1) AND (NOT $2::bool OR registered_by=$3)"
    total = await db.fetchval(
        f"SELECT count(*) FROM events.registered_persons WHERE {where}",
        search_value, owner_only, user.id,
    )
    rows = await db.fetch(
        f"SELECT {PERSON_COLUMNS} FROM events.registered_persons WHERE {where} ORDER BY created_at DESC LIMIT $4 OFFSET $5",
        search_value, owner_only, user.id, page_size, (page - 1) * page_size,
    )
    return envelope({"items": [public_person(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_persons(
    request: Request,
    admin: CurrentUser = Depends(require_roles("admin", "super_admin")),
):
    def render_csv(rows) -> str:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerows(rows)
        return stream.getvalue()

    async def stream_csv():
        header = [["id", "full_name", "phone", "entry_status", "entry_time", "created_at"]]
        yield await asyncio.to_thread(render_csv, header)
        last_id: UUID | None = None
        async with request.app.state.db_pool.acquire() as connection:
            while True:
                rows = await connection.fetch(
                    """SELECT id, full_name, phone, entry_status, entry_time, created_at
                       FROM events.registered_persons WHERE ($1::uuid IS NULL OR id > $1)
                       ORDER BY id LIMIT 500""",
                    last_id,
                )
                if not rows:
                    break
                yield await asyncio.to_thread(render_csv, rows)
                last_id = rows[-1]["id"]

    return StreamingResponse(
        stream_csv(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=registered-persons.csv"},
    )


@router.get("/{person_id}", response_model=SuccessEnvelope)
async def get_person(person_id: UUID, user: CurrentUserDep, request: Request, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {PERSON_COLUMNS} FROM events.registered_persons WHERE id=$1", person_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    authorize(user, "persons", "read", owner_id=row["registered_by"], resource_id=person_id)
    url = None
    if row["face_image_id"]:
        url = await request.app.state.file_service.get_presigned_url(
            get_settings().minio_bucket_faces, f"{row['face_image_id']}.jpg"
        )
    return envelope(public_person(row, url))


@router.patch("/{person_id}", response_model=SuccessEnvelope)
async def update_person(payload: PersonUpdate, person_id: UUID, request: Request, user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    person = await db.fetchrow("SELECT registered_by FROM events.registered_persons WHERE id=$1", person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    authorize(user, "persons", "update", owner_id=person["registered_by"], resource_id=person_id)
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


@router.delete("/{person_id}", response_model=SuccessEnvelope)
async def delete_person(person_id: UUID, request: Request, user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    person = await db.fetchrow("SELECT registered_by FROM events.registered_persons WHERE id=$1", person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    authorize(user, "persons", "delete", owner_id=person["registered_by"], resource_id=person_id)
    async with db.transaction():
        row = await db.fetchrow("DELETE FROM events.registered_persons WHERE id=$1 RETURNING face_image_id", person_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Registered person not found")
        if row["face_image_id"]:
            await enqueue_external_cleanup(
                db, get_settings().minio_bucket_faces, f"{row['face_image_id']}.jpg"
            )
        await write_audit_log(db, request, user, "delete", "events.registered_person", person_id)
    await process_external_cleanup_once(request.app)
    return envelope({"deleted": True})


@router.post("/{person_id}/update-face", response_model=SuccessEnvelope)
async def update_face(person_id: UUID, request: Request, face_image: UploadFile = File(), user: CurrentUser = Depends(require_roles("staff", "admin", "super_admin")), db: asyncpg.Connection = Depends(get_db)):
    person = await db.fetchrow(
        "SELECT face_image_id, registered_by FROM events.registered_persons WHERE id=$1", person_id
    )
    if person is None:
        raise HTTPException(status_code=404, detail="Registered person not found")
    old_id = person["face_image_id"]
    authorize(user, "persons", "update", owner_id=person["registered_by"], resource_id=person_id)
    jpeg, encoding = await prepare_face(face_image)
    object_name = await request.app.state.file_service.upload_file(get_settings().minio_bucket_faces, jpeg, "image/jpeg")
    new_id = UUID(object_name.split(".", 1)[0])
    try:
        async with db.transaction():
            row = await db.fetchrow(
                f"UPDATE events.registered_persons SET face_encoding=$1, face_image_id=$2, updated_at=NOW() WHERE id=$3 RETURNING {PERSON_COLUMNS}",
                encoding, new_id, person_id,
            )
            if old_id:
                await enqueue_external_cleanup(
                    db, get_settings().minio_bucket_faces, f"{old_id}.jpg"
                )
            await write_audit_log(db, request, user, "update_face", "events.registered_person", person_id)
    except Exception:
        await enqueue_external_cleanup(db, get_settings().minio_bucket_faces, object_name)
        raise
    if old_id:
        await process_external_cleanup_once(request.app)
    return envelope(public_person(row))
