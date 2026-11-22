#!/bin/sh
# start-web.sh — last step of upstream scripts/start.sh in this image (replaces its `yarn start`).
#
# Upstream starts the web app through `yarn start` -> yarn 4 -> turbo -> yarn 4 -> `next start`: five idle Node
# and Go processes (~275 MiB, all of it swapped out on a small host) in front of the one that serves requests.
# This runs the same `next start` the chain ends in, with nothing in between:
#   - same working directory (/calcom/apps/web): the app reads app-store and email-template files relative to it;
#   - same next.config.ts evaluated at startup (it derives env such as NEXT_PUBLIC_CALCOM_VERSION) — this is why
#     the traced `.next/standalone` server is NOT used;
#   - same environment: turbo's strict env mode hid exactly these container variables from the app, so they are
#     removed here too (HOSTNAME would otherwise be the container id). Everything else reached the app unchanged.
# `exec` makes next-server PID 1, so `docker stop` (SIGTERM) shuts it down cleanly instead of waiting for SIGKILL.
set -eu
cd /calcom/apps/web
exec env -u HOSTNAME -u DATABASE_HOST -u BUILT_NEXT_PUBLIC_WEBAPP_URL -u NODE_VERSION -u YARN_VERSION \
  node /calcom/node_modules/next/dist/bin/next start
