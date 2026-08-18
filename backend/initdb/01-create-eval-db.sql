-- Creates the throwaway database the eval harness and test suite run against,
-- and installs the two extensions the schema needs.

CREATE DATABASE invoice_agent_eval;

\connect invoice_agent_eval

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
