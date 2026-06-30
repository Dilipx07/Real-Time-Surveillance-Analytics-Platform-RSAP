# RSAP central backend

## Database schema lifecycle

`infra/postgres/init.sql` is the provisioning baseline for a fresh PostgreSQL
database. Docker's PostgreSQL entrypoint runs it only while initializing a new
data volume, and its current schema stores encrypted Aadhaar last-four values
in `events.registered_persons.aadhaar_last4 TEXT`.

Alembic migrations upgrade databases that already exist. They do not recreate
or compete with the provisioning baseline. Revision `0001_encrypt_aadhaar`
therefore behaves as follows:

- legacy `CHAR(4)` column: alter it to `TEXT`;
- current `TEXT` column: no operation;
- table not provisioned yet: no operation.

Run migrations from `apps/webapp-backend` with environment variables set:

```powershell
alembic upgrade head
```

The migration is intentionally irreversible. Once the column contains
encrypted ciphertext, converting it back to `CHAR(4)` would truncate data.

## Aadhaar handling

Person creation and update encrypt the validated last four digits with AES-256
GCM before passing the value to PostgreSQL. API responses always replace the
stored ciphertext with `****`; the backend has no endpoint that decrypts or
returns Aadhaar digits.
