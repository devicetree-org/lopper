#!/usr/bin/env python3
# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Sync upstream public material (Linux kernel, Xilinx vendor kernel,
Zephyr) into `lopper/data/upstream/` according to
`scripts/upstream-manifest.yaml`.

Modelled after U-Boot's `dts/upstream/` mechanism — no network is
performed by this script. The caller supplies upstream checkouts via
environment variables; the script copies the manifest's listed files
out of them, records the resolved tag + commit SHA in a `.source`
file, and exits. Reviewing the diff and committing is the maintainer's
job.

Usage:
    LINUX_SRC=/path/to/linux \\
    LINUX_XLNX_SRC=/path/to/linux-xlnx \\
    ZEPHYR_SRC=/path/to/zephyr \\
    scripts/sync-upstream.py

    # Or sync just one source:
    LINUX_SRC=/path/to/linux scripts/sync-upstream.py --only linux

    # Dry-run (show what would happen, change nothing):
    LINUX_SRC=/path/to/linux scripts/sync-upstream.py --dry-run

Reproducibility comes from the exact SHA, which is always recorded.
The script accepts whatever the checkout is currently at — exact tag,
post-tag commit, branch tip — and records the resolved tag-or-describe
string plus the SHA in `.source`. If a source is on a commit that
doesn't match the manifest's recommended `tag_pattern:`, the script
warns but proceeds. The only hard refusal is a dirty working tree, since
that would make the `.source` record lie.

Design context: sdt-from-linux-upstream-sync.md.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Repo-relative paths
REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / 'scripts' / 'upstream-manifest.yaml'
UPSTREAM_ROOT = REPO_ROOT / 'lopper' / 'data' / 'upstream'


def run_git(src_dir, *args):
    """Run `git <args...>` in src_dir; return stripped stdout or None on error."""
    try:
        out = subprocess.check_output(
            ['git', *args], cwd=src_dir, stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def validate_source(src_dir, tag_pattern, source_name=None):
    """Confirm src_dir is a clean git tree; resolve the most useful
    description of its current HEAD.

    Returns (describe_string, sha) on success; raises RuntimeError only
    when the tree is unusable (missing, not a git checkout, dirty).
    A non-tag HEAD or a tag that doesn't match `tag_pattern` is reported
    as a warning, not a failure — the SHA is the canonical pin, the
    describe string is human-readable context.
    """
    if not src_dir.is_dir():
        raise RuntimeError(f"source path does not exist or is not a directory: {src_dir}")
    if not (src_dir / '.git').exists():
        raise RuntimeError(f"source path is not a git checkout: {src_dir}")

    sha = run_git(src_dir, 'rev-parse', 'HEAD')
    if not sha:
        raise RuntimeError(f"cannot resolve HEAD in {src_dir}")

    # Refuse a dirty tree — the .source record would lie.
    dirty = run_git(src_dir, 'status', '--porcelain')
    if dirty:
        raise RuntimeError(
            f"working tree in {src_dir} is dirty; refuse to sync from "
            f"non-pristine source. Stash or clean it first.")

    # Prefer an exact tag match; fall back to `git describe --always` which
    # returns "<tag>-<n>-g<sha>" for post-tag commits or just the SHA if
    # the tree has no tags. Always returns something useful.
    exact_tag = run_git(src_dir, 'describe', '--tags', '--exact-match') or ''
    describe = exact_tag or run_git(src_dir, 'describe', '--tags', '--always', '--dirty') or sha[:12]

    prefix = f"[{source_name}] " if source_name else ""
    if exact_tag:
        if tag_pattern and not re.match(tag_pattern, exact_tag):
            print(f"  {prefix}note: tag {exact_tag!r} does not match "
                  f"recommended pattern {tag_pattern!r} — proceeding anyway",
                  file=sys.stderr)
    else:
        # Not on an exact tag; describe is "v6.12-15-gabcdef" or similar.
        print(f"  {prefix}note: HEAD is not on a tag (resolved as {describe!r}); "
              f"recording the exact SHA as the pin",
              file=sys.stderr)

    return describe, sha


def sync_source(source, dry_run=False):
    """Sync one source. Returns True on success, False on skip."""
    env_var = source['env_var']
    src_path = os.environ.get(env_var)
    if not src_path:
        print(f"  [{source['name']}] {env_var} not set — skipping")
        return False

    src_dir = Path(src_path).resolve()
    try:
        describe, sha = validate_source(src_dir, source.get('tag_pattern'),
                                        source_name=source['name'])
    except RuntimeError as e:
        print(f"  [{source['name']}] error: {e}", file=sys.stderr)
        return False

    target = UPSTREAM_ROOT / source['target_subdir']
    print(f"  [{source['name']}] {src_dir}  ref={describe}  commit={sha[:12]}")
    print(f"  [{source['name']}] target: {target.relative_to(REPO_ROOT)}")

    if dry_run:
        print(f"  [{source['name']}] DRY-RUN: would copy {len(source['files'])} file(s)")
        for rel in source['files']:
            present = "✓" if (src_dir / rel).exists() else "MISSING"
            print(f"    {present}  {rel}")
        return True

    # Wipe and recreate target so deleted-upstream files don't linger
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    missing = []
    copied = 0
    for rel in source['files']:
        src_file = src_dir / rel
        if not src_file.is_file():
            missing.append(rel)
            continue
        dst_file = target / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1

    # Always write .source even if some files were missing, so the audit
    # trail is intact. The error exit below makes the script fail the
    # build, but the partial copy + record stays for inspection.
    source_record = target / '.source'
    source_record.write_text(
        f"repo:       {source.get('description', source['name'])}\n"
        f"source_dir: {src_dir}\n"
        f"ref:        {describe}\n"
        f"commit:     {sha}\n"
        f"manifest:   scripts/upstream-manifest.yaml\n"
        f"files:      {copied}/{len(source['files'])} copied\n"
    )

    if missing:
        print(f"  [{source['name']}] WARNING: {len(missing)} file(s) "
              f"not present at this upstream tag:", file=sys.stderr)
        for rel in missing:
            print(f"    MISSING: {rel}", file=sys.stderr)
        return False

    print(f"  [{source['name']}] copied {copied} file(s); .source written")
    return True


def load_manifest():
    try:
        from ruamel.yaml import YAML
    except ImportError:
        sys.exit("error: ruamel.yaml is required (pip install ruamel.yaml)")
    yaml = YAML(typ='safe')
    with open(MANIFEST_PATH) as fh:
        return yaml.load(fh)


def assemble_sources(manifest):
    """Flatten the board-scoped manifest into per-source sync units.

    The manifest lists files per board, grouped by source. Sync,
    however, operates per source (one target_subdir, one .source
    provenance record). So we take the union of every board's files
    for each source — a file shared by multiple boards is copied once.

    Returns a list of source dicts (name, env_var, description,
    tag_pattern, target_subdir, files), in manifest source order.
    """
    sources_meta = manifest.get('sources') or {}
    boards = manifest.get('boards') or {}

    per_source_files = {name: [] for name in sources_meta}
    seen = {name: set() for name in sources_meta}
    for bname, bdef in boards.items():
        files_by_source = (bdef or {}).get('files') or {}
        for sname, flist in files_by_source.items():
            if sname not in sources_meta:
                sys.exit(f"error: board {bname!r} references unknown "
                         f"source {sname!r} (not in manifest 'sources:')")
            for f in (flist or []):
                if f not in seen[sname]:
                    seen[sname].add(f)
                    per_source_files[sname].append(f)

    result = []
    for name, meta in sources_meta.items():
        result.append({
            'name': name,
            'env_var': meta['env_var'],
            'description': meta.get('description', name),
            'tag_pattern': meta.get('tag_pattern'),
            'target_subdir': meta['target_subdir'],
            'files': sorted(per_source_files[name]),
        })
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--only', metavar='SOURCE',
                   help='Sync just this source (e.g. linux, zephyr, linux-xlnx)')
    p.add_argument('-n', '--dry-run', action='store_true',
                   help='Show what would be done without changing anything')
    args = p.parse_args()

    manifest = load_manifest()
    sources = assemble_sources(manifest)

    if args.only:
        sources = [s for s in sources if s['name'] == args.only]
        if not sources:
            sys.exit(f"error: --only {args.only} matched no source in manifest")

    print(f"Manifest: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(f"Target:   {UPSTREAM_ROOT.relative_to(REPO_ROOT)}")
    print(f"Mode:     {'DRY-RUN' if args.dry_run else 'SYNC'}")
    print()

    ok = 0
    skipped = 0
    failed = 0
    for source in sources:
        result = sync_source(source, dry_run=args.dry_run)
        if result is True:
            ok += 1
        elif result is False and os.environ.get(source['env_var']) is None:
            skipped += 1
        else:
            failed += 1
        print()

    print(f"summary: {ok} ok, {skipped} skipped (env var unset), {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
