#!/usr/bin/env python3
"""Helpers for the upstream-watch workflow.

  upstream_watch.py report  --upstream OWNER/REPO --candidate SHA [--previous SHA] --out report.json
      Compares two upstream commits and writes an upgrade report (commits grouped by Conventional
      Commit type, new database migrations, .env example changes, build-sensitive files). Exits with
      status 2 if the upstream build no longer provides what runtime-env.sh relies on.

  upstream_watch.py release --report report.json --image IMAGE --tag TAG --digest DIGEST
                            --flavor FLAVOR --patches JSON --run-url URL
                            --release-json release.json --notes notes.md
      Writes the release.json asset (a stable contract for downstream consumers) and the release notes.

Requires git and an authenticated gh CLI (GH_TOKEN) on PATH. Standard library only.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile

MIGRATIONS_DIR = "packages/prisma/migrations/"
SCHEMA_FILE = "packages/prisma/schema.prisma"
ENV_EXAMPLES = [".env.example", ".env.appStore.example"]
RISKY_FILES = [
    "Dockerfile",
    "scripts/start.sh",
    "scripts/replace-placeholder.sh",
    "apps/web/next.config.ts",
    SCHEMA_FILE,
    "packages/lib/constants.ts",
]
# What runtime-env.sh depends on in the upstream build: the URL placeholder baked in by the Dockerfile,
# and start.sh honouring BUILT_NEXT_PUBLIC_WEBAPP_URL (so it skips its own replacement pass).
REQUIRED_STRINGS = [
    ("Dockerfile", "NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER"),
    ("scripts/start.sh", "BUILT_NEXT_PUBLIC_WEBAPP_URL"),
]
GROUPS = [
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("chore", "Maintenance"),
    ("other", "Other"),
]
# Conventional Commit types folded into "chore".
CHORE_TYPES = {"chore", "build", "ci", "docs", "style", "test", "tests"}
TOP_ITEMS = 15
CC_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\([^)]*\))?(?P<bang>!)?:\s")
ENV_RE = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")


def run(*args, cwd=None, check=True):
    res = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.stderr.write(res.stderr)
        raise SystemExit(f"command failed ({res.returncode}): {' '.join(args)}")
    return res


def gh_api(path, paginate=False, jq=None):
    args = ["gh", "api", path]
    if paginate:
        args.append("--paginate")
    if jq:
        args += ["--jq", jq]
    return run(*args).stdout


def classify(subject):
    m = CC_RE.match(subject)
    if not m:
        return "other", False
    kind = m.group("type").lower()
    if kind in CHORE_TYPES:
        kind = "chore"
    elif kind not in {"feat", "fix", "perf", "refactor"}:
        kind = "other"
    return kind, bool(m.group("bang"))


def env_names(text):
    return {m.group(1) for line in text.splitlines() if (m := ENV_RE.match(line))}


def show(repo_dir, sha, path):
    res = run("git", "show", f"{sha}:{path}", cwd=repo_dir, check=False)
    return res.stdout if res.returncode == 0 else None


def cmd_report(a):
    up, cand, prev = a.upstream, a.candidate, a.previous or None
    report = {
        "upstream": up,
        "candidate": cand,
        "previous_ref": prev,
        "compare_status": None,
        "upstream_compare_url": f"https://github.com/{up}/commits/{cand}",
        "commit_count": 0,
        "groups": {k: {"count": 0, "items": []} for k, _ in GROUPS},
        "breaking": [],
        "migrations_added": [],
        "env_example": {"added": [], "removed": []},
        "risky_files_changed": [],
        "schema_changed_without_migration": False,
        "runtime_contract": [],
    }

    with tempfile.TemporaryDirectory() as repo:
        # Trees of both commits only; blobs are fetched lazily for the few files read below.
        run("git", "init", "-q", repo)
        run("git", "remote", "add", "origin", f"https://github.com/{up}.git", cwd=repo)
        run("git", "fetch", "-q", "--filter=blob:none", "--depth=1", "origin", *[s for s in (cand, prev) if s], cwd=repo)

        failed = False
        for path, needle in REQUIRED_STRINGS:
            text = show(repo, cand, path)
            ok = text is not None and needle in text
            report["runtime_contract"].append({"file": path, "string": needle, "present": ok})
            if not ok:
                failed = True
                print(f"::error file={path}::upstream {path} at {cand[:10]} no longer contains "
                      f"'{needle}'; runtime-env.sh depends on it. Review the upstream change before building.")

        if prev:
            cmp = json.loads(gh_api(f"repos/{up}/compare/{prev}...{cand}?per_page=1"))
            report["compare_status"] = cmp["status"]
            report["commit_count"] = cmp["ahead_by"]
            report["upstream_compare_url"] = f"https://github.com/{up}/compare/{prev[:12]}...{cand[:12]}"

            lines = gh_api(f"repos/{up}/compare/{prev}...{cand}?per_page=100", paginate=True,
                           jq=r'.commits[] | [.sha, (.commit.message | split("\n")[0])] | @tsv')
            commits = [l.split("\t", 1) for l in lines.splitlines() if "\t" in l]
            seen = set()
            for sha, subject in reversed(commits):  # newest first
                if sha in seen:
                    continue
                seen.add(sha)
                kind, breaking = classify(subject)
                group = report["groups"][kind]
                group["count"] += 1
                if len(group["items"]) < TOP_ITEMS:
                    group["items"].append({"sha": sha, "subject": subject})
                if breaking or "BREAKING" in subject:
                    report["breaking"].append({"sha": sha, "subject": subject})

            diff = run("git", "diff", "--no-renames", "--name-status", prev, cand, cwd=repo).stdout
            changed, added_dirs = set(), set()
            for line in diff.splitlines():
                status, path = line.split("\t", 1)
                changed.add(path)
                if status.startswith("A") and path.startswith(MIGRATIONS_DIR):
                    rest = path[len(MIGRATIONS_DIR):]
                    if "/" in rest:
                        added_dirs.add(rest.split("/", 1)[0])
            # A directory counts as added only if it did not exist before.
            old_dirs = set(run("git", "ls-tree", "--name-only", f"{prev}:{MIGRATIONS_DIR}", cwd=repo,
                               check=False).stdout.split())
            report["migrations_added"] = sorted(added_dirs - old_dirs)
            report["risky_files_changed"] = [f for f in RISKY_FILES if f in changed]
            report["schema_changed_without_migration"] = (SCHEMA_FILE in changed and not report["migrations_added"])

            old_env, new_env = set(), set()
            for path in ENV_EXAMPLES:
                old_env |= env_names(show(repo, prev, path) or "")
                new_env |= env_names(show(repo, cand, path) or "")
            report["env_example"] = {"added": sorted(new_env - old_env), "removed": sorted(old_env - new_env)}

    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in ("commit_count", "migrations_added", "env_example",
                                             "risky_files_changed")}, indent=2))
    return 2 if failed else 0


def link_prs(subject, up):
    return re.sub(r"\(#(\d+)\)", lambda m: f"([#{m.group(1)}](https://github.com/{up}/pull/{m.group(1)}))", subject)


def md_escape(s):
    return s.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def cmd_release(a):
    with open(a.report) as fh:
        r = json.load(fh)
    patches = json.loads(a.patches or "[]")
    up, ref, prev = r["upstream"], r["candidate"], r["previous_ref"]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    release = {
        "schema": 1,
        "image": a.image,
        "tag": a.tag,
        "digest": a.digest,
        "ref": ref,
        "upstream": up,
        "flavor": a.flavor,
        "previous_ref": prev,
        "upstream_compare_url": r["upstream_compare_url"],
        "commit_count": r["commit_count"],
        "smoke_test": "passed",
        "patches": [{"name": p["name"], "status": p["status"]} for p in patches],
        "migrations_added": r["migrations_added"],
        "env_example": r["env_example"],
        "risky_files_changed": r["risky_files_changed"],
        "published_at": now,
    }
    with open(a.release_json, "w") as fh:
        json.dump(release, fh, indent=2)
        fh.write("\n")

    pinned = f"{a.image}:{a.tag}@{a.digest}"
    mig, env, risky = r["migrations_added"], r["env_example"], r["risky_files_changed"]
    by_status = lambda s: [p["name"] for p in patches if p["status"] == s]
    applied, upstreamed = by_status("applied"), by_status("upstreamed")
    code = lambda s: f"`{s}`"

    out = []
    w = out.append
    if prev:
        w(f"Cal.diy built from [`{up}@{ref[:10]}`](https://github.com/{up}/commit/{ref}), "
          f"**{r['commit_count']} upstream commit{'s' if r['commit_count'] != 1 else ''}** since the previous release "
          f"([`{prev[:10]}`](https://github.com/{up}/commit/{prev})). [Compare on GitHub]({r['upstream_compare_url']}).")
        if r.get("compare_status") not in (None, "ahead"):
            w("")
            w(f"> [!WARNING]\n> The upstream comparison status is `{r['compare_status']}`, not `ahead`: the "
              "previous release is not an ancestor of this commit (force push or manual override). "
              "Review the history before upgrading.")
    else:
        w(f"Cal.diy built from [`{up}@{ref[:10]}`](https://github.com/{up}/commit/{ref}). "
          "This is the first release from this workflow, so there is no previous release to compare against.")
    w("")
    w(f"Flavor: `{a.flavor}`. Platform: `linux/amd64`.")
    w("")
    w("### Image")
    w("")
    w("```")
    w(pinned)
    w("```")
    w("")
    w("### At a glance")
    w("")
    w("| Area | Result |")
    w("|---|---|")
    w(f"| Database migrations | {f'**{len(mig)} new** (applied automatically on start, no down migrations)' if mig else 'none'} |")
    env_cell = ", ".join([f"+{code(n)}" for n in env["added"]] + [f"−{code(n)}" for n in env["removed"]]) or "no change"
    w(f"| Example env variables | {env_cell} |")
    w(f"| Build-sensitive files | {', '.join(code(f) for f in risky) if risky else 'unchanged'} |")
    patch_cell = []
    if applied:
        patch_cell.append(f"{len(applied)} applied")
    if upstreamed:
        patch_cell.append(f"**{len(upstreamed)} upstreamed** (skipped; a pull request drops them)")
    w(f"| Patches | {', '.join(patch_cell) or 'none'} |")
    w("| Smoke test | passed (migrations, runtime URL substitution, sign-up disabled, restart) |")
    w("")

    if mig:
        w("### Database migrations")
        w("")
        for m in mig:
            w(f"- [`{m}`](https://github.com/{up}/tree/{ref}/{MIGRATIONS_DIR}{m})")
        w("")
    if r.get("schema_changed_without_migration"):
        w(f"> [!NOTE]\n> `{SCHEMA_FILE}` changed without a new migration (comments, formatting or generator settings).")
        w("")
    if env["added"] or env["removed"]:
        w("### Environment variables")
        w("")
        w(f"Names diffed across {', '.join(code(f) for f in ENV_EXAMPLES)} (commented-out entries included).")
        w("")
        if env["added"]:
            w(f"- Added: {', '.join(code(n) for n in env['added'])}")
        if env["removed"]:
            w(f"- Removed: {', '.join(code(n) for n in env['removed'])}")
        w("")
    if patches:
        w("### Patches")
        w("")
        w("| Patch | Status |")
        w("|---|---|")
        for p in patches:
            w(f"| `{p['name']}` | {p['status']} |")
        w("")
    if r["breaking"]:
        w("### Breaking changes (as marked upstream)")
        w("")
        for c in r["breaking"]:
            w(f"- {md_escape(link_prs(c['subject'], up))} ({c['sha'][:7]})")
        w("")
    if r["commit_count"]:
        w("### Changes")
        w("")
        for key, title in GROUPS:
            g = r["groups"][key]
            if not g["count"]:
                continue
            w(f"<details{' open' if key in ('feat', 'fix') else ''}><summary><b>{title}</b> ({g['count']})</summary>")
            w("")
            for c in g["items"]:
                w(f"- {md_escape(link_prs(c['subject'], up))} ({c['sha'][:7]})")
            if g["count"] > len(g["items"]):
                w(f"- … and {g['count'] - len(g['items'])} more")
            w("")
            w("</details>")
            w("")
    w("### Upgrade notes")
    w("")
    w("1. Back up the database. Migrations run automatically on start and cannot be rolled back.")
    if env["added"] or env["removed"]:
        w("2. Review the environment variable changes above against your configuration.")
    else:
        w("2. No example environment variables changed.")
    w(f"3. Set the image to `{pinned}` and recreate the container (`docker compose up -d`).")
    w("4. Check `GET /api/version` and the container logs.")
    w("")
    w("Rollback: previous image digest plus a database restore.")
    w("")
    w(f"Machine-readable summary: `release.json` (schema 1). Built by [this workflow run]({a.run_url}).")

    with open(a.notes, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--upstream", required=True)
    r.add_argument("--candidate", required=True)
    r.add_argument("--previous", default="")
    r.add_argument("--out", required=True)
    s = sub.add_parser("release")
    for name in ("report", "image", "tag", "digest", "flavor", "run-url", "release-json", "notes"):
        s.add_argument(f"--{name}", required=True)
    s.add_argument("--patches", default="[]")
    a = p.parse_args()
    return cmd_report(a) if a.cmd == "report" else cmd_release(a)


if __name__ == "__main__":
    sys.exit(main())
