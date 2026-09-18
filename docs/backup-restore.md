# Backup and restore

Nothing here is automatic. What a deployment keeps, and how to get it back.

- [Taking a backup](#taking-a-backup)
- [Restoring](#restoring)

## Taking a backup

Everything the deployment cannot afford to lose is in two Docker volumes, and neither is
backed up by anything. A named volume survives `docker compose down`, which is what makes
it feel safe; it does not survive `docker compose down -v`, a pruned host, or a dead disk.

| Volume | Holds | If you lose it |
| --- | --- | --- |
| `attendance-data` | `attendance.db`, `known_faces/`, `encodings.npz` | The attendance record and every enrolled face. Everyone re-enrols from photographs you no longer have. |
| `caddy-data` | The internal CA's private key and issued certificates | Caddy generates a new CA, and every kiosk that trusted the old root has to be visited and re-trusted by hand. |

`encodings.npz` is the one thing here that is derivable — `scripts/build_encodings.py`
rebuilds it from `known_faces/`. Back it up anyway; restoring it is a file copy, and
rebuilding it is a job.

**Do not back the database up by copying the file.** The database runs in WAL mode, so at
any instant the committed state is spread across `attendance.db` and `attendance.db-wal`.
`cp` reads them at different moments and can produce a database missing its most recent
check-ins, or one that will not open at all — and it fails this way only when someone was
being marked present mid-copy, which is to say rarely, and never while you are testing the
backup.

SQLite's own backup API takes a consistent snapshot of a live database with writers
attached, which is exactly the situation here:

```bash
docker compose exec app python -c "import sqlite3, pathlib; pathlib.Path('/data/backup').mkdir(exist_ok=True); source = sqlite3.connect('/data/attendance.db'); target = sqlite3.connect('/data/backup/attendance.db'); source.backup(target); target.close(); source.close()"
```

Then copy the snapshot and the photographs off the host:

```bash
docker compose cp app:/data/backup/attendance.db ./attendance-backup.db
```

```bash
docker compose cp app:/data/known_faces ./known_faces-backup
```

```bash
docker compose cp caddy:/data/caddy ./caddy-backup
```

**Off the host.** A backup on the same disk as the thing it protects covers exactly one
failure — a mistaken `down -v` — and not the one that takes the machine.

> **Treat the backup as biometric data, because it is.** `known_faces/` is a folder of
> photographs of identifiable people and `encodings.npz` is a mathematical description of
> their faces; neither becomes less sensitive for being in a tarball on a laptop. Whatever
> obligations apply to the running system — encryption at rest, access control, a
> retention limit, deleting someone's data when they ask — apply to every copy of it. A
> backup nobody remembers taking is the copy that outlives the retention policy.

## Restoring

Stop the application first. Restoring underneath a running worker means overwriting a file
that has connections open on it, which is the corruption case above with the steps in the
other order.

```bash
docker compose stop app
```

```bash
docker compose cp ./attendance-backup.db app:/data/attendance.db
```

```bash
docker compose cp ./known_faces-backup app:/data/known_faces
```

**Then fix the ownership, or the application will start and fail to write.** The container
runs as uid 10001, and `docker compose cp` writes what it copies in as root — so a restored
database is readable, every page renders, and the first check-in of the day fails with
"attempt to write a readonly database".

```bash
docker compose exec -u root app chown -R attendance:attendance /data
```

> **On Windows, run that one from PowerShell or CMD.** It is the exact shape Git Bash
> rewrites — `/data` becomes `C:/Program Files/Git/data` and the command fails with
> "cannot access", having changed nothing, while the database stays root-owned and the
> next check-in still fails. `MSYS_NO_PATHCONV=1` in front of it is the Git Bash fix.

The healthcheck catches this state, so you do not have to remember to look for it:
`/healthz` treats a database it cannot write to as unhealthy, for exactly this reason.

```bash
docker compose start app
```

There is one thing to delete rather than restore: the old `attendance.db-wal` and
`attendance.db-shm` sidecars, if any came along. They belong to the database that was
replaced, and SQLite will recreate them.

**Test the restore before you need it**, on a throwaway volume rather than the live one. An
untested backup is a belief about a file, and the failure mode of that belief is finding
out on the morning the disk dies.

