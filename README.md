# cal-diy-build

Reproducible, **instance-agnostic** Docker images of [Cal.diy](https://github.com/calcom/cal.diy) (the MIT,
self-hosted community edition of Cal.com), built from a pinned upstream commit with the **unmodified
upstream Dockerfile**.

Why this exists: at the time of writing, no image is published under the Cal.diy name, and the upstream
image bakes most `NEXT_PUBLIC_*` values in at build time, so one image cannot serve different URLs. This
project builds once and lets every deployment set its own URL and branding at container start.

Not affiliated with Cal.com, Inc. Upstream says Cal.diy is intended for personal, non-production use.
Read their README before relying on it.

## How it works

1. The upstream Dockerfile builds the app with **placeholders** instead of instance values:
   `http://NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER` (the upstream mechanism) and `__CALDIY_RT_<NAME>__`
   for a few text values.
2. A thin layer (`Dockerfile.runtime`) adds `runtime-env.sh` as the container command.
3. On first start, `runtime-env.sh` replaces the placeholders in the built output with values from the
   container environment, or with upstream defaults, refuses to start if any are left, then runs upstream
   `scripts/start.sh` (`prisma migrate deploy`, app-store seed, `yarn start`).
4. CI runs a **smoke test** before pushing: it starts the image against a throw-away Postgres with
   `NEXT_PUBLIC_WEBAPP_URL=https://cal.example.test` and checks that:
   - migrations run;
   - `/api/version` answers;
   - no placeholder is left;
   - the runtime URL is in the client bundle;
   - the container survives a restart.

## Image

`ghcr.io/<owner>/cal-diy:<upstream-sha10>-<flavor>-r<run>`. The digest is printed in the run summary.
Pin `tag@sha256:…` in production, and don't use a moving tag.

| Flavor | Meaning |
|---|---|
| `nosignup` | `NEXT_PUBLIC_DISABLE_SIGNUP=true` baked into the client bundle (UI hides sign-up). |
| `signup` | Upstream default. |

**Sign-up is enforced server-side at runtime.** The `/signup` page and `POST /api/auth/signup` read
`NEXT_PUBLIC_DISABLE_SIGNUP` from the container environment, and also honour the global `disable-signup`
feature flag in the database. So whichever flavor you use, set `NEXT_PUBLIC_DISABLE_SIGNUP=true` on the
container to close sign-up. The flavor only changes what the UI shows.

Both flavors have telemetry disabled (`CALCOM_TELEMETRY_DISABLED=1`) and `NEXT_PUBLIC_LICENSE_CONSENT=agree`.
Platform: `linux/amd64`.

## Runtime configuration

Set by `runtime-env.sh` (applied once per container; recreate the container to change them):

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_WEBAPP_URL` | — (**required**) | Public URL, `https://host[:port][/path]`, no trailing slash |
| `NEXTAUTH_URL` | `NEXT_PUBLIC_WEBAPP_URL` | |
| `NEXT_PUBLIC_APP_NAME` | `Cal.diy` | |
| `NEXT_PUBLIC_COMPANY_NAME` | `Cal.com, Inc.` | |
| `NEXT_PUBLIC_SUPPORT_MAIL_ADDRESS` | `help@cal.com` | |
| `NEXT_PUBLIC_WEBSITE_TERMS_URL` | `https://cal.com/terms` | |
| `NEXT_PUBLIC_WEBSITE_PRIVACY_POLICY_URL` | `https://cal.com/privacy` | |

Text values must be one line and must not contain `" ' \` \ < > | &`.

Everything else is read by the app at runtime, as in upstream. See upstream `.env.example` at the commit
you built. The usual set:

| Variable | Notes |
|---|---|
| `DATABASE_URL`, `DATABASE_DIRECT_URL` | PostgreSQL; add `?connection_limit=N` to limit Prisma's pool |
| `DATABASE_HOST` | `host:port`, used by upstream `start.sh` to wait for the database |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` |
| `CALENDSO_ENCRYPTION_KEY` | `openssl rand -base64 24`. It encrypts stored calendar credentials, so **never lose or rotate it**. |
| `EMAIL_FROM`, `EMAIL_FROM_NAME`, `EMAIL_SERVER_HOST`, `EMAIL_SERVER_PORT`, `EMAIL_SERVER_USER`, `EMAIL_SERVER_PASSWORD` | SMTP |
| `GOOGLE_API_CREDENTIALS` | Google OAuth client JSON. The upstream seed writes it to the database on **every** start, so the env wins over the admin UI. |
| `ALLOWED_HOSTNAMES` | e.g. `'"cal.example.com"'` |
| `NEXT_PUBLIC_DISABLE_SIGNUP` | `true` closes public sign-up (server-side check, read at runtime) |
| `TZ` | Container timezone |

**Do not set `NEXT_PUBLIC_WEBSITE_URL`** unless you run the Cal.com marketing site. Upstream builds booking
links from it (`CAL_URL = WEBSITE_URL || WEBAPP_URL`), so pointing it at your own homepage breaks them.

Port `3000`. Health: `GET /api/version`. First user: open `/auth/setup` (works only while there are no
users; creates an ADMIN, and the password needs at least 15 characters with upper case, lower case and a
digit). Create it before exposing the instance publicly.

## Example

See [`examples/compose.yaml`](examples/compose.yaml) and [`examples/nginx.conf`](examples/nginx.conf).

## Reverse proxy notes

- **Embedding:** the app sends `X-Frame-Options: DENY` only on `/auth/*` and `/signup`. If your proxy adds a
  global `X-Frame-Options`, booking pages cannot be iframed. Allow embedding with
  `Content-Security-Policy: frame-ancestors 'self' https://your-site.example`.
- **Sign-up:** besides `NEXT_PUBLIC_DISABLE_SIGNUP=true`, you can return 404 for `/signup` and
  `/api/auth/signup` at the proxy, as defence in depth.

## Building

Actions → **build** → Run workflow:

| Input | Value |
|---|---|
| `ref` | full upstream commit SHA |
| `upstream` | default `calcom/cal.diy`; a fork works too |
| `flavor` | `nosignup` or `signup` |
| `image` | empty = `ghcr.io/<owner>/cal-diy` |
| `push` | push after the smoke test passes |

Pick a commit whose upstream CI is green. The build needs a standard GitHub-hosted runner for public
repositories (4 vCPU / 16 GB). It takes about 15–20 minutes.

## Updating an instance

Build the new SHA, then:
1. Back up the database.
2. Set the new `tag@digest`.
3. Run `docker compose up -d`.

Migrations run automatically on start and have no down migrations, so rollback = previous digest +
database restore.

## License

MIT, for the files in this repository. Cal.diy itself is MIT; see upstream.
