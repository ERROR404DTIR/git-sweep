#!/usr/bin/env python3
"""git-sweep - safely clean up merged, stale and orphaned local git branches."""

import argparse
import subprocess
import sys
import time

__version__ = "1.0.0"
DEFAULT_PROTECTED = {"main", "master", "develop", "dev", "trunk", "release", "staging", "production"}
FIELDS = "%(refname:short)%09%(committerdate:unix)%09%(upstream:track)%09%(HEAD)%09%(worktreepath)"


class GitError(Exception):
    pass


def git(*args, check=True):
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.rstrip("\n")


def detect_base(explicit=None):
    if explicit:
        base = explicit
    else:
        ref = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False).strip()
        base = ref.split("/", 1)[1] if "/" in ref else ""
        if not base:
            for candidate in ("main", "master"):
                if git("rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}", check=False).strip():
                    base = candidate
                    break
    if not base:
        raise GitError("could not detect the base branch; pass --base NAME")
    if not git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}", check=False).strip():
        raise GitError(f"base branch '{base}' does not exist locally")
    return base


def collect(base, protected, older_than):
    merged = set(git("branch", "--merged", base, "--format=%(refname:short)").split())
    now = time.time()
    found = []
    for line in git("for-each-ref", f"--format={FIELDS}", "refs/heads").splitlines():
        parts = line.split("\t") + [""] * 5
        name, ts, track, head, worktree = parts[:5]
        if name == base or name in protected or head == "*" or worktree:
            continue
        age = int((now - int(ts)) // 86400) if ts.isdigit() else 0
        reasons = []
        if name in merged:
            reasons.append("merged")
        if "gone" in track:
            reasons.append("upstream gone")
        if older_than and age >= older_than and "merged" not in reasons:
            reasons.append(f"stale {age}d")
        if reasons:
            found.append({"name": name, "reasons": reasons, "age": age, "safe": name in merged})
    return sorted(found, key=lambda b: (not b["safe"], -b["age"], b["name"]))


def build_parser():
    p = argparse.ArgumentParser(
        prog="git-sweep",
        description="Find (and optionally delete) local branches that are merged, stale or whose remote is gone. "
                    "Dry run by default.",
    )
    p.add_argument("--base", help="branch to compare against (default: origin/HEAD, then main/master)")
    p.add_argument("--older-than", type=int, metavar="DAYS", help="also flag unmerged branches idle for DAYS+ days")
    p.add_argument("--protect", action="append", default=[], metavar="NAME", help="never touch this branch (repeatable)")
    p.add_argument("--fetch", action="store_true", help="run `git fetch --prune` first so 'upstream gone' is accurate")
    p.add_argument("--delete", action="store_true", help="actually delete (default is a dry run)")
    p.add_argument("--force", action="store_true", help="with --delete, also remove branches that are NOT merged")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("-V", "--version", action="version", version=f"git-sweep {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        git("rev-parse", "--is-inside-work-tree")
        if args.fetch:
            git("fetch", "--prune", check=False)
        base = detect_base(args.base)
        branches = collect(base, DEFAULT_PROTECTED | set(args.protect), args.older_than)
    except GitError as err:
        print(f"git-sweep: {err}", file=sys.stderr)
        return 2

    if not branches:
        print(f"Nothing to sweep (base: {base}).")
        return 0

    print(f"Base branch: {base}\n")
    for b in branches:
        note = "" if b["safe"] else "  (not merged - needs --force)"
        print(f"  {b['name']:<36} {', '.join(b['reasons']):<22} {b['age']:>4}d{note}")

    eligible = [b for b in branches if b["safe"] or args.force]
    if not args.delete:
        print(f"\nDry run: {len(eligible)} branch(es) would be deleted. Re-run with --delete to apply.")
        return 0
    if not eligible:
        print("\nNothing eligible. Unmerged branches need --force.")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to delete without a terminal; pass -y to confirm.", file=sys.stderr)
            return 2
        if input(f"\nDelete {len(eligible)} branch(es)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    failed = 0
    for b in eligible:
        try:
            git("branch", "-D", b["name"])
            print(f"✔ deleted {b['name']}")
        except GitError as err:
            print(f"✘ {b['name']}: {err}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
