#!/bin/sh
# Container entrypoint: migrate, then serve.
#
# Why this exists: the schema is owned by Alembic (project.md Section 4), and
# `docker compose up` against a fresh postgres volume would otherwise start
# uvicorn against an empty database - every query fails on a missing table and
# the cause is not obvious. compose gates this script behind
# `postgres: condition: service_healthy`, so postgres is accepting connections
# before migrations run.
#
# The `&&` is deliberate: if migrations fail, uvicorn must NOT start. A backend
# serving requests against an unmigrated schema would look alive on /health
# while every real endpoint 500s. Exiting non-zero surfaces the failure and
# lets `restart: unless-stopped` retry.

set -eu

echo "[entrypoint] applying database migrations (alembic upgrade head)"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
# exec so uvicorn becomes PID 1 and receives SIGTERM directly on `docker stop`,
# which lets the lifespan shutdown hook run (closes LLM/Qdrant/engine clients).
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
