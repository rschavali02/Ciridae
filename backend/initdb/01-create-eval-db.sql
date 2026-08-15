-- Creates the throwaway database the eval harness and test suite run against,
-- and installs the two extensions the schema needs.
--
-- Postgres runs everything in /docker-entrypoint-initdb.d exactly once, when
-- the data volume is first created. An existing volume never sees this file --
-- see README-eval-db.md for the commands to run by hand on a volume that
-- already exists.
--
-- The extensions are installed here rather than in an Alembic migration because
-- that is where the application's own extensions came from: `alembic
-- revision --autogenerate` only diffs tables and columns, so CREATE EXTENSION
-- has never been part of the migration chain. A database that has every
-- migration applied and neither extension installed still cannot run
-- lookup_vendor's similarity() or store a pgvector column.

CREATE DATABASE invoice_agent_eval;

\connect invoice_agent_eval

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
