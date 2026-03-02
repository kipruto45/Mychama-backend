# 🚨 PostgreSQL Database Reset Guide for Docker (Local Dev Only)

## ⚠️ SAFETY WARNING

> **DO NOT RUN IN PRODUCTION!** This guide is for **local development only**.
> 
> - All data will be **PERMANENTLY DELETED**
> - This includes all users, chama data, transactions, and settings
> - No rollback is possible after running these commands

---

## Understanding the Problem

The "current transaction is aborted" error occurs when:
1. A migration fails mid-way (e.g., the `vector` extension isn't available in PostgreSQL)
2. PostgreSQL automatically rolls back the entire transaction
3. Subsequent statements in the same transaction are blocked until `ROLLBACK`

**Root Cause**: The migration `0002_enable_pgvector.py` tries to create the `vector` extension, but the `postgres:16-alpine` image doesn't have pgvector installed.

---

## Option A: Full Reset (Recommended) ✅

This removes ALL volumes (database, media files, static files) and starts fresh.

```bash
# 1. Stop all containers
cd digital_chama_system
docker compose down

# 2. Remove ALL volumes (database, media, static)
docker compose down -v

# 3. Start containers fresh
docker compose up -d

# 4. Wait for PostgreSQL to be healthy
docker compose ps postgres
# Should show: (healthy)

# 5. Run ALL migrations
docker compose exec web python manage.py migrate

# 6. Create superuser
docker compose exec web python manage.py createsuperuser
# Follow prompts: email, password, etc.

# 7. Optional: Seed plans for billing
docker compose exec web python manage.py seed_plans

# 8. Optional: Seed test users
docker compose exec web python manage.py seed_users
```

### What `-v` Does:
- `-v` / `--volumes` removes **named volumes** attached to containers
- In this project, it removes:
  - `digital_chama_system_postgres_data` ← **THE DATABASE**
  - `digital_chama_system_media_data` ← Uploaded files
  - `digital_chama_system_static_data` ← Static files

---

## Option B: Drop Database Only (Keep Containers Running)

If you want to keep containers running and just reset the database:

```bash
# 1. Drop and recreate database inside PostgreSQL container
docker compose exec postgres psql -U digital_chama -c "
DROP DATABASE IF EXISTS digital_chama;
CREATE DATABASE digital_chama;
"

# 2. Run migrations
docker compose exec web python manage.py migrate

# 3. Create superuser
docker compose exec web python manage.py createsuperuser

# 4. Seed data
docker compose exec web python manage.py seed_plans
```

---

## Option C: Remove Only PostgreSQL Volume

If you want to keep media files but reset database:

```bash
# 1. Remove ONLY the postgres volume
docker volume rm digital_chama_system_postgres_data

# 2. Recreate postgres container (it will create new volume)
docker compose up -d postgres

# 3. Wait for PostgreSQL to be healthy
sleep 10

# 4. Run migrations
docker compose exec web python manage.py migrate

# 5. Create superuser
docker compose exec web python manage.py createsuperuser
```

---

## After Reset Checklist

Run these commands in order:

```bash
# ✅ Verify containers are running
docker compose ps

# ✅ Verify PostgreSQL is healthy
docker compose ps postgres
# Output should show: (healthy)

# ✅ Run migrations (ALL apps)
docker compose exec web python manage.py migrate

# ✅ Check migration status
docker compose exec web python manage.py showmigrations

# ✅ Create superuser
docker compose exec web python manage.py createsuperuser
# Enter: email, first name, last name, password

# ✅ Seed billing plans (required for feature gating)
docker compose exec web python manage.py seed_plans

# ✅ Optional: Seed test users for development
docker compose exec web python manage.py seed_users

# ✅ Clear Redis cache (if needed)
docker compose exec redis redis-cli FLUSHALL

# ✅ Restart Celery workers (they may have stale connections)
docker compose restart worker worker_notifications worker_otp beat

# ✅ Test the server
curl http://localhost:8888/admin/
# Should show Django admin login

# ✅ Test API
curl http://localhost:8888/api/v1/
# Should return API response
```

---

## Fixing the Vector Extension Issue

The root cause is that `pgvector` isn't installed. **Two options**:

### Option 1: Skip pgvector for now (Development)

Fake the problematic migrations:

```bash
# Fake the vector extension migration
docker compose exec web python manage.py migrate ai 0001 --fake
docker compose exec web python manage.py migrate ai 0002_enable_pgvector --fake
docker compose exec web python manage.py migrate ai 0003 --fake
docker compose exec web python manage.py migrate ai 0004 --fake
docker compose exec web python manage.py migrate ai 0005 --fake
docker compose exec web python manage.py migrate ai 0006 --fake
docker compose exec web python manage.py migrate ai 0007 --fake
```

### Option 2: Install pgvector properly (Production-ready)

Create a custom PostgreSQL Dockerfile:

```dockerfile
# postgres.Dockerfile
FROM postgres:16-alpine

# Install pgvector
RUN apk add --no-cache postgresql-16-pgvector

# Set permissions
RUN mkdir -p /usr/local/share/postgresql/extension && \
    chmod 755 /usr/local/share/postgresql/extension/
```

Then update `docker-compose.yml`:

```yaml
services:
  postgres:
    build:
      context: .
      dockerfile: postgres.Dockerfile
    # ... rest of config
```

---

## Quick Reference Commands

| Action | Command |
|--------|---------|
| Stop all | `docker compose down` |
| Stop + delete volumes | `docker compose down -v` |
| View logs | `docker compose logs -f postgres` |
| Shell into postgres | `docker compose exec postgres psql -U digital_chama` |
| Run Django shell | `docker compose exec web python manage.py shell` |
| Check migrations | `docker compose exec web python manage.py showmigrations` |
| Reset Redis | `docker compose exec redis redis-cli FLUSHALL` |

---

## Why This Fixes the Error

1. **Transaction Rollback**: When migration 0002 fails, PostgreSQL aborts the transaction
2. **Blocked State**: All subsequent statements in that transaction are blocked
3. **Fresh Start**: By removing the volume, we get a clean database
4. **No Corruption**: New database has no corrupted migration state

The "current transaction is aborted" error is a **PostgreSQL safety mechanism** - it prevents partial/inconsistent data from being saved when an error occurs.

---

## Last Resort: Nuke Everything

If nothing works:

```bash
# Stop everything
docker compose down -v --remove-orphans

# Remove ALL related Docker resources
docker system prune -f

# Start fresh
docker compose up -d --build

# Wait for database
sleep 30

# Migrate
docker compose exec web python manage.py migrate

# Create superuser
docker compose exec web python manage.py createsuperuser
```
