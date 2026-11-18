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

`ghcr.io/<owner>/cal-diy:<upstream-sha10>-<flavor>[-p]-r<run>` (`-p` = [patches](#patches) applied). The digest is printed in the run summary.
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

## Patches

By default the build applies the files in [`patches/`](patches/) to the upstream checkout, in order, with
`git apply`. They are minimal bug fixes, each meant to be sent upstream and dropped once upstream has it.
Nothing else in the source is changed. Each patch ends up in one of three states:

| Status | Meaning |
|---|---|
| `applied` | Applies cleanly; applied. |
| `upstreamed` | Applies in reverse, so upstream already contains the fix; skipped (the build continues). |
| `failed` | Applies neither way; the build fails after reporting every patch. Refresh or drop it. |

| Patch | Fixes |
|---|---|
| `0001-calendars-filter-selected-calendars-by-credential` | With two connected accounts of the same calendar type (e.g. two Google or two Zoho accounts), each credential was asked for the other account's selected calendars, because they were filtered by integration type only. Busy times stayed correct, but logs filled with errors (`CALENDAR_NOTFOUND`, "error getting events", "getAvailabilityWithTitles failed"). Now a selected calendar that records its `credentialId` goes only to that credential; legacy rows without one are kept. CalDAV matching is unchanged. |
| `0002-zohocalendar-empty-freebusy` | Zoho returns no `freebusy` key when nothing is busy; the code threw a `TypeError` and dropped availability for that whole Zoho credential. Missing `freebusy` is now treated as empty. |

The applied patches are listed in the image label `io.github.cal-diy-build.patches` and in the run
summary. The tag carries `-p` only when at least one patch was applied. To build plain upstream, run the
workflow with `apply_patches` = `false`.

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

## CDN / browser caching (important)

Next.js serves `/_next/static/*` as `public, max-age=31536000, immutable`, but this image rewrites the
*content* of those files at container start without changing their names. After you change a runtime value
(URL, app name, links), a CDN or browser that already cached a chunk keeps serving the old one for up to a
year. So:

- override the header at your reverse proxy, e.g. `Cache-Control: public, max-age=86400` for
  `/_next/static/` (see `examples/nginx.conf`), and
- purge your CDN cache for the instance hostname after changing runtime configuration.

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
| `apply_patches` | default `true`; apply [`patches/`](#patches) |

Pick a commit whose upstream CI is green. The build needs a standard GitHub-hosted runner for public
repositories (4 vCPU / 16 GB). It takes about 15–20 minutes.

`build.yml` is also a reusable workflow (`workflow_call`) with the same inputs. It returns `image`, `tag`,
`digest`, `pinned` (`image:tag@digest`) and `patches_json` (`[{"name", "status"}]`).

## Automated updates

[`upstream-watch`](.github/workflows/upstream-watch.yml) runs every Monday at 01:00 UTC (and on demand from
the Actions tab):

1. **Candidate.** It walks back up to 20 commits on upstream `main` and takes the newest one whose check runs
   are all `success`, `skipped` or `neutral`.
2. **Up to date?** If the latest release of this repository was built from that commit, it stops. Run it
   manually with `force` to rebuild anyway, or with `ref` to build a specific commit (check runs are then
   not evaluated).
3. **Upgrade report.** Between the previous release and the candidate: commit count and compare link,
   commits grouped by [Conventional Commit](https://www.conventionalcommits.org/) type, new
   `packages/prisma/migrations/` directories, variable names added to or removed from `.env.example` and
   `.env.appStore.example`, and changes to build-sensitive files (`Dockerfile`, `scripts/start.sh`,
   `scripts/replace-placeholder.sh`, `apps/web/next.config.ts`, `packages/prisma/schema.prisma`,
   `packages/lib/constants.ts`). It also checks that the upstream build still provides what
   `runtime-env.sh` relies on (the `NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER` URL in the `Dockerfile`, and
   `BUILT_NEXT_PUBLIC_WEBAPP_URL` in `scripts/start.sh`), and fails if it does not.
4. **Build.** It calls `build.yml` with flavor `nosignup`, patches on, push on. Nothing is published unless
   the smoke test passes.
5. **Release.** It publishes a GitHub Release tagged with the image tag, with the report as release notes
   and a `release.json` asset.
6. **Upstreamed patches.** If a patch was `upstreamed`, it opens a pull request
   (`chore/drop-upstreamed-patches-<sha10>`) that deletes it and its README row.
7. **Failures.** If anything fails (a patch conflict, the runtime check, the build or the smoke test), it
   opens an issue labelled `upstream-watch`, or updates the open one, with the failed steps, the error
   messages and the run link. The next successful run closes it. Downstream consumers stay on the previous
   release in the meantime.

Settings for forks:

- The upstream repository and branch default to `calcom/cal.diy` and `main`; override them with the
  repository variables `UPSTREAM_REPO` and `UPSTREAM_BRANCH`.
- For step 6, enable *Settings → Actions → General → Allow GitHub Actions to create and approve pull
  requests*. Pull requests opened with `GITHUB_TOKEN` do not trigger workflows.
- Scheduled workflows are paused after 60 days without activity in the repository.

### `release.json`

Downstream automation (a deploy job, a bot, a dashboard) should read the `release.json` asset of the latest
release rather than parse the notes:

```sh
gh release download -R <owner>/cal-diy-build -p release.json -O -          # latest release
curl -fsSL https://github.com/<owner>/cal-diy-build/releases/latest/download/release.json
```

```json
{
  "schema": 1,
  "image": "ghcr.io/<owner>/cal-diy",
  "tag": "ae027bb70e-nosignup-p-r12",
  "digest": "sha256:…",
  "ref": "<upstream commit, 40 hex>",
  "upstream": "calcom/cal.diy",
  "flavor": "nosignup",
  "previous_ref": "<40 hex, or null for the first release>",
  "upstream_compare_url": "https://github.com/calcom/cal.diy/compare/…",
  "commit_count": 42,
  "smoke_test": "passed",
  "patches": [{"name": "0001-….patch", "status": "applied"}],
  "migrations_added": ["20261110000000_example"],
  "env_example": {"added": [], "removed": []},
  "risky_files_changed": ["packages/lib/constants.ts"],
  "published_at": "2026-11-16T01:23:45Z"
}
```

- Deploy `image:tag@digest`. Only releases whose image passed the smoke test are published, so
  `smoke_test` is always `passed`.
- `patches[].status` is `applied` or `upstreamed`; a `failed` patch fails the build, so no release has one.
- `previous_ref` is the upstream commit of the release before this one; the diff fields (`commit_count`,
  `migrations_added`, `env_example`, `risky_files_changed`) cover `previous_ref..ref`. When
  `previous_ref` is `null` they are empty and `commit_count` is `0`.
- A consumer that skips releases should merge the diff fields across every release it skipped, or compare
  its own deployed `ref` with the new one.
- Non-empty `migrations_added` means the database schema changes on first start: back up first.
- `schema` changes only on incompatible changes; new fields may be added without a bump.

## Updating an instance

Build the new SHA, then:
1. Back up the database.
2. Set the new `tag@digest`.
3. Run `docker compose up -d`.

Migrations run automatically on start and have no down migrations, so rollback = previous digest +
database restore.

## License

MIT, for the files in this repository. Cal.diy itself is MIT; see upstream.
