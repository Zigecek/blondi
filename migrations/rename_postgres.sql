-- Migrace PostgreSQL identity ze `spot_operator` na `blondi`.
--
-- Spustit jako PG superuser (typicky `postgres`):
--   psql -U postgres -h <host> -p <port> -d postgres -f migrations/rename_postgres.sql
--
-- POZOR — předpoklady:
--   1. Aplikace Blondi MUSÍ být úplně zastavená. Žádné aktivní sessiony
--      nesmí existovat (ALTER DATABASE RENAME vyžaduje exclusive access).
--      Zkontroluj v pgAdminu / přes:
--          SELECT pid, usename, application_name, state
--            FROM pg_stat_activity
--            WHERE datname = 'spot_operator';
--      Pokud něco visí, ukonči to (\q v psql, zavřít pgAdmin tab).
--   2. Připojuj se k databázi `postgres` (NE `spot_operator`) — jinak
--      ALTER DATABASE selže s "database is being accessed by other users".
--   3. Po RENAME zaktualizuj DSN v `.env` a `alembic.ini`:
--          DATABASE_URL=postgresql+psycopg://blondi:<heslo>@<host>:<port>/blondi
--      a v alembic.ini:
--          sqlalchemy.url = postgresql+psycopg://blondi:heslo@localhost:5432/blondi
--   4. Pak pust `python -m blondi.migrate_keyring` pro migraci hesel
--      v Windows Credential Lockeru.
--   5. Spusť aplikaci — ověř `python main.py --diag`.
--
-- Tabulky, indexy, constrainty, data zůstávají beze změny — měníme jen
-- jméno role a databáze.

\echo '== Před migrací =='
SELECT current_database() AS current_db;
SELECT rolname FROM pg_roles WHERE rolname IN ('spot_operator', 'blondi');
SELECT datname FROM pg_database WHERE datname IN ('spot_operator', 'blondi');

-- 1) Rename databáze.
ALTER DATABASE spot_operator RENAME TO blondi;

-- 2) Rename role.
ALTER ROLE spot_operator RENAME TO blondi;

-- 3) (Volitelné) Rotace hesla při rebrandu. Odkomentuj a doplň heslo,
--    pokud chceš zároveň změnit credentials. Pokud ponecháš hash, staré
--    heslo zůstává platné.
-- ALTER ROLE blondi WITH PASSWORD 'NOVE-HESLO-SEM';

\echo '== Po migraci =='
SELECT rolname FROM pg_roles WHERE rolname IN ('spot_operator', 'blondi');
SELECT datname FROM pg_database WHERE datname IN ('spot_operator', 'blondi');

\echo '== HOTOVO =='
\echo 'Aktualizuj DSN v .env a alembic.ini, pak spust python -m blondi.migrate_keyring.'
