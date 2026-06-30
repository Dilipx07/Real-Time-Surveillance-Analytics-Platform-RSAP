from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.user import CurrentUser
from app.services.rbac_service import (
    SUPER_ADMIN_INVARIANT_LOCK_KEY,
    assert_can_manage_target,
    authorize,
    protect_last_super_admin,
    serialize_super_admin_mutation,
)


def user(role: str, permissions: list[dict] | None = None) -> CurrentUser:
    return CurrentUser(
        id=uuid4(), email="user@example.com", role=role, session_id="sid",
        license_id=uuid4(), permissions=permissions or [],
    )


def test_role_boundaries_deny_staff_and_va_cross_access():
    with pytest.raises(HTTPException) as staff_denied:
        authorize(user("staff"), "sync", "write")
    assert staff_denied.value.status_code == 403
    with pytest.raises(HTTPException) as va_denied:
        authorize(user("va_user"), "persons", "read")
    assert va_denied.value.status_code == 403


def test_non_admin_requires_explicit_permission():
    with pytest.raises(HTTPException):
        authorize(user("staff"), "persons", "read")


def test_permission_ownership_and_resource_constraints_are_enforced():
    actor = user("staff", [{
        "resource": "persons", "actions": ["read"],
        "constraints": {"owner_only": True, "allowed_resource_ids": []},
    }])
    with pytest.raises(HTTPException):
        authorize(actor, "persons", "read", owner_id=uuid4())
    with pytest.raises(HTTPException):
        authorize(actor, "persons", "read", owner_id=actor.id, resource_id=uuid4())


def test_ordinary_admin_cannot_manage_admin_or_super_admin():
    admin = user("admin")
    for target_role in ("admin", "super_admin"):
        with pytest.raises(HTTPException):
            assert_can_manage_target(admin, target_role)
    assert_can_manage_target(admin, "staff")
    assert_can_manage_target(admin, "va_user")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "disable", "demote"])
async def test_final_active_super_admin_is_protected(operation):
    class DB:
        async def fetchval(self, query, *_args):
            return "SELECT EXISTS" in query

    with pytest.raises(HTTPException) as blocked:
        await protect_last_super_admin(DB(), uuid4(), "super_admin")
    assert blocked.value.status_code == 409
    assert operation in blocked.value.detail.lower() or "removed" in blocked.value.detail.lower()


@pytest.mark.asyncio
async def test_one_of_two_operational_super_admins_can_be_mutated():
    class DB:
        async def fetchval(self, query, *_args):
            return True if "SELECT EXISTS" in query else 1

    await protect_last_super_admin(DB(), uuid4(), "super_admin")


@pytest.mark.asyncio
async def test_super_admin_invariant_uses_stable_transaction_lock():
    calls = []

    class DB:
        async def execute(self, query, *args):
            calls.append((query, args))

    await serialize_super_admin_mutation(DB())
    assert calls == [
        ("SELECT pg_advisory_xact_lock($1)", (SUPER_ADMIN_INVARIANT_LOCK_KEY,))
    ]
