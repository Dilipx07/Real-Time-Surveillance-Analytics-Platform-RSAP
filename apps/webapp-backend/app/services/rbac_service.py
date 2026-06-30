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


async def protect_last_super_admin(db, target_id: UUID, target_role: str) -> None:
    if target_role != "super_admin":
        return
    remaining = await db.fetchval(
        """SELECT count(*) FROM auth.users
           WHERE role='super_admin' AND is_active=true AND is_deleted=false AND id<>$1""",
        target_id,
    )
    if remaining == 0:
        raise HTTPException(status_code=409, detail="The final active super-admin cannot be removed")
