-- Idempotent script to create the Postgres role the Lambda consumer needs to log in
--
-- Run it once per environment, as the master user:
--     psql "host=$DB_HOST port=$DB_PORT dbname=$DB_NAME user=$DB_USER sslmode=require" \
--          -f scripts/bootstrap_db_user.sql

-- Postgres has no CREATE USER IF NOT EXISTS, hence the DO block. Every GRANT below is idempotent on its own
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airmax_lambda') THEN
        CREATE USER airmax_lambda;
    END IF;
END
$$;

-- No password is set anywhere: rds_iam is what tells RDS to accept a signed IAM token in the password field instead
GRANT rds_iam TO airmax_lambda;

GRANT CONNECT ON DATABASE airmax TO airmax_lambda;
GRANT USAGE ON SCHEMA public TO airmax_lambda;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO airmax_lambda;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO airmax_lambda;

-- The two grants above only cover tables that exist right now. Without these defaults, the next migration creates
-- tables the Lambda can't see, and it surfaces days later as a permission error on a table nobody remembers adding.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO airmax_lambda;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO airmax_lambda;
