\getenv app_password APP_DB_PASSWORD
SELECT format('CREATE ROLE legal_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_password') \gexec
GRANT CONNECT ON DATABASE legal TO legal_app;
GRANT USAGE ON SCHEMA public TO legal_app;
