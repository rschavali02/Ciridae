# The eval / test database

The eval harness (`app/eval/harness.py`) and the test suite
(`tests/conftest.py`) both empty every table before each trial or test, and
rely on an outer transaction to put the rows back. That works — but it is the
*only* thing that works, and `DELETE FROM` every table is already written and
runs 36 times in a full eval run. One refactor that opens a plain
`SessionLocal()` instead, or drops `join_transaction_mode="create_savepoint"`,
turns 36 harmless wipes into a real one against whatever database it is
pointed at.

So both are pointed at a second, throwaway database instead. The rollback still
does the work; the separate database means getting the rollback wrong costs
nothing.

Set `EVAL_DATABASE_URL` in `backend/.env`:

```
EVAL_DATABASE_URL=postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent_eval
```

Without it, the harness and the test suite refuse to start. There is
deliberately no fallback to `DATABASE_URL` — a fallback would put the
wipe-everything code back on the development database at exactly the moment
the setting is missing.

## Fresh clone

`initdb/01-create-eval-db.sql` creates the database and its extensions
automatically, because Postgres runs everything in
`/docker-entrypoint-initdb.d` when the data volume is first created. Then run
the two commands under "Schema and corpus" below.

## Existing volume

An init script never runs against a volume that already exists, so create the
database by hand once:

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent \
  -c "CREATE DATABASE invoice_agent_eval;"

docker compose exec db psql -U invoice_agent -d invoice_agent_eval \
  -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

The extensions are not optional and are not installed by any migration.
`alembic revision --autogenerate` only diffs tables and columns, so
`CREATE EXTENSION` has never been part of the migration chain — the
application database has them because someone ran them by hand. A database
with all seven migrations applied and neither extension installed still cannot
run `lookup_vendor`'s `similarity()` or store a pgvector column.

## Schema and corpus

Both commands take the URL from the environment, so no code change is needed
to point them at the eval database:

```bash
cd backend && source venv/bin/activate

DATABASE_URL="postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent_eval" \
  alembic upgrade head

DATABASE_URL="postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent_eval" \
  python -m fixtures.load_policy
```

**The second command is the one that gets forgotten.** The harness deliberately
does not wipe `documents`, which also means it never populates it. An eval
database with an empty `documents` table does not error — every
policy-dependent case simply fails, and it reads exactly like retrieval having
regressed. It costs one Voyage batch call (25 chunks) to load, once.

Note that `tests/conftest.py` *does* wipe `documents` (the harness does not),
but inside the same rolled-back transaction, so the corpus survives a test run.

## Verifying

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent_eval \
  -c "SELECT count(*) FROM documents;"
```

Expect 25. Then `python -m pytest -q -m "not integration"` should pass, and the
application database should be untouched by it.
