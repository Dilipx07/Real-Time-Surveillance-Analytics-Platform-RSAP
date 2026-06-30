from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException

from app.dependencies import verify_dual_token
from app.models.user import CurrentUser

ROLE_RESOURCES: dict[str, set[str]] = {
    "super_admin": {"*"},
    "admin": {"users", "licenses", "persons", "cameras", "analytics"},
    "staff": {"persons"},
    "va_user": {"cameras", "analytics", "sync"},
}

# ASCII "RSAPSADM" encoded as one signed-bigint-safe PostgreSQL advisory key.
# Every transaction that can reduce the operational super-admin set takes this
# lock before locking its target row or counting the remaining administrators.
SUPER_ADMIN_INVARIANT_LOCK_KEY = 5932156947427050573


def matching_permission(user: CurrentUser, resource: str, action: str) -> dict | None:
    for permission in user.permissions:
        actions = permission.get("actions") or []
        if permission.get("resource") in {resource, "*"} and (action in actions or "*" in actions):
            return permission
    return None


def authorize(
    user: CurrentUser,
    resource: str,
    action: str,
    *,
    owner_id: UUID | None = None,
    resource_id: UUID | None = None,
) -> dict | None:
    allowed_resources = ROLE_RESOURCES.get(user.role, set())
    if "*" not in allowed_resources and resource not in allowed_resources:
        raise HTTPException(status_code=403, detail="Role cannot access this resource")
    if user.role in {"super_admin", "admin"}:
        return None
    permission = matching_permission(user, resource, action)
    if permission is None:
        raise HTTPException(status_code=403, detail="Explicit permission is required")
    constraints = permission.get("constraints") or {}
    if constraints.get("owner_only"):
        if owner_id is None or owner_id != user.id:
            raise HTTPException(status_code=403, detail="Ownership constraint denied access")
    allowed_ids = constraints.get("allowed_resource_ids")
    if allowed_ids is not None:
        if resource_id is None or str(resource_id) not in {str(value) for value in allowed_ids}:
            raise HTTPException(status_code=403, detail="Resource constraint denied access")
    return permission


def require_access(resource: str, action: str) -> Callable:
    async def checker(user: CurrentUser = Depends(verify_dual_token)) -> CurrentUser:
        authorize(user, resource, action)
        return user

    return checker


def assert_can_manage_target(actor: CurrentUser, target_role: str) -> None:
    if actor.role == "super_admin":
        return
    if actor.role != "admin" or target_role not in {"staff", "va_user"}:
        raise HTTPException(status_code=403, detail="Cannot manage a higher-privileged account")


async def serialize_super_admin_mutation(db) -> None:
    await db.execute(
        "SELECT pg_advisory_xact_lock($1)", SUPER_ADMIN_INVARIANT_LOCK_KEY
    )


async def protect_last_super_admin(
    db,
    target_id: UUID,
    target_role: str,
    *,
    excluded_license_id: UUID | None = None,
) -> None:
    """Reject a mutation that removes the final operational super-admin.

    Operational means an active, non-deleted super-admin with at least one
    active licence whose validity window contains the database clock. Callers
    hold ``SUPER_ADMIN_INVARIANT_LOCK_KEY`` and the target row before calling.
    ``excluded_license_id`` models that licence after an expiry/deactivation.
    """
    if target_role != "super_admin":
        return
    currently_operational = await db.fetchval(
        """SELECT EXISTS(
               SELECT 1 FROM auth.users u
               WHERE u.id=$1 AND u.role='super_admin'
                 AND u.is_active=true AND u.is_deleted=false
                 AND EXISTS (
                   SELECT 1 FROM rbac.licenses l
                   WHERE l.user_id=u.id AND l.is_active=true
                     AND l.valid_from <= NOW() AND l.valid_until > NOW()
                 )
             )""",
        target_id,
    )
    if not currently_operational:
        return
    if excluded_license_id is not None:
        remains_operational = await db.fetchval(
            """SELECT EXISTS(
                   SELECT 1 FROM rbac.licenses
                   WHERE user_id=$1 AND id<>$2 AND is_active=true
                     AND valid_from <= NOW() AND valid_until > NOW()
                 )""",
            target_id,
            excluded_license_id,
        )
        if remains_operational:
            return
    remaining = await db.fetchval(
        """SELECT count(*) FROM auth.users u
           WHERE u.role='super_admin' AND u.is_active=true
             AND u.is_deleted=false AND u.id<>$1
             AND EXISTS (
               SELECT 1 FROM rbac.licenses l
               WHERE l.user_id=u.id AND l.is_active=true
                 AND l.valid_from <= NOW() AND l.valid_until > NOW()
             )""",
        target_id,
    )
    if remaining == 0:
        raise HTTPException(status_code=409, detail="The final active super-admin cannot be removed")
