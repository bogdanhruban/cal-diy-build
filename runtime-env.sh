#!/bin/sh
# runtime-env.sh — container entrypoint (CMD) of the cal-diy image.
#
# The image is built with unique placeholders instead of instance-specific values. On start this script
# replaces them in the built Next.js output with values from the container environment (or with the
# upstream defaults when a variable is not set), then hands over to the upstream scripts/start.sh
# (prisma migrate deploy, app-store seed, yarn start).
#
# Runtime-configurable values:
#   NEXT_PUBLIC_WEBAPP_URL                  required, public URL of this instance, e.g. https://cal.example.com
#   NEXT_PUBLIC_APP_NAME                    default: Cal.diy
#   NEXT_PUBLIC_COMPANY_NAME                default: Cal.com, Inc.
#   NEXT_PUBLIC_SUPPORT_MAIL_ADDRESS        default: help@cal.com
#   NEXT_PUBLIC_WEBSITE_TERMS_URL           default: https://cal.com/terms
#   NEXT_PUBLIC_WEBSITE_PRIVACY_POLICY_URL  default: https://cal.com/privacy
#
# A container's environment cannot change after creation, so the replacement runs once per container;
# later restarts find nothing left to replace. To change a value, recreate the container.
set -eu
cd /calcom

die() { echo "runtime-env: $*" >&2; exit 1; }

URL="${NEXT_PUBLIC_WEBAPP_URL:-}"
case "$URL" in
  "" | *NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER*)
    die "set NEXT_PUBLIC_WEBAPP_URL to the public URL of this instance (e.g. https://cal.example.com)" ;;
esac
printf '%s' "$URL" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~-]+)*$' \
  || die "NEXT_PUBLIC_WEBAPP_URL must be a plain http(s) URL without a trailing slash, got: $URL"
export NEXTAUTH_URL="${NEXTAUTH_URL:-$URL}"

# Values end up inside JavaScript string literals and a sed expression: keep them to one plain line.
check_text() {
  case "$2" in
    *[\"\'\`\\\<\>\|\&]*) die "$1 must not contain any of: \" ' \` \\ < > | &" ;;
  esac
  [ "$(printf '%s' "$2" | wc -l)" -eq 0 ] || die "$1 must be a single line"
}

APP_NAME="${NEXT_PUBLIC_APP_NAME:-Cal.diy}"
COMPANY_NAME="${NEXT_PUBLIC_COMPANY_NAME:-Cal.com, Inc.}"
SUPPORT_MAIL="${NEXT_PUBLIC_SUPPORT_MAIL_ADDRESS:-help@cal.com}"
TERMS_URL="${NEXT_PUBLIC_WEBSITE_TERMS_URL:-https://cal.com/terms}"
PRIVACY_URL="${NEXT_PUBLIC_WEBSITE_PRIVACY_POLICY_URL:-https://cal.com/privacy}"
check_text NEXT_PUBLIC_APP_NAME "$APP_NAME"
check_text NEXT_PUBLIC_COMPANY_NAME "$COMPANY_NAME"
check_text NEXT_PUBLIC_SUPPORT_MAIL_ADDRESS "$SUPPORT_MAIL"
check_text NEXT_PUBLIC_WEBSITE_TERMS_URL "$TERMS_URL"
check_text NEXT_PUBLIC_WEBSITE_PRIVACY_POLICY_URL "$PRIVACY_URL"

PATTERN='__CALDIY_RT_[A-Z_]+__|http://NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER'
DIRS="apps/web/.next apps/web/public"

count=$(grep -rlE "$PATTERN" $DIRS 2>/dev/null | wc -l)
if [ "$count" -gt 0 ]; then
  echo "runtime-env: applying runtime configuration to $count files (NEXT_PUBLIC_WEBAPP_URL=$URL)"
  grep -rlEZ "$PATTERN" $DIRS 2>/dev/null | xargs -0 sed -i \
    -e "s|http://NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER|$URL|g" \
    -e "s|__CALDIY_RT_APP_NAME__|$APP_NAME|g" \
    -e "s|__CALDIY_RT_COMPANY_NAME__|$COMPANY_NAME|g" \
    -e "s|__CALDIY_RT_SUPPORT_MAIL_ADDRESS__|$SUPPORT_MAIL|g" \
    -e "s|__CALDIY_RT_WEBSITE_TERMS_URL__|$TERMS_URL|g" \
    -e "s|__CALDIY_RT_WEBSITE_PRIVACY_POLICY_URL__|$PRIVACY_URL|g"
  left=$(grep -rlE "$PATTERN" $DIRS 2>/dev/null | wc -l)
  [ "$left" -eq 0 ] || die "$left files still contain placeholders after replacement (unknown placeholder?)"
else
  echo "runtime-env: runtime configuration already applied"
fi

# Tell upstream start.sh the URL is already in place, so it skips its own replacement pass.
export BUILT_NEXT_PUBLIC_WEBAPP_URL="$URL"
exec /calcom/scripts/start.sh
