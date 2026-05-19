# Alembic migrations

Sprint 2 — Alembic is now the source of truth for schema changes (action plan item **A2.4**). The baseline revision `0001_baseline_post_sprint2` represents the schema as produced by `Base.metadata.create_all` at the end of Sprint 2 (Phase 1 reset + Sprint 1 `must_change_password` + Sprint 2 `token_hash` + `rate_limit_attempts`).

## Workflow

### Fresh install (post Sprint 2)

```bash
# 1. Reset recreates every table from the ORM metadata:
docker exec -e ALLOW_DB_RESET=yes -it towt-app-v2 python3 scripts/reset_database.py

# 2. Stamp the DB as already at the baseline so Alembic knows.
docker exec towt-app-v2 alembic stamp 0001_baseline_post_sprint2
```

### Existing install already running Sprint 2 code

The reset+stamp combo above brings the DB in sync. If you prefer to skip
the reset, stamp the baseline manually:

```bash
docker exec towt-app-v2 alembic stamp 0001_baseline_post_sprint2
```

### Creating a new migration

```bash
# Edit SQLAlchemy models, then:
docker exec towt-app-v2 alembic revision --autogenerate -m "add xxx"
# Review the generated file in migrations/versions/, commit it.
# Apply:
docker exec towt-app-v2 alembic upgrade head
```

### Rolling back

```bash
docker exec towt-app-v2 alembic downgrade -1
```

## Why the baseline is empty

The baseline revision is intentionally a no-op. The schema it represents
is already laid down by `Base.metadata.create_all` at reset time; the
baseline's role is only to tell Alembic "you start counting from here".
Every real schema change from now on lives in its own revision under
`migrations/versions/`.
