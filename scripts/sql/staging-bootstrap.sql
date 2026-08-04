-- Run against postgres as the RDS master user. Passwords are psql variables
-- injected directly from Secrets Manager by ecs-run-sql.sh.
SELECT format('CREATE ROLE %I LOGIN', 'auth_app_staging')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_app_staging') \gexec
SELECT format('CREATE ROLE %I LOGIN', 'items_app_staging')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'items_app_staging') \gexec

ALTER ROLE auth_app_staging WITH LOGIN PASSWORD :'AUTH_PASSWORD';
ALTER ROLE items_app_staging WITH LOGIN PASSWORD :'ITEMS_PASSWORD';

SELECT 'CREATE DATABASE auth_staging'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'auth_staging') \gexec
SELECT 'CREATE DATABASE items_staging'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'items_staging') \gexec

REVOKE ALL ON DATABASE auth_staging FROM PUBLIC;
REVOKE ALL ON DATABASE items_staging FROM PUBLIC;
GRANT CONNECT ON DATABASE auth_staging TO auth_app_staging;
GRANT CONNECT ON DATABASE items_staging TO items_app_staging;

