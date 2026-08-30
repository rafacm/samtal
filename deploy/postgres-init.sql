-- Provision one vinga database: the three schemas, `domain` for the
-- configuration, `record` for what was said and `memory`, which agent
-- memory is moving into and which nothing writes to yet, and the
-- read-only role an analyst queries the conversation record through.
--
-- One file, run in two places. The compose service mounts it into
-- /docker-entrypoint-initdb.d, where Postgres executes it once as the
-- superuser when the data directory is initialized; an infra repository
-- runs the same bytes by hand against its own instance:
--
--     psql "$ADMIN_URL" -f deploy/postgres-init.sql
--
-- What the executor needs is the right to create roles and to create
-- schemas in that database: a superuser, or the database's owner with
-- CREATEROLE. What the server role needs afterwards is nothing beyond
-- ordinary DML on its own schemas, which is why the schemas below are
-- created WITH AUTHORIZATION to it: the executor owns what it creates,
-- and CREATE on the database does not grant the server role CREATE on a
-- schema somebody else owns, which would leave Alembic unable to make
-- its own tables.
--
-- Repeatable by construction, because the documented reset is a
-- dropdb/createdb: that destroys the schemas and the database-local
-- default privileges while the instance-level vinga_ro survives, so the
-- role is create-or-rotate and every grant is written to be run again.
-- Rerun this file after any database reset.
--
-- Rerun it when a release moves the file, too, before starting the new
-- image. A release that adds a schema is exactly that case: the server
-- role deliberately has no CREATE on the database, so it cannot make a
-- new schema for itself, and an image started before the rerun refuses
-- to start with a fixed sentence naming this file.
--
-- Nothing here is a migration. The tables inside the three schemas are
-- Alembic's, created by the server on its first boot against a fresh
-- database; a database provisioned without this file simply has no
-- analyst role, and migrates and serves exactly the same.
\set ON_ERROR_STOP on

-- Which role the server connects as, and what the analyst role's
-- password is, read from the executing process's environment so that
-- the same file serves a compose default and a deployment's real
-- values. `\getenv` needs psql 15 or later; the compose pin is 17.
--
-- Defaulted first and then overwritten, because `\getenv` leaves the
-- variable untouched when the environment does not carry the name, and
-- an unset psql variable interpolates as its own literal text rather
-- than failing.
\set server_role vinga
\set ro_password vinga_ro
\getenv server_role VINGA_DB_USER
\getenv ro_password VINGA_DB_RO_PASSWORD

-- The analyst role's name is fixed rather than configurable: it is the
-- name the documentation, the recovery procedure and the integration
-- assertions all say, and one fact with one home.
\set ro_role vinga_ro

-- The three schemas, one per Alembic chain. `IF NOT EXISTS` so a rerun
-- is a no-op, and `AUTHORIZATION` so the server role owns them whoever
-- executes this file.
CREATE SCHEMA IF NOT EXISTS domain AUTHORIZATION :"server_role";
CREATE SCHEMA IF NOT EXISTS record AUTHORIZATION :"server_role";
CREATE SCHEMA IF NOT EXISTS memory AUTHORIZATION :"server_role";

-- The read-only role, created when it is missing and given its password
-- either way. Two statements rather than one PL/pgSQL block, because
-- psql does not interpolate its variables inside a dollar-quoted body
-- and the password has to arrive as one.
SELECT format('CREATE ROLE %I LOGIN', :'ro_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_role')
\gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'ro_role', :'ro_password')
\gexec

-- What an analyst's session may cost. Role-level rather than advisory,
-- because a reader's locks block DDL: a query or a transaction left
-- open in a terminal is what would otherwise make a boot migration wait
-- out its lock timeout and refuse.
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'ro_role', '60s')
\gexec

SELECT format(
    'ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', :'ro_role', '5min'
)
\gexec

-- Connecting, and reading the conversation record. Written against
-- current_database() so the file names no database of its own.
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'ro_role')
\gexec

SELECT format('GRANT USAGE ON SCHEMA record TO %I', :'ro_role')
\gexec

-- Both halves, so this file lands the same place whether it runs before
-- the server's first migration or after it: the tables that exist now,
-- and the ones the server role creates later.
SELECT format(
    'GRANT SELECT ON ALL TABLES IN SCHEMA record TO %I', :'ro_role'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA record '
    'GRANT SELECT ON TABLES TO %I',
    :'server_role',
    :'ro_role'
)
\gexec

-- And nothing at all on the domain schema, where the stored secrets'
-- ciphertexts live. A non-public schema grants PUBLIC nothing to begin
-- with, so this is written down rather than granted away: the revoke
-- below is what makes a schema somebody widened by hand narrow again.
SELECT format('REVOKE ALL ON SCHEMA domain FROM %I', :'ro_role')
\gexec

SELECT format(
    'REVOKE ALL ON ALL TABLES IN SCHEMA domain FROM %I', :'ro_role'
)
\gexec

-- And nothing at all on the memory schema either, written down the same
-- way and for a different reason. Remembered facts are not more
-- sensitive than the transcripts this role already reads; the operator
-- read surface for memory is #83's deliberate design, addressed by
-- scope and served over the API, and granting the raw tables here would
-- freeze a contract #83 is about to reshape. Narrowing a granted read
-- later breaks somebody; widening later is one additive line in this
-- file, which is the door #83 opens.
SELECT format('REVOKE ALL ON SCHEMA memory FROM %I', :'ro_role')
\gexec

SELECT format(
    'REVOKE ALL ON ALL TABLES IN SCHEMA memory FROM %I', :'ro_role'
)
\gexec
