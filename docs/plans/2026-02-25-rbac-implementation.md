# RBAC + ABAC Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace binary `isAdmin` with granular RBAC+ABAC: roles, permissions, policies, audit logging across backend and frontend.

**Architecture:** Flat permission model (`section:action` strings) with ABAC policy overlay. Roles group permissions. Hierarchy via `level` field. JWT carries permissions for frontend hints. Backend always re-validates.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2.x async / Alembic (backend), React 19 / TypeScript / Zustand / Tailwind / Radix UI (frontend)

**Key Paths:**
- Backend: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/`
- Frontend: `/Users/ea/Desktop/DEV/bedolaga-cabinet/`

---

## Phase 1: Database Models & Migration (Backend)

### Task 1: Create SQLAlchemy models for RBAC tables

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/database/models.py` (append after existing models)

**Step 1: Add AdminRole model**

Add after the last model class in `models.py`:

```python
class AdminRole(Base):
    __tablename__ = 'admin_roles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    level = Column(Integer, default=0, nullable=False)
    permissions = Column(JSONB, default=list, nullable=False)
    color = Column(String(7), nullable=True)
    icon = Column(String(50), nullable=True)
    is_system = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(BigInteger, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    creator = relationship('User', foreign_keys=[created_by])
    user_roles = relationship('UserRole', back_populates='role', lazy='selectin')
```

**Step 2: Add UserRole model**

```python
class UserRole(Base):
    __tablename__ = 'user_roles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id = Column(Integer, ForeignKey('admin_roles.id', ondelete='CASCADE'), nullable=False)
    assigned_by = Column(BigInteger, ForeignKey('users.id'), nullable=True)
    assigned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint('user_id', 'role_id', name='uq_user_role'),)

    user = relationship('User', foreign_keys=[user_id], back_populates='admin_roles_rel')
    role = relationship('AdminRole', back_populates='user_roles')
    assigner = relationship('User', foreign_keys=[assigned_by])
```

**Step 3: Add AccessPolicy model**

```python
class AccessPolicy(Base):
    __tablename__ = 'access_policies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    role_id = Column(Integer, ForeignKey('admin_roles.id', ondelete='CASCADE'), nullable=True)
    priority = Column(Integer, default=0, nullable=False)
    effect = Column(String(10), nullable=False)  # "allow" or "deny"
    conditions = Column(JSONB, default=dict, nullable=False)
    resource = Column(String(100), nullable=False)
    actions = Column(JSONB, default=list, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(BigInteger, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    role = relationship('AdminRole')
    creator = relationship('User', foreign_keys=[created_by])
```

**Step 4: Add AdminAuditLog model**

```python
class AdminAuditLog(Base):
    __tablename__ = 'admin_audit_log'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(100), nullable=True)
    details = Column(JSONB, nullable=True)
    ip_address = Column(String(45), nullable=True)  # Use String for INET compat
    user_agent = Column(Text, nullable=True)
    status = Column(String(20), nullable=False)
    request_method = Column(String(10), nullable=True)
    request_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship('User')

    __table_args__ = (
        Index('ix_audit_log_user_created', 'user_id', 'created_at'),
        Index('ix_audit_log_resource', 'resource_type', 'resource_id'),
        Index('ix_audit_log_created', 'created_at'),
    )
```

**Step 5: Add relationship to User model**

Find the `User` class in `models.py` and add:

```python
admin_roles_rel = relationship('UserRole', back_populates='user', lazy='selectin')
```

**Step 6: Commit**

```bash
git add app/database/models.py
git commit -m "feat: add RBAC database models (AdminRole, UserRole, AccessPolicy, AdminAuditLog)"
```

---

### Task 2: Create Alembic migration

**Files:**
- Create: `migrations/alembic/versions/xxxx_add_rbac_tables.py` (generated by alembic)

**Step 1: Generate migration**

```bash
cd /Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot
make migration m="add_rbac_tables"
```

**Step 2: Edit migration to add preset roles seed data**

In the generated migration file, add to the `upgrade()` function after table creation:

```python
# Seed preset roles
op.execute("""
    INSERT INTO admin_roles (name, description, level, permissions, color, icon, is_system, is_active, created_at, updated_at)
    VALUES
    ('Superadmin', 'Full system access', 999, '["*:*"]'::jsonb, '#EF4444', 'shield', true, true, NOW(), NOW()),
    ('Admin', 'Administrative access', 100, '["users:*", "tickets:*", "stats:*", "broadcasts:*", "tariffs:*", "promocodes:*", "promo_groups:*", "promo_offers:*", "campaigns:*", "partners:*", "withdrawals:*", "payments:*", "payment_methods:*", "servers:*", "remnawave:*", "traffic:*", "settings:*", "roles:read", "roles:create", "roles:edit", "roles:assign", "audit_log:*", "channels:*", "ban_system:*", "wheel:*", "apps:*", "email_templates:*", "pinned_messages:*", "updates:*"]'::jsonb, '#F59E0B', 'crown', true, true, NOW(), NOW()),
    ('Moderator', 'User and ticket management', 50, '["users:read", "users:edit", "users:block", "tickets:*", "ban_system:*"]'::jsonb, '#3B82F6', 'user-shield', true, true, NOW(), NOW()),
    ('Marketer', 'Marketing tools access', 30, '["campaigns:*", "broadcasts:*", "promocodes:*", "promo_offers:*", "promo_groups:*", "stats:read", "pinned_messages:*", "wheel:*"]'::jsonb, '#8B5CF6', 'megaphone', true, true, NOW(), NOW()),
    ('Support', 'Ticket support access', 20, '["tickets:read", "tickets:reply", "users:read"]'::jsonb, '#10B981', 'headset', true, true, NOW(), NOW())
    ON CONFLICT (name) DO NOTHING;
""")
```

**Step 3: Run migration**

```bash
make migrate
```

**Step 4: Commit**

```bash
git add migrations/
git commit -m "feat: alembic migration for RBAC tables with preset roles"
```

---

## Phase 2: Backend CRUD Layer

### Task 3: Create RBAC CRUD operations

**Files:**
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/database/crud/rbac.py`

**Step 1: Write RBAC CRUD**

```python
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import AccessPolicy, AdminAuditLog, AdminRole, User, UserRole

logger = structlog.get_logger()


class AdminRoleCRUD:
    @staticmethod
    async def get_all(db: AsyncSession, *, include_inactive: bool = False) -> list[AdminRole]:
        stmt = select(AdminRole).order_by(AdminRole.level.desc())
        if not include_inactive:
            stmt = stmt.where(AdminRole.is_active.is_(True))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, role_id: int) -> AdminRole | None:
        result = await db.execute(select(AdminRole).where(AdminRole.id == role_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> AdminRole | None:
        result = await db.execute(select(AdminRole).where(AdminRole.name == name))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, *, name: str, description: str | None, level: int,
                     permissions: list[str], color: str | None, icon: str | None,
                     is_system: bool = False, created_by: int | None) -> AdminRole:
        role = AdminRole(
            name=name, description=description, level=level,
            permissions=permissions, color=color, icon=icon,
            is_system=is_system, created_by=created_by,
        )
        db.add(role)
        await db.flush()
        return role

    @staticmethod
    async def update(db: AsyncSession, role_id: int, **kwargs) -> AdminRole | None:
        role = await AdminRoleCRUD.get_by_id(db, role_id)
        if not role:
            return None
        for key, value in kwargs.items():
            if hasattr(role, key):
                setattr(role, key, value)
        role.updated_at = datetime.now(UTC)
        await db.flush()
        return role

    @staticmethod
    async def delete(db: AsyncSession, role_id: int) -> bool:
        role = await AdminRoleCRUD.get_by_id(db, role_id)
        if not role or role.is_system:
            return False
        await db.execute(delete(UserRole).where(UserRole.role_id == role_id))
        await db.execute(delete(AccessPolicy).where(AccessPolicy.role_id == role_id))
        await db.delete(role)
        await db.flush()
        return True

    @staticmethod
    async def count_users(db: AsyncSession, role_id: int) -> int:
        result = await db.execute(
            select(func.count()).select_from(UserRole)
            .where(UserRole.role_id == role_id, UserRole.is_active.is_(True))
        )
        return result.scalar_one()


class UserRoleCRUD:
    @staticmethod
    async def get_user_roles(db: AsyncSession, user_id: int) -> list[UserRole]:
        result = await db.execute(
            select(UserRole)
            .options(selectinload(UserRole.role))
            .where(UserRole.user_id == user_id, UserRole.is_active.is_(True))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_user_permissions(db: AsyncSession, user_id: int) -> tuple[list[str], list[str], int]:
        """Returns (permissions, role_names, max_level)."""
        roles = await UserRoleCRUD.get_user_roles(db, user_id)
        now = datetime.now(UTC)
        permissions: set[str] = set()
        role_names: list[str] = []
        max_level = 0
        for ur in roles:
            if ur.expires_at and ur.expires_at < now:
                continue
            if ur.role and ur.role.is_active:
                permissions.update(ur.role.permissions or [])
                role_names.append(ur.role.name)
                max_level = max(max_level, ur.role.level)
        return sorted(permissions), role_names, max_level

    @staticmethod
    async def assign_role(db: AsyncSession, *, user_id: int, role_id: int,
                          assigned_by: int, expires_at: datetime | None = None) -> UserRole:
        existing = await db.execute(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
        ur = existing.scalar_one_or_none()
        if ur:
            ur.is_active = True
            ur.assigned_by = assigned_by
            ur.expires_at = expires_at
            ur.assigned_at = datetime.now(UTC)
            await db.flush()
            return ur
        ur = UserRole(
            user_id=user_id, role_id=role_id,
            assigned_by=assigned_by, expires_at=expires_at,
        )
        db.add(ur)
        await db.flush()
        return ur

    @staticmethod
    async def revoke_role(db: AsyncSession, user_role_id: int) -> bool:
        result = await db.execute(
            update(UserRole).where(UserRole.id == user_role_id).values(is_active=False)
        )
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def get_all_admins(db: AsyncSession, *, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get all users with any active role."""
        stmt = (
            select(User, func.array_agg(AdminRole.name).label('role_names'))
            .join(UserRole, UserRole.user_id == User.id)
            .join(AdminRole, AdminRole.id == UserRole.role_id)
            .where(UserRole.is_active.is_(True), AdminRole.is_active.is_(True))
            .group_by(User.id)
            .order_by(User.id)
            .limit(limit).offset(offset)
        )
        result = await db.execute(stmt)
        return [{'user': row[0], 'role_names': row[1]} for row in result.all()]

    @staticmethod
    async def get_superadmin_count(db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count()).select_from(UserRole)
            .join(AdminRole, AdminRole.id == UserRole.role_id)
            .where(UserRole.is_active.is_(True), AdminRole.level == 999)
        )
        return result.scalar_one()


class AccessPolicyCRUD:
    @staticmethod
    async def get_all(db: AsyncSession, *, role_id: int | None = None) -> list[AccessPolicy]:
        stmt = select(AccessPolicy).where(AccessPolicy.is_active.is_(True)).order_by(AccessPolicy.priority.desc())
        if role_id is not None:
            stmt = stmt.where(AccessPolicy.role_id == role_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, policy_id: int) -> AccessPolicy | None:
        result = await db.execute(select(AccessPolicy).where(AccessPolicy.id == policy_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, **kwargs) -> AccessPolicy:
        policy = AccessPolicy(**kwargs)
        db.add(policy)
        await db.flush()
        return policy

    @staticmethod
    async def update(db: AsyncSession, policy_id: int, **kwargs) -> AccessPolicy | None:
        policy = await AccessPolicyCRUD.get_by_id(db, policy_id)
        if not policy:
            return None
        for key, value in kwargs.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        await db.flush()
        return policy

    @staticmethod
    async def delete(db: AsyncSession, policy_id: int) -> bool:
        result = await db.execute(delete(AccessPolicy).where(AccessPolicy.id == policy_id))
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def get_policies_for_user(db: AsyncSession, role_ids: list[int]) -> list[AccessPolicy]:
        stmt = (
            select(AccessPolicy)
            .where(
                AccessPolicy.is_active.is_(True),
                AccessPolicy.role_id.in_(role_ids) | AccessPolicy.role_id.is_(None),
            )
            .order_by(AccessPolicy.priority.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


class AuditLogCRUD:
    @staticmethod
    async def create(db: AsyncSession, *, user_id: int, action: str,
                     resource_type: str | None = None, resource_id: str | None = None,
                     details: dict | None = None, ip_address: str | None = None,
                     user_agent: str | None = None, status: str = 'success',
                     request_method: str | None = None, request_path: str | None = None) -> AdminAuditLog:
        log = AdminAuditLog(
            user_id=user_id, action=action, resource_type=resource_type,
            resource_id=resource_id, details=details, ip_address=ip_address,
            user_agent=user_agent, status=status,
            request_method=request_method, request_path=request_path,
        )
        db.add(log)
        await db.flush()
        return log

    @staticmethod
    async def get_logs(db: AsyncSession, *, user_id: int | None = None,
                       action: str | None = None, resource_type: str | None = None,
                       status: str | None = None,
                       date_from: datetime | None = None, date_to: datetime | None = None,
                       limit: int = 50, offset: int = 0) -> tuple[list[AdminAuditLog], int]:
        stmt = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
        count_stmt = select(func.count()).select_from(AdminAuditLog)

        filters = []
        if user_id:
            filters.append(AdminAuditLog.user_id == user_id)
        if action:
            filters.append(AdminAuditLog.action.ilike(f'%{action}%'))
        if resource_type:
            filters.append(AdminAuditLog.resource_type == resource_type)
        if status:
            filters.append(AdminAuditLog.status == status)
        if date_from:
            filters.append(AdminAuditLog.created_at >= date_from)
        if date_to:
            filters.append(AdminAuditLog.created_at <= date_to)

        for f in filters:
            stmt = stmt.where(f)
            count_stmt = count_stmt.where(f)

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(stmt.limit(limit).offset(offset))
        return list(result.scalars().all()), total
```

**Step 2: Commit**

```bash
git add app/database/crud/rbac.py
git commit -m "feat: add RBAC CRUD operations (roles, user_roles, policies, audit_log)"
```

---

## Phase 3: Permission Engine (Backend)

### Task 4: Create permission registry and service

**Files:**
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/services/permission_service.py`

**Step 1: Write permission registry and evaluation engine**

```python
from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from fnmatch import fnmatch

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.rbac import AccessPolicyCRUD, AuditLogCRUD, UserRoleCRUD
from app.database.models import AccessPolicy, User

logger = structlog.get_logger()

# ── Permission Registry ──────────────────────────────────────────────
PERMISSION_REGISTRY: dict[str, list[str]] = {
    'users': ['read', 'edit', 'block', 'delete', 'sync'],
    'tickets': ['read', 'reply', 'close', 'settings'],
    'stats': ['read', 'export'],
    'broadcasts': ['read', 'create', 'edit', 'delete', 'send'],
    'tariffs': ['read', 'create', 'edit', 'delete'],
    'promocodes': ['read', 'create', 'edit', 'delete', 'stats'],
    'promo_groups': ['read', 'create', 'edit', 'delete'],
    'promo_offers': ['read', 'create', 'edit', 'send'],
    'campaigns': ['read', 'create', 'edit', 'delete', 'stats'],
    'partners': ['read', 'edit', 'approve', 'revoke', 'settings'],
    'withdrawals': ['read', 'approve', 'reject'],
    'payments': ['read', 'export'],
    'payment_methods': ['read', 'edit'],
    'servers': ['read', 'edit'],
    'remnawave': ['read', 'sync', 'manage'],
    'traffic': ['read', 'export'],
    'settings': ['read', 'edit'],
    'roles': ['read', 'create', 'edit', 'delete', 'assign'],
    'audit_log': ['read', 'export'],
    'channels': ['read', 'edit'],
    'ban_system': ['read', 'ban', 'unban'],
    'wheel': ['read', 'edit'],
    'apps': ['read', 'edit'],
    'email_templates': ['read', 'edit'],
    'pinned_messages': ['read', 'create', 'edit', 'delete'],
    'updates': ['read', 'manage'],
}


def get_all_permissions() -> list[str]:
    """Return flat list of all valid permissions."""
    result = []
    for section, actions in PERMISSION_REGISTRY.items():
        for action in actions:
            result.append(f'{section}:{action}')
    return result


def permission_matches(user_perm: str, required_perm: str) -> bool:
    """Check if user_perm grants access for required_perm. Supports wildcards."""
    if user_perm == '*:*':
        return True
    return fnmatch(required_perm, user_perm)


class PermissionService:
    @staticmethod
    async def check_permission(
        db: AsyncSession,
        user: User,
        required_permission: str,
        *,
        ip_address: str | None = None,
    ) -> tuple[bool, str]:
        """
        Check if user has the required permission.
        Returns (allowed: bool, reason: str).
        """
        # Get user permissions from roles
        permissions, role_names, max_level = await UserRoleCRUD.get_user_permissions(db, user.id)

        # Check if any permission matches
        has_base_permission = any(
            permission_matches(p, required_permission) for p in permissions
        )

        if not has_base_permission:
            return False, f'Permission {required_permission} not granted by roles: {role_names}'

        # Get user's role IDs for policy lookup
        user_roles = await UserRoleCRUD.get_user_roles(db, user.id)
        role_ids = [ur.role_id for ur in user_roles if ur.role and ur.role.is_active]

        # Evaluate ABAC policies
        policies = await AccessPolicyCRUD.get_policies_for_user(db, role_ids)
        if not policies:
            return True, 'Granted by role permissions'

        section = required_permission.split(':')[0] if ':' in required_permission else required_permission
        action = required_permission.split(':')[1] if ':' in required_permission else '*'

        for policy in sorted(policies, key=lambda p: p.priority, reverse=True):
            if not _policy_matches_resource(policy, section, action):
                continue

            conditions_met = _evaluate_conditions(policy.conditions, ip_address=ip_address)

            if policy.effect == 'deny' and conditions_met:
                return False, f'Denied by policy: {policy.name}'
            if policy.effect == 'allow' and not conditions_met:
                return False, f'Conditions not met for policy: {policy.name}'

        return True, 'Granted'

    @staticmethod
    async def get_user_permissions(db: AsyncSession, user_id: int) -> dict:
        """Get permissions summary for a user (used by /me/permissions endpoint)."""
        permissions, role_names, max_level = await UserRoleCRUD.get_user_permissions(db, user_id)
        return {
            'permissions': permissions,
            'roles': role_names,
            'role_level': max_level,
        }

    @staticmethod
    async def log_action(
        db: AsyncSession,
        *,
        user_id: int,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        status: str = 'success',
        request_method: str | None = None,
        request_path: str | None = None,
    ) -> None:
        """Write audit log entry."""
        await AuditLogCRUD.create(
            db,
            user_id=user_id, action=action,
            resource_type=resource_type, resource_id=resource_id,
            details=details, ip_address=ip_address,
            user_agent=user_agent, status=status,
            request_method=request_method, request_path=request_path,
        )


def _policy_matches_resource(policy: AccessPolicy, section: str, action: str) -> bool:
    """Check if policy applies to the given resource and action."""
    if policy.resource != '*' and policy.resource != section:
        return False
    policy_actions = policy.actions or []
    if '*' in policy_actions:
        return True
    return action in policy_actions


def _evaluate_conditions(conditions: dict | None, *, ip_address: str | None = None) -> bool:
    """Evaluate ABAC conditions. Returns True if all conditions are met."""
    if not conditions:
        return True

    now = datetime.now(UTC)

    # Time range check
    if 'time_range' in conditions:
        tr = conditions['time_range']
        start_h, start_m = map(int, tr['start'].split(':'))
        end_h, end_m = map(int, tr['end'].split(':'))
        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        if start_minutes <= end_minutes:
            if not (start_minutes <= current_minutes <= end_minutes):
                return False
        else:  # overnight range
            if end_minutes < current_minutes < start_minutes:
                return False

    # IP whitelist check
    if 'ip_whitelist' in conditions and ip_address:
        try:
            client_ip = ipaddress.ip_address(ip_address)
            allowed = False
            for network_str in conditions['ip_whitelist']:
                if '/' in network_str:
                    if client_ip in ipaddress.ip_network(network_str, strict=False):
                        allowed = True
                        break
                elif str(client_ip) == network_str:
                    allowed = True
                    break
            if not allowed:
                return False
        except ValueError:
            pass

    return True
```

**Step 2: Commit**

```bash
git add app/services/permission_service.py
git commit -m "feat: add PermissionService with ABAC policy engine and permission registry"
```

---

### Task 5: Create require_permission FastAPI dependency

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/dependencies.py`

**Step 1: Add require_permission dependency function**

Add after the existing `get_current_admin_user` function (after line ~250):

```python
from app.services.permission_service import PermissionService


def require_permission(*permissions: str):
    """
    FastAPI dependency factory that checks user has required permissions.
    Usage: Depends(require_permission("users:read"))
    """
    async def dependency(
        request: Request,
        user: User = Depends(get_current_cabinet_user),
        db: AsyncSession = Depends(get_cabinet_db),
    ) -> User:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get('user-agent', '')

        for perm in permissions:
            allowed, reason = await PermissionService.check_permission(
                db, user, perm, ip_address=ip_address,
            )
            if not allowed:
                # Log denied action
                await PermissionService.log_action(
                    db,
                    user_id=user.id,
                    action=perm,
                    status='denied',
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_method=request.method,
                    request_path=str(request.url.path),
                    details={'reason': reason},
                )
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f'Permission denied: {reason}',
                )

        # Log successful access
        action = permissions[0] if permissions else 'unknown'
        await PermissionService.log_action(
            db,
            user_id=user.id,
            action=action,
            status='success',
            ip_address=ip_address,
            user_agent=user_agent,
            request_method=request.method,
            request_path=str(request.url.path),
        )
        await db.commit()
        return user

    return dependency
```

**Step 2: Add Request import**

At the top of dependencies.py, add:

```python
from fastapi import Request
```

**Step 3: Update get_current_admin_user for backward compatibility**

Replace the existing `get_current_admin_user` function body to also check RBAC:

```python
async def get_current_admin_user(
    request: Request,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> User:
    """
    Get current authenticated admin user.
    Checks both legacy ADMIN_IDS config AND RBAC roles.
    """
    # Legacy check: config-based admin
    is_legacy_admin = settings.is_admin(
        telegram_id=user.telegram_id,
        email=user.email if user.email_verified else None,
    )

    if is_legacy_admin:
        return user

    # RBAC check: user has any active role
    from app.database.crud.rbac import UserRoleCRUD
    permissions, role_names, max_level = await UserRoleCRUD.get_user_permissions(db, user.id)
    if max_level > 0:
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail='Admin access required',
    )
```

**Step 4: Commit**

```bash
git add app/cabinet/dependencies.py
git commit -m "feat: add require_permission dependency and update get_current_admin_user for RBAC"
```

---

### Task 6: Enhance JWT with permissions

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/auth/jwt_handler.py`

**Step 1: Update create_access_token signature**

Change the function to accept optional permissions data:

```python
def create_access_token(
    user_id: int,
    telegram_id: int | None = None,
    *,
    permissions: list[str] | None = None,
    roles: list[str] | None = None,
    role_level: int = 0,
) -> str:
```

Add to payload before `jwt.encode()`:

```python
if permissions is not None:
    payload['permissions'] = permissions
if roles is not None:
    payload['roles'] = roles
if role_level > 0:
    payload['role_level'] = role_level
```

**Step 2: Update all callers of create_access_token**

Find all callers (in `auth.py`, `oauth.py`) — they pass positional args `(user.id, user.telegram_id)`. These continue to work because the new params are keyword-only.

For the auth routes that create tokens, add permission loading. In `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/auth.py`, after the user is loaded and before token creation, add:

```python
from app.database.crud.rbac import UserRoleCRUD

# Load permissions for JWT
user_permissions, user_role_names, user_role_level = await UserRoleCRUD.get_user_permissions(db, user.id)
access_token = create_access_token(
    user.id, user.telegram_id,
    permissions=user_permissions,
    roles=user_role_names,
    role_level=user_role_level,
)
```

This needs to be done in every login endpoint: `login_telegram`, `login_telegram_widget`, `login_email`, `oauth_callback`, `refresh_token`.

**Step 3: Commit**

```bash
git add app/cabinet/auth/jwt_handler.py app/cabinet/routes/auth.py app/cabinet/routes/oauth.py
git commit -m "feat: embed permissions, roles, role_level in JWT access token"
```

---

## Phase 4: Backend RBAC API Routes

### Task 7: Create RBAC management routes

**Files:**
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/admin_roles.py`

**Step 1: Write role management endpoints**

Create the full file with these endpoints:

```
GET    /admin/roles              — list all roles (require: roles:read)
POST   /admin/roles              — create role (require: roles:create)
PUT    /admin/roles/{id}         — update role (require: roles:edit)
DELETE /admin/roles/{id}         — delete role (require: roles:delete)
GET    /admin/roles/permissions  — get permission registry (require: roles:read)
GET    /admin/roles/users        — list users with roles (require: roles:read)
POST   /admin/roles/assign       — assign role (require: roles:assign)
DELETE /admin/roles/assign/{id}  — revoke role (require: roles:assign)
```

Each endpoint uses `Depends(require_permission("roles:xxx"))`. Include:
- Pydantic request/response models (inline in file)
- Level hierarchy enforcement (can't assign roles >= own level)
- Cannot delete system roles
- Cannot remove last superadmin

**Step 2: Commit**

```bash
git add app/cabinet/routes/admin_roles.py
git commit -m "feat: add admin role management API routes"
```

---

### Task 8: Create access policy management routes

**Files:**
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/admin_policies.py`

**Step 1: Write policy CRUD endpoints**

```
GET    /admin/policies           — list policies (require: roles:read)
POST   /admin/policies           — create policy (require: roles:create)
PUT    /admin/policies/{id}      — update policy (require: roles:edit)
DELETE /admin/policies/{id}      — delete policy (require: roles:delete)
```

**Step 2: Commit**

```bash
git add app/cabinet/routes/admin_policies.py
git commit -m "feat: add ABAC policy management API routes"
```

---

### Task 9: Create audit log routes

**Files:**
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/admin_audit_log.py`

**Step 1: Write audit log endpoints**

```
GET    /admin/audit-log          — list logs with filters (require: audit_log:read)
GET    /admin/audit-log/export   — CSV export (require: audit_log:export)
GET    /admin/audit-log/stats    — action stats summary (require: audit_log:read)
```

Filters: user_id, action, resource_type, status, date_from, date_to. Pagination with limit/offset.

**Step 2: Commit**

```bash
git add app/cabinet/routes/admin_audit_log.py
git commit -m "feat: add audit log API routes with filtering and export"
```

---

### Task 10: Add permissions endpoint and update is-admin

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/auth.py`

**Step 1: Add GET /cabinet/auth/me/permissions endpoint**

```python
@router.get('/me/permissions')
async def get_my_permissions(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    from app.services.permission_service import PermissionService
    return await PermissionService.get_user_permissions(db, user.id)
```

**Step 2: Update is-admin endpoint to check RBAC**

Find the existing `is-admin` endpoint and update it to also check RBAC roles (not just config ADMIN_IDS):

```python
@router.get('/me/is-admin')
async def check_is_admin(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    is_legacy = settings.is_admin(telegram_id=user.telegram_id, email=user.email if user.email_verified else None)
    if is_legacy:
        return {'is_admin': True}
    from app.database.crud.rbac import UserRoleCRUD
    _, _, max_level = await UserRoleCRUD.get_user_permissions(db, user.id)
    return {'is_admin': max_level > 0}
```

**Step 3: Commit**

```bash
git add app/cabinet/routes/auth.py
git commit -m "feat: add /me/permissions endpoint and update is-admin to check RBAC"
```

---

### Task 11: Register new routers

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/__init__.py`

**Step 1: Import and include new routers**

Add imports for the 3 new route files and include them in the cabinet router alongside existing admin routes:

```python
from .admin_roles import router as admin_roles_router
from .admin_policies import router as admin_policies_router
from .admin_audit_log import router as admin_audit_log_router

# In the router.include_router section:
router.include_router(admin_roles_router)
router.include_router(admin_policies_router)
router.include_router(admin_audit_log_router)
```

**Step 2: Commit**

```bash
git add app/cabinet/routes/__init__.py
git commit -m "feat: register RBAC route modules in cabinet router"
```

---

## Phase 5: Migrate Existing Admin Routes to require_permission

### Task 12: Migrate all 25 admin route files

**Files:**
- Modify: All `admin_*.py` files in `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/cabinet/routes/`

**Strategy:** For each file, replace `admin: User = Depends(get_current_admin_user)` with `admin: User = Depends(require_permission("section:action"))` where the section and action match the endpoint's purpose.

**Permission mapping by file:**

| File | Section | GET → | POST/PUT → | DELETE → |
|------|---------|-------|------------|----------|
| `admin_users.py` | `users` | `users:read` | `users:edit` | `users:delete` |
| `admin_tickets.py` | `tickets` | `tickets:read` | `tickets:reply` / `tickets:close` | — |
| `admin_stats.py` | `stats` | `stats:read` | — | — |
| `admin_broadcasts.py` | `broadcasts` | `broadcasts:read` | `broadcasts:create` / `broadcasts:send` | `broadcasts:delete` |
| `admin_tariffs.py` | `tariffs` | `tariffs:read` | `tariffs:create` / `tariffs:edit` | `tariffs:delete` |
| `admin_promocodes.py` | `promocodes` / `promo_groups` | `:read` | `:create` / `:edit` | `:delete` |
| `admin_promo_offers.py` | `promo_offers` | `:read` | `:create` / `:send` | — |
| `admin_campaigns.py` | `campaigns` | `:read` | `:create` / `:edit` | `:delete` |
| `admin_partners.py` | `partners` | `:read` | `:edit` / `:approve` / `:revoke` | — |
| `admin_withdrawals.py` | `withdrawals` | `:read` | `:approve` / `:reject` | — |
| `admin_payments.py` | `payments` | `payments:read` | — | — |
| `admin_payment_methods.py` | `payment_methods` | `:read` | `:edit` | — |
| `admin_servers.py` | `servers` | `:read` | `:edit` | — |
| `admin_remnawave.py` | `remnawave` | `:read` | `:sync` / `:manage` | — |
| `admin_traffic.py` | `traffic` | `:read` / `:export` | — | — |
| `admin_settings.py` | `settings` | `:read` | `:edit` | — |
| `admin_channels.py` | `channels` | `:read` | `:edit` | — |
| `admin_ban_system.py` | `ban_system` | `:read` | `:ban` / `:unban` | — |
| `admin_wheel.py` | `wheel` | `:read` | `:edit` | — |
| `admin_apps.py` | `apps` | `:read` | `:edit` | — |
| `admin_email_templates.py` | `email_templates` | `:read` | `:edit` | — |
| `admin_pinned_messages.py` | `pinned_messages` | `:read` | `:create` / `:edit` | `:delete` |
| `admin_updates.py` | `updates` | `:read` | `:manage` | — |
| `admin_button_styles.py` | `settings` | `:read` | `:edit` | — |
| `ticket_notifications.py` | `tickets` | `:read` | `:settings` | — |

**Pattern for each endpoint:**

Replace:
```python
admin: User = Depends(get_current_admin_user)
```

With:
```python
admin: User = Depends(require_permission("section:action"))
```

Also add import at top of each file:
```python
from ..dependencies import get_cabinet_db, require_permission
```

And remove the `get_current_admin_user` import if it becomes unused.

**Do this in batches of 5 files, committing after each batch:**

Batch 1: `admin_users.py`, `admin_tickets.py`, `admin_stats.py`, `admin_broadcasts.py`, `admin_tariffs.py`
Batch 2: `admin_promocodes.py`, `admin_promo_offers.py`, `admin_campaigns.py`, `admin_partners.py`, `admin_withdrawals.py`
Batch 3: `admin_payments.py`, `admin_payment_methods.py`, `admin_servers.py`, `admin_remnawave.py`, `admin_traffic.py`
Batch 4: `admin_settings.py`, `admin_channels.py`, `admin_ban_system.py`, `admin_wheel.py`, `admin_apps.py`
Batch 5: `admin_email_templates.py`, `admin_pinned_messages.py`, `admin_updates.py`, `admin_button_styles.py`, `ticket_notifications.py`

**Step N: Commit after each batch**

```bash
git commit -m "feat: migrate admin routes batch N to require_permission RBAC"
```

---

## Phase 6: Superadmin Auto-Assignment

### Task 13: Auto-assign Superadmin role on startup

**Files:**
- Modify: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/database/migrations.py` (or appropriate startup hook)
- Create: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/app/services/rbac_bootstrap_service.py`

**Step 1: Write bootstrap service**

```python
"""Ensure users from ADMIN_IDS/ADMIN_EMAILS have Superadmin role."""
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import AdminRole, User, UserRole

logger = structlog.get_logger()


async def bootstrap_superadmins(db: AsyncSession) -> None:
    """Auto-assign Superadmin role to users from ADMIN_IDS config."""
    superadmin = await db.execute(
        select(AdminRole).where(AdminRole.name == 'Superadmin')
    )
    role = superadmin.scalar_one_or_none()
    if not role:
        logger.warning('Superadmin role not found, skipping bootstrap')
        return

    admin_ids = settings.get_admin_ids()
    for tid in admin_ids:
        user = await db.execute(
            select(User).where(User.telegram_id == tid)
        )
        user_obj = user.scalar_one_or_none()
        if not user_obj:
            continue

        existing = await db.execute(
            select(UserRole).where(
                UserRole.user_id == user_obj.id,
                UserRole.role_id == role.id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        db.add(UserRole(
            user_id=user_obj.id,
            role_id=role.id,
        ))
        logger.info('Auto-assigned Superadmin role', user_id=user_obj.id, telegram_id=tid)

    await db.commit()
```

**Step 2: Call bootstrap on bot startup**

In the bot startup sequence (after migration), call `bootstrap_superadmins(db)`.

**Step 3: Commit**

```bash
git add app/services/rbac_bootstrap_service.py
git commit -m "feat: auto-assign Superadmin role to ADMIN_IDS users on startup"
```

---

## Phase 7: Frontend — Permission Store & Guards

### Task 14: Create permission store

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/store/permissions.ts`

**Step 1: Write Zustand permission store**

```typescript
import { create } from 'zustand';
import { apiClient } from '@/api/client';

interface PermissionState {
  permissions: string[];
  roles: string[];
  roleLevel: number;
  isLoaded: boolean;

  fetchPermissions: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
  hasAnyPermission: (...permissions: string[]) => boolean;
  hasAllPermissions: (...permissions: string[]) => boolean;
  canManageRole: (level: number) => boolean;
  reset: () => void;
}

function permissionMatches(userPerm: string, required: string): boolean {
  if (userPerm === '*:*') return true;
  if (userPerm === required) return true;
  // Wildcard: "users:*" matches "users:read"
  const [userSection, userAction] = userPerm.split(':');
  const [reqSection] = required.split(':');
  if (userSection === reqSection && userAction === '*') return true;
  return false;
}

export const usePermissionStore = create<PermissionState>((set, get) => ({
  permissions: [],
  roles: [],
  roleLevel: 0,
  isLoaded: false,

  fetchPermissions: async () => {
    try {
      const response = await apiClient.get<{
        permissions: string[];
        roles: string[];
        role_level: number;
      }>('/cabinet/auth/me/permissions');
      set({
        permissions: response.data.permissions,
        roles: response.data.roles,
        roleLevel: response.data.role_level,
        isLoaded: true,
      });
    } catch {
      set({ permissions: [], roles: [], roleLevel: 0, isLoaded: true });
    }
  },

  hasPermission: (permission: string) => {
    const { permissions } = get();
    return permissions.some((p) => permissionMatches(p, permission));
  },

  hasAnyPermission: (...perms: string[]) => {
    const { hasPermission } = get();
    return perms.some((p) => hasPermission(p));
  },

  hasAllPermissions: (...perms: string[]) => {
    const { hasPermission } = get();
    return perms.every((p) => hasPermission(p));
  },

  canManageRole: (level: number) => {
    return get().roleLevel > level;
  },

  reset: () => {
    set({ permissions: [], roles: [], roleLevel: 0, isLoaded: false });
  },
}));
```

**Step 2: Commit**

```bash
git add src/store/permissions.ts
git commit -m "feat: add Zustand permission store with wildcard matching"
```

---

### Task 15: Create PermissionRoute and PermissionGate components

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/components/auth/PermissionRoute.tsx`
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/components/auth/PermissionGate.tsx`

**Step 1: Write PermissionRoute**

```tsx
import { Navigate, useLocation } from 'react-router';
import { useAuthStore } from '@/store/auth';
import { usePermissionStore } from '@/store/permissions';
import { Layout } from '@/components/layout/Layout';
import { PageLoader } from '@/components/ui/PageLoader';

interface PermissionRouteProps {
  children: React.ReactNode;
  permission?: string;
  permissions?: string[];
  requireAll?: boolean;
}

export function PermissionRoute({
  children,
  permission,
  permissions,
  requireAll = false,
}: PermissionRouteProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const { hasPermission, hasAnyPermission, hasAllPermissions, isLoaded } =
    usePermissionStore();
  const location = useLocation();

  if (isLoading || (isAdmin && !isLoaded)) {
    return <PageLoader variant="light" />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  // Check specific permissions
  const requiredPerms = permissions || (permission ? [permission] : []);
  if (requiredPerms.length > 0) {
    const hasAccess = requireAll
      ? hasAllPermissions(...requiredPerms)
      : hasAnyPermission(...requiredPerms);
    if (!hasAccess) {
      return <Navigate to="/admin" replace />;
    }
  }

  return <Layout>{children}</Layout>;
}
```

**Step 2: Write PermissionGate**

```tsx
import { usePermissionStore } from '@/store/permissions';

interface PermissionGateProps {
  children: React.ReactNode;
  permission?: string;
  permissions?: string[];
  requireAll?: boolean;
  fallback?: React.ReactNode;
}

export function PermissionGate({
  children,
  permission,
  permissions,
  requireAll = false,
  fallback = null,
}: PermissionGateProps) {
  const { hasPermission, hasAnyPermission, hasAllPermissions } =
    usePermissionStore();

  const requiredPerms = permissions || (permission ? [permission] : []);
  if (requiredPerms.length === 0) return <>{children}</>;

  const hasAccess = requireAll
    ? hasAllPermissions(...requiredPerms)
    : hasAnyPermission(...requiredPerms);

  return hasAccess ? <>{children}</> : <>{fallback}</>;
}
```

**Step 3: Commit**

```bash
git add src/components/auth/
git commit -m "feat: add PermissionRoute and PermissionGate components"
```

---

### Task 16: Integrate permission loading into auth flow

**Files:**
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/store/auth.ts`

**Step 1: After `checkAdminStatus()`, fetch permissions if admin**

In each place where `checkAdminStatus()` is called (lines 165, 189, 204, 256, 271, 286, 307), add after it:

```typescript
import { usePermissionStore } from '@/store/permissions';

// After: await get().checkAdminStatus();
if (get().isAdmin) {
  await usePermissionStore.getState().fetchPermissions();
}
```

**Step 2: On logout, reset permissions**

In the `logout` action, add:

```typescript
usePermissionStore.getState().reset();
```

**Step 3: Commit**

```bash
git add src/store/auth.ts
git commit -m "feat: integrate permission loading into auth flow"
```

---

### Task 17: Create RBAC API layer

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/api/rbac.ts`

**Step 1: Write API functions**

```typescript
import { apiClient } from './client';

// Types
export interface AdminRole {
  id: number;
  name: string;
  description: string | null;
  level: number;
  permissions: string[];
  color: string | null;
  icon: string | null;
  is_system: boolean;
  is_active: boolean;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  user_count?: number;
}

export interface UserRoleAssignment {
  id: number;
  user_id: number;
  role_id: number;
  assigned_by: number | null;
  assigned_at: string;
  expires_at: string | null;
  is_active: boolean;
  role: AdminRole;
  user?: { id: number; telegram_id: number | null; username: string | null; first_name: string | null; email: string | null };
}

export interface AccessPolicy {
  id: number;
  name: string;
  description: string | null;
  role_id: number | null;
  priority: number;
  effect: 'allow' | 'deny';
  conditions: Record<string, unknown>;
  resource: string;
  actions: string[];
  is_active: boolean;
  created_by: number | null;
  created_at: string;
}

export interface AuditLogEntry {
  id: number;
  user_id: number;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  status: string;
  request_method: string | null;
  request_path: string | null;
  created_at: string;
  user?: { username: string | null; first_name: string | null };
}

export interface PermissionSection {
  section: string;
  actions: string[];
}

// API
export const rbacApi = {
  // Roles
  getRoles: () => apiClient.get<AdminRole[]>('/cabinet/admin/roles'),
  createRole: (data: Partial<AdminRole>) => apiClient.post<AdminRole>('/cabinet/admin/roles', data),
  updateRole: (id: number, data: Partial<AdminRole>) => apiClient.put<AdminRole>(`/cabinet/admin/roles/${id}`, data),
  deleteRole: (id: number) => apiClient.delete(`/cabinet/admin/roles/${id}`),

  // Permission registry
  getPermissionRegistry: () => apiClient.get<PermissionSection[]>('/cabinet/admin/roles/permissions'),

  // Role assignments
  getRoleUsers: (params?: { role_id?: number; limit?: number; offset?: number }) =>
    apiClient.get<{ items: UserRoleAssignment[]; total: number }>('/cabinet/admin/roles/users', { params }),
  assignRole: (data: { user_id: number; role_id: number; expires_at?: string }) =>
    apiClient.post<UserRoleAssignment>('/cabinet/admin/roles/assign', data),
  revokeRole: (assignmentId: number) =>
    apiClient.delete(`/cabinet/admin/roles/assign/${assignmentId}`),

  // Policies
  getPolicies: () => apiClient.get<AccessPolicy[]>('/cabinet/admin/policies'),
  createPolicy: (data: Partial<AccessPolicy>) => apiClient.post<AccessPolicy>('/cabinet/admin/policies', data),
  updatePolicy: (id: number, data: Partial<AccessPolicy>) => apiClient.put<AccessPolicy>(`/cabinet/admin/policies/${id}`, data),
  deletePolicy: (id: number) => apiClient.delete(`/cabinet/admin/policies/${id}`),

  // Audit log
  getAuditLog: (params: {
    user_id?: number;
    action?: string;
    resource_type?: string;
    status?: string;
    date_from?: string;
    date_to?: string;
    limit?: number;
    offset?: number;
  }) => apiClient.get<{ items: AuditLogEntry[]; total: number }>('/cabinet/admin/audit-log', { params }),
  exportAuditLog: (params: Record<string, string>) =>
    apiClient.get('/cabinet/admin/audit-log/export', { params, responseType: 'blob' }),
};
```

**Step 2: Commit**

```bash
git add src/api/rbac.ts
git commit -m "feat: add RBAC API layer with types for roles, policies, audit log"
```

---

## Phase 8: Frontend — Admin Pages

### Task 18: Create AdminRoles page

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/pages/AdminRoles.tsx`

**Step 1: Build role management page**

Features:
- Table of roles with columns: name (color badge), level, description, user count, system flag, actions
- Create/edit modal with: name, description, level slider, color picker, permission matrix (grouped by section with checkboxes)
- Preset templates: one-click apply Moderator/Marketer/Support permissions
- Delete button (disabled for system roles)
- Permission matrix: rows = sections, columns = actions, checkboxes for each

Use existing patterns from `AdminPromocodes.tsx` for table/modal structure, `AdminSettings.tsx` for form patterns.

i18n keys under `admin.roles.*`

**Step 2: Commit**

```bash
git add src/pages/AdminRoles.tsx
git commit -m "feat: add AdminRoles page with permission matrix editor"
```

---

### Task 19: Create AdminRoleAssign page

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/pages/AdminRoleAssign.tsx`

**Step 1: Build role assignment page**

Features:
- Search users (reuse pattern from AdminUsers)
- Assign role dropdown
- Optional expiry date picker
- Table of current assignments with revoke button
- Level hierarchy enforcement (can't assign roles >= own level)

**Step 2: Commit**

```bash
git add src/pages/AdminRoleAssign.tsx
git commit -m "feat: add AdminRoleAssign page for user role management"
```

---

### Task 20: Create AdminPolicies page

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/pages/AdminPolicies.tsx`

**Step 1: Build policy management page**

Features:
- Table of policies: name, effect (allow/deny badge), resource, actions, role, active toggle
- Create/edit form:
  - Name, description
  - Effect: allow/deny toggle
  - Resource: dropdown of sections
  - Actions: multi-select checkboxes
  - Role: optional dropdown (global if none)
  - Conditions builder:
    - Time range: start/end time inputs
    - IP whitelist: tag input
    - Rate limit: number input
  - Priority: number input

**Step 2: Commit**

```bash
git add src/pages/AdminPolicies.tsx
git commit -m "feat: add AdminPolicies page with ABAC condition builder"
```

---

### Task 21: Create AdminAuditLog page

**Files:**
- Create: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/pages/AdminAuditLog.tsx`

**Step 1: Build audit log page**

Features:
- Filterable table: user, action, resource, status, date range
- Each row shows: timestamp, user avatar/name, action badge, resource, status (success/denied/error), IP
- Expandable row detail: full details JSON, before/after diff, user agent
- CSV export button
- Pagination
- Auto-refresh toggle (poll every 30s)

Use `@tanstack/react-table` for the table (already in deps).

**Step 2: Commit**

```bash
git add src/pages/AdminAuditLog.tsx
git commit -m "feat: add AdminAuditLog page with filters, expandable details, export"
```

---

## Phase 9: Frontend — Integration

### Task 22: Update App.tsx routes

**Files:**
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/App.tsx`

**Step 1: Add lazy imports for new pages**

```tsx
const AdminRoles = lazy(() => import('./pages/AdminRoles'));
const AdminRoleAssign = lazy(() => import('./pages/AdminRoleAssign'));
const AdminPolicies = lazy(() => import('./pages/AdminPolicies'));
const AdminAuditLog = lazy(() => import('./pages/AdminAuditLog'));
```

**Step 2: Add new routes**

```tsx
<Route path="/admin/roles" element={<PermissionRoute permission="roles:read"><LazyPage><AdminRoles /></LazyPage></PermissionRoute>} />
<Route path="/admin/roles/assign" element={<PermissionRoute permission="roles:assign"><LazyPage><AdminRoleAssign /></LazyPage></PermissionRoute>} />
<Route path="/admin/policies" element={<PermissionRoute permission="roles:read"><LazyPage><AdminPolicies /></LazyPage></PermissionRoute>} />
<Route path="/admin/audit-log" element={<PermissionRoute permission="audit_log:read"><LazyPage><AdminAuditLog /></LazyPage></PermissionRoute>} />
```

**Step 3: Migrate existing admin routes to PermissionRoute**

Replace all `<AdminRoute>` wrappers with `<PermissionRoute permission="...">` using the same mapping from Task 12.

Example:
```tsx
// Before:
<Route path="/admin/users" element={<AdminRoute><LazyPage><AdminUsers /></LazyPage></AdminRoute>} />

// After:
<Route path="/admin/users" element={<PermissionRoute permission="users:read"><LazyPage><AdminUsers /></LazyPage></PermissionRoute>} />
```

**Step 4: Commit**

```bash
git add src/App.tsx
git commit -m "feat: migrate all admin routes to PermissionRoute with granular permissions"
```

---

### Task 23: Update AdminPanel navigation

**Files:**
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/pages/AdminPanel.tsx`

**Step 1: Add permission field to AdminItem interface**

```typescript
interface AdminItem {
  to: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  permission: string;  // NEW
}
```

**Step 2: Add permissions to all items**

Map each item to its required permission (e.g., `/admin/users` → `users:read`).

**Step 3: Add new RBAC group**

Add a 6th group "Security" (id: `security`) with items:
- Roles → `/admin/roles` (permission: `roles:read`)
- Role Assignment → `/admin/roles/assign` (permission: `roles:assign`)
- Access Policies → `/admin/policies` (permission: `roles:read`)
- Audit Log → `/admin/audit-log` (permission: `audit_log:read`)

**Step 4: Filter items by permission**

In the rendering, filter out items the user doesn't have access to:

```tsx
const { hasPermission } = usePermissionStore();

// In GroupSection:
const visibleItems = group.items.filter(item => hasPermission(item.permission));
if (visibleItems.length === 0) return null;
```

**Step 5: Commit**

```bash
git add src/pages/AdminPanel.tsx
git commit -m "feat: filter AdminPanel navigation by user permissions, add Security group"
```

---

### Task 24: Add i18n translations

**Files:**
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/locales/ru.json`
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/locales/en.json`
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/locales/zh.json`
- Modify: `/Users/ea/Desktop/DEV/bedolaga-cabinet/src/locales/fa.json`

**Step 1: Add keys for all RBAC pages**

Key structure:
```json
{
  "admin": {
    "groups": {
      "security": "Security"
    },
    "roles": {
      "title": "Roles",
      "subtitle": "Manage admin roles and permissions",
      "create": "Create Role",
      "edit": "Edit Role",
      "name": "Role Name",
      "description": "Description",
      "level": "Access Level",
      "permissions": "Permissions",
      "system": "System",
      "userCount": "Users",
      "deleteConfirm": "Delete role '{{name}}'?",
      "sections": { ... per section names ... },
      "actions": { ... per action names ... },
      "presets": {
        "apply": "Apply Template",
        "moderator": "Moderator",
        "marketer": "Marketer",
        "support": "Support"
      }
    },
    "roleAssign": {
      "title": "Role Assignment",
      "assign": "Assign Role",
      "revoke": "Revoke",
      "expires": "Expires At",
      "noExpiry": "No expiry"
    },
    "policies": {
      "title": "Access Policies",
      "create": "Create Policy",
      "effect": { "allow": "Allow", "deny": "Deny" },
      "conditions": {
        "timeRange": "Time Range",
        "ipWhitelist": "IP Whitelist",
        "rateLimit": "Rate Limit"
      }
    },
    "auditLog": {
      "title": "Audit Log",
      "filters": "Filters",
      "export": "Export CSV",
      "status": { "success": "Success", "denied": "Denied", "error": "Error" },
      "details": "Details",
      "noLogs": "No log entries found"
    },
    "permissions": {
      "denied": "Access denied",
      "deniedMessage": "You don't have permission to access this section"
    }
  }
}
```

Repeat for all 4 locales (ru, en, zh, fa) with translated values.

**Step 2: Commit**

```bash
git add src/locales/
git commit -m "feat: add RBAC i18n translations for all 4 locales"
```

---

## Phase 10: Testing & Finalization

### Task 25: Manual integration test checklist

**Verify the following flows:**

1. Fresh start: migration creates tables, seeds 5 preset roles
2. Existing ADMIN_IDS users auto-receive Superadmin role on startup
3. Superadmin can see all admin sections
4. Create custom role "Content Manager" with `broadcasts:*`, `pinned_messages:*`
5. Assign role to a test user → they can access broadcasts but not users
6. Create deny policy: "No access after hours" for Moderator role
7. Audit log shows all admin actions with correct user/action/resource
8. Revoke role → user loses admin access immediately
9. JWT contains permissions array, frontend uses it for instant UI filtering
10. Legacy ADMIN_IDS users still work without any role assignment (backward compat)

### Task 26: Final commit and deploy

```bash
git add -A
git commit -m "feat: complete RBAC + ABAC system with roles, policies, audit log"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-2 | Database models + Alembic migration |
| 2 | 3 | CRUD layer for all 4 tables |
| 3 | 4-6 | Permission engine, FastAPI dependency, JWT |
| 4 | 7-11 | RBAC API routes + router registration |
| 5 | 12 | Migrate 25 admin route files |
| 6 | 13 | Superadmin bootstrap on startup |
| 7 | 14-17 | Frontend: store, guards, API layer |
| 8 | 18-21 | Frontend: 4 new admin pages |
| 9 | 22-24 | Frontend: route integration, AdminPanel, i18n |
| 10 | 25-26 | Testing + deploy |

**Total: 26 tasks, ~25 files modified, ~8 files created**
