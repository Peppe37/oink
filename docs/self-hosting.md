# Self-hosting Oink

Oink is one container with a SQLite file. Everything else (documents engine, AI, bank sync) is
optional. This page covers what you need to run it for yourself or for a household.

## Requirements
- Docker with Compose. 256 MB of RAM is enough; the optional documents engine wants about 4 GB.
- amd64 or arm64.

## First start
```bash
mkdir oink && cd oink
curl -O https://raw.githubusercontent.com/Peppe37/oink/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Peppe37/oink/main/.env.example
docker compose up -d
```
Open `http://localhost:8080` and create your account. The service admin is `admin` (password
`oink`, or `OINK_ADMIN_PASSWORD`); it must change its password at the first sign-in and never
sees anybody's finances: it only manages people and settings.

Set in `.env` before you share it with others: `OINK_PUBLIC_URL` (the address people use),
`OINK_CURRENCY` and `OINK_TIMEZONE` (defaults for new people; everybody can change theirs) and
`OINK_SIGNUP=false` if only invited people should join.

## Putting it on the internet
Oink does not need to be public. If you want to reach it from your phone, prefer a private
network (Tailscale, WireGuard) or an access layer (Cloudflare Access, Authelia, an OIDC
provider with `OINK_OIDC_*`). If you publish it, put a reverse proxy with HTTPS in front and set
`OINK_TRUSTED_PROXIES` to its address. Caddy, as an example:

```
oink.example.com {
    reverse_proxy 127.0.0.1:8080
}
```
Bind Oink to localhost behind the proxy with `OINK_BIND=127.0.0.1` in `.env`.

## Backups and restore
Everything lives in `./data`. Take a consistent copy of the database at any time:
```bash
scripts/backup.sh          # ./data/backups/oink-YYYYmmdd-HHMMSS.db, last 14 kept
```
Do not simply copy `oink.db` while Oink runs: part of the data may still be in the `-wal`
file. Copy `./data/backups`, `./data/documents` and `./data/secret.key` to a private place
(the key decrypts the API keys of linked accounts).

Restore: `docker compose down`, replace `data/oink.db` with a backup (delete `oink.db-wal` and
`oink.db-shm`), `docker compose up -d`. Before any schema upgrade Oink also copies the database
to `data/oink-before-migration-<revision>.db` by itself.

## Updating
`docker compose pull && docker compose up -d`, or the optional one-click updater (see the
README). Pin a version with `OINK_TAG` if you prefer to update on your own schedule. Read the
release notes: a release with a database migration is the moment to take a backup first.

## What leaves your server
Nothing, unless you turn it on: the AI model you choose (`OINK_AI_MODEL`; use
`OINK_AI_SEND_DESCRIPTIONS=false` to share only totals, or a local Ollama model), the bank
provider you link (Enable Banking, your own application), the update check
(`OINK_UPDATE_CHECK=false` turns it off), and market prices for holdings you track.

## Health
`GET /api/health` answers when Oink is up; the image's container health check uses it.
