# RBAC + ABAC Design for Bedolaga Cabinet

**Date:** 2026-02-25
**Status:** Approved
**Approach:** Hybrid RBAC + ABAC (Attribute-Based Access Control)

## Overview

Full role-based access control with attribute-based policies for the Telegram bot admin cabinet. Replaces the current binary `isAdmin` check (ADMIN_IDS env var) with granular permissions, hierarchical roles, ABAC policy engine, and comprehensive audit logging.

## Architecture Decisions

- **Hierarchy:** superadmin > admin > moderator (managed via `level` field)
- **Management:** Through cabinet UI only (no invite links)
- **Audit logging:** ALL admin API calls (GET included)
- **Role templates:** 5 presets (Superadmin, Admin, Moderator, Marketer, Support) + custom roles
- **Assignment:** Via UI, superadmin/admin assigns roles to users from the user list

## Data Model

### Tables

**`admin_roles`** — role definitions with permission groups

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| name | VARCHAR(100) UNIQUE | "Moderator", "Marketer" |
| description | TEXT | Human-readable role description |
| level | INTEGER DEFAULT 0 | Hierarchy: 0=viewer, 50=moderator, 100=admin, 999=superadmin |
| permissions | JSONB | ["users:read", "tickets:*", ...] |
| color | VARCHAR(7) | HEX badge color for UI |
| icon | VARCHAR(50) | Icon name for UI |
| is_system | BOOLEAN DEFAULT false | System role, cannot be deleted |
| is_active | BOOLEAN DEFAULT true | Soft disable |
| created_by | BIGINT FK users.id | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**`user_roles`** — M2M user-to-role assignment

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| user_id | BIGINT FK users.id | |
| role_id | INTEGER FK admin_roles.id | |
| assigned_by | BIGINT FK users.id | Who assigned |
| assigned_at | TIMESTAMPTZ | |
| expires_at | TIMESTAMPTZ NULL | Temporary role (vacation cover, etc.) |
| is_active | BOOLEAN DEFAULT true | |
| UNIQUE(user_id, role_id) | | |

**`access_policies`** — ABAC attribute-based policies

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| name | VARCHAR(200) | Policy name |
| description | TEXT | |
| role_id | INTEGER FK admin_roles.id NULL | Bound to role or global |
| priority | INTEGER DEFAULT 0 | Evaluation order |
| effect | VARCHAR(10) | "allow" or "deny" |
| conditions | JSONB | Attribute conditions (see format below) |
| resource | VARCHAR(100) | "users", "tickets", "*" |
| actions | JSONB | ["read", "edit"] or ["*"] |
| is_active | BOOLEAN DEFAULT true | |
| created_by | BIGINT FK users.id | |
| created_at | TIMESTAMPTZ | |

**Conditions JSONB format:**
```json
{
  "time_range": {"start": "09:00", "end": "18:00", "timezone": "Europe/Moscow"},
  "ip_whitelist": ["192.168.1.0/24"],
  "max_actions_per_hour": 100,
  "require_2fa": true,
  "user_attributes": {"status": ["active"]}
}
```

**`admin_audit_log`** — immutable action log (INSERT only)

| Column | Type | Description |
|--------|------|-------------|
| id | BIGSERIAL PK | |
| user_id | BIGINT FK users.id | Who acted |
| action | VARCHAR(100) | "users:edit", "roles:create" |
| resource_type | VARCHAR(50) | "user", "role", "ticket" |
| resource_id | VARCHAR(100) NULL | ID of affected resource |
| details | JSONB | Before/after diff |
| ip_address | INET | |
| user_agent | TEXT | |
| status | VARCHAR(20) | "success", "denied", "error" |
| request_method | VARCHAR(10) | GET/POST/PUT/DELETE |
| request_path | TEXT | |
| created_at | TIMESTAMPTZ | |

### Permission Registry

Format: `section:action`. Wildcard: `section:*` (all actions), `*:*` (superadmin).

| Section | Actions |
|---------|---------|
| users | read, edit, block, delete, sync |
| tickets | read, reply, close, settings |
| stats | read, export |
| broadcasts | read, create, edit, delete, send |
| tariffs | read, create, edit, delete |
| promocodes | read, create, edit, delete, stats |
| promo_groups | read, create, edit, delete |
| promo_offers | read, create, edit, send |
| campaigns | read, create, edit, delete, stats |
| partners | read, edit, approve, revoke, settings |
| withdrawals | read, approve, reject |
| payments | read, export |
| payment_methods | read, edit |
| servers | read, edit |
| remnawave | read, sync, manage |
| traffic | read, export |
| settings | read, edit |
| roles | read, create, edit, delete, assign |
| audit_log | read, export |
| channels | read, edit |
| ban_system | read, ban, unban |
| wheel | read, edit |
| apps | read, edit |
| email_templates | read, edit |
| pinned_messages | read, create, edit, delete |
| updates | read, manage |

### Preset Roles

1. **Superadmin** (level 999, system): `*:*`
2. **Admin** (level 100, system): all except `roles:delete` on system roles
3. **Moderator** (level 50): `users:read,edit,block`, `tickets:*`, `ban_system:*`
4. **Marketer** (level 30): `campaigns:*`, `broadcasts:*`, `promocodes:*`, `promo_offers:*`, `stats:read`, `pinned_messages:*`
5. **Support** (level 20): `tickets:read,reply`, `users:read`

## Backend Architecture

### Policy Engine (PermissionService)

Evaluation flow:
1. Get user roles (active, not expired)
2. Merge all permissions from roles
3. Check requested permission (exact match or wildcard)
4. If access_policies exist → evaluate conditions (time, IP, rate limit)
5. Deny policies take priority over allow
6. Return: allow/deny + reason

### FastAPI Dependency

Replace `get_current_admin_user` with parameterized `require_permission()`:

```python
def require_permission(*permissions: str):
    async def dependency(user=Depends(get_current_cabinet_user), ...):
        # Check via PermissionService
        # Log to audit_log
        # Return user if ok, else 403
    return dependency
```

### JWT Enhancement

Add to JWT payload:
```json
{
  "permissions": ["users:read", "users:edit", "tickets:*"],
  "role_level": 50,
  "roles": ["Moderator"]
}
```

### New API Endpoints

```
GET    /cabinet/admin/roles              — list roles
POST   /cabinet/admin/roles              — create role
PUT    /cabinet/admin/roles/:id          — update role
DELETE /cabinet/admin/roles/:id          — delete (non-system)
GET    /cabinet/admin/roles/users        — users with roles
POST   /cabinet/admin/roles/assign       — assign role to user
DELETE /cabinet/admin/roles/assign/:id   — revoke role
GET    /cabinet/admin/policies           — list policies
POST   /cabinet/admin/policies           — create policy
PUT    /cabinet/admin/policies/:id       — update policy
DELETE /cabinet/admin/policies/:id       — delete policy
GET    /cabinet/admin/audit-log          — log with filters
GET    /cabinet/admin/audit-log/export   — CSV/JSON export
GET    /cabinet/auth/me/permissions      — current user permissions
```

### Audit Middleware

Logs every request to `/admin/*`: user_id, action, resource, details, IP, status.

### Hierarchy Rule

Users can only manage roles with `level` lower than their own.

## Frontend Architecture

### Permission Store (Zustand)

`usePermissionStore`:
- State: permissions[], roles[], roleLevel, isLoading
- Actions: fetchPermissions(), hasPermission(), hasAnyPermission(), hasAllPermissions(), canManageRole()

### Route Guards

`PermissionRoute` component replaces `AdminRoute` with permission parameter.

### PermissionGate Component

Hides/shows UI elements based on permissions with optional fallback.

### New Pages

1. **AdminRoles** — role CRUD with permission matrix
2. **AdminRoleAssign** — assign roles to users
3. **AdminPolicies** — ABAC policy management with visual condition builder
4. **AdminAuditLog** — filterable timeline with before/after diffs

### i18n

New keys in all 4 locales (ru, en, zh, fa).

## Migration Strategy

1. Auto-create Superadmin role for users from ADMIN_IDS/ADMIN_EMAILS
2. `get_current_admin_user` stays backward-compatible via `require_permission("*:*")`
3. Gradual route migration to `require_permission()`
4. `is_admin` endpoint returns true if user has ANY role (level > 0)

## Security

- Backend always re-validates (JWT is UI hint only)
- Rate limiting on RBAC endpoints
- Deny policies win over allow
- Cannot delete last superadmin
- Cannot lower own level
- Audit log is immutable (INSERT only, no UPDATE/DELETE)
