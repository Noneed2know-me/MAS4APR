#!/usr/bin/env python3
"""
split_defects4j.py
==================
Split Defects4J V1.2 and V2.0 bugs into two categories based on the developer
patch scope:

  • single-file patch   the fix touches exactly ONE source (.java) file
  • multi-file  patch   the fix touches TWO OR MORE source (.java) files

The script works in two modes (controlled by --mode):

  1. patch-file  (default, no Defects4J installation required)
     Parses the unified-diff *.src.patch files that live inside the cloned
     Defects4J repository at:
         <d4j_root>/framework/projects/<Project>/patches/<id>.src.patch

  2. cli         (requires a working `defects4j` installation on PATH)
     Calls `defects4j query -p <Project> -q "classes.modified" -b <id>`
     for every active bug, which returns the list of modified source files
     directly from the Defects4J metadata.

Output
------
Three files are written to <output_dir>:
  single_file_patches.csv    bugs whose patch modifies exactly 1 .java file
  multi_file_patches.csv     bugs whose patch modifies 2+ .java files
  split_summary.json         machine-readable summary of every bug + counts

Usage examples
--------------
# Mode 1 - parse patch files from a cloned Defects4J repo
python split_defects4j.py \\
    --d4j-root /path/to/defects4j \\
    --output   ./results

# Mode 2 - use the defects4j CLI (must be on PATH)
python split_defects4j.py \\
    --mode cli \\
    --output ./results

# Restrict to V1.2 projects only
python split_defects4j.py \\
    --d4j-root /path/to/defects4j \\
    --version v1.2 \\
    --output ./results

# Restrict to V2.0 projects only
python split_defects4j.py \\
    --d4j-root /path/to/defects4j \\
    --version v2.0 \\
    --output ./results
"""

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("split_d4j")



# V1.2 projects (395 bugs across these 5 projects)
V1_2_PROJECTS: Set[str] = {"Chart", "Closure", "Lang", "Math", "Time"}

# V2.0 adds the following new projects (854 total bugs including V1.2 ones)
V2_0_NEW_PROJECTS: Set[str] = {
    "Cli",
    "Codec",
    "Collections",
    "Compress",
    "Csv",
    "Gson",
    "JacksonCore",
    "JacksonDatabind",
    "JacksonXml",
    "Jsoup",
    "JxPath",
    "Mockito",
}

ALL_PROJECTS: Set[str] = V1_2_PROJECTS | V2_0_NEW_PROJECTS

# Map version flag → set of projects to include
VERSION_MAP: Dict[str, Set[str]] = {
    "v1.2": V1_2_PROJECTS,
    "v2.0": ALL_PROJECTS,   # V2.0 is a superset
    "all":  ALL_PROJECTS,
}


@dataclass
class BugRecord:
    """Holds metadata about a single Defects4J bug entry."""
    project:        str
    bug_id:         int
    version_tag:    str          # 'v1.2' or 'v2.0'
    modified_files: List[str] = field(default_factory=list)
    patch_type:     str = ""     # 'single-file' | 'multi-file' | 'unknown'
    num_files:      int = 0
    patch_file:     str = ""     # path to .src.patch (mode=patch-file)
    error:          str = ""     # non-empty if something went wrong

    def classify(self) -> None:
        """Set patch_type and num_files from modified_files."""
        self.num_files = len(self.modified_files)
        if self.num_files == 0:
            self.patch_type = "unknown"
        elif self.num_files == 1:
            self.patch_type = "single-file"
        else:
            self.patch_type = "multi-file"


# ---------------------------------------------------------------------------
# Patch-file parser
# ---------------------------------------------------------------------------

# Regex that matches the "--- a/path/to/File.java" or "+++ b/path/to/File.java"
# lines in a unified diff.  We look for lines that end in .java and are NOT
# test files (src/test/**).  To keep it simple we collect ALL changed .java
# source files (test files are filtered out separately).
_DIFF_FILE_RE = re.compile(
    r'^(?:---|\+\+\+)\s+[ab]/(.+\.java)',
    re.MULTILINE
)

# We only want SOURCE files, not test files.  Defects4J's *.src.patch already
# excludes test changes, but we apply the filter defensively anyway.
_TEST_PATH_RE = re.compile(
    r'(?:src/test|test/java|src/it|src/testng)',
    re.IGNORECASE
)


def parse_patch_file(patch_path: Path) -> Tuple[List[str], str]:
    """
    Parse a Defects4J *.src.patch unified-diff file and return a deduplicated
    list of modified source (.java) file paths, plus any error string.

    Returns
    -------
    (modified_files, error_message)
    """
    try:
        text = patch_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], str(exc)

    raw_paths = _DIFF_FILE_RE.findall(text)

    # Deduplicate while preserving order; filter out /dev/null placeholders
    seen: Set[str] = set()
    unique: List[str] = []
    for p in raw_paths:
        p = p.strip()
        if p == "/dev/null":
            continue
        # Normalise: strip leading "a/" or "b/" that some tools leave behind
        p = re.sub(r'^[ab]/', '', p)
        # Filter test paths
        if _TEST_PATH_RE.search(p):
            continue
        if p not in seen:
            seen.add(p)
            unique.append(p)

    return unique, ""


def _run_d4j_query(project: str, bug_id: int) -> Tuple[List[str], str]:
    """
    Call `defects4j query` to get the list of modified source files for a bug.

    Returns (file_list, error_string).
    """
    cmd = [
        "defects4j", "query",
        "-p", project,
        "-q", "classes.modified",
        "-b", str(bug_id),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return [], "defects4j binary not found on PATH"
    except subprocess.TimeoutExpired:
        return [], f"timeout querying {project}-{bug_id}"

    if result.returncode != 0:
        return [], result.stderr.strip()

    # Output is one class/file per line, e.g.:
    #   org.apache.commons.lang3.StringUtils
    # Convert dotted class name → relative file path
    files = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Convert class name to file path: org.Foo → org/Foo.java
        path = line.replace(".", "/") + ".java"
        files.append(path)

    return files, ""


def read_active_bugs(project_dir: Path) -> List[int]:
    """
    Read bug IDs from <project_dir>/active-bugs.csv.
    Returns a list of integer bug IDs.
    """
    csv_path = project_dir / "active-bugs.csv"
    if not csv_path.exists():
        log.warning("  No active-bugs.csv at %s", csv_path)
        return []

    bug_ids: List[int] = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # The first column is the bug id
                bid = row.get("bug.id") or row.get("bug_id") or list(row.values())[0]
                try:
                    bug_ids.append(int(bid.strip()))
                except (ValueError, AttributeError):
                    continue
    except OSError as exc:
        log.error("  Cannot read %s: %s", csv_path, exc)

    return sorted(bug_ids)



def collect_patch_file_mode(
    d4j_root: Path,
    projects: Set[str],
    version_tag: str,
) -> List[BugRecord]:
    """
    Walk the Defects4J project directories and parse *.src.patch files.
    """
    records: List[BugRecord] = []
    projects_root = d4j_root / "framework" / "projects"

    if not projects_root.exists():
        log.error(
            "Directory not found: %s\n"
            "Make sure --d4j-root points to the root of a cloned Defects4J repository.",
            projects_root,
        )
        sys.exit(1)

    # Discover which project directories actually exist
    available = {p.name for p in projects_root.iterdir() if p.is_dir()}
    target = projects & available
    missing = projects - available

    if missing:
        log.warning("Projects not found in repo (skipped): %s", sorted(missing))
    if not target:
        log.error("No target projects found under %s", projects_root)
        sys.exit(1)

    log.info("Processing %d project(s): %s", len(target), sorted(target))

    for project in sorted(target):
        proj_dir = projects_root / project
        patches_dir = proj_dir / "patches"
        bug_ids = read_active_bugs(proj_dir)

        if not bug_ids:
            log.warning("  [%s] No active bugs found", project)
            continue

        log.info("  [%s] %d active bugs", project, len(bug_ids))
        found_patches = 0

        for bid in bug_ids:
            patch_path = patches_dir / f"{bid}.src.patch"
            rec = BugRecord(
                project=project,
                bug_id=bid,
                version_tag=version_tag,
                patch_file=str(patch_path),
            )

            if not patch_path.exists():
                rec.error = f"patch file not found: {patch_path}"
                log.debug("    Bug %s-%d: patch file missing", project, bid)
            else:
                found_patches += 1
                rec.modified_files, rec.error = parse_patch_file(patch_path)

            rec.classify()
            records.append(rec)

        log.info(
            "  [%s] patch files found: %d / %d",
            project, found_patches, len(bug_ids)
        )

    return records


# Output writers
CSV_FIELDNAMES = [
    "project", "bug_id", "version_tag", "patch_type",
    "num_files", "modified_files", "patch_file", "error",
]


def write_csv(records: List[BugRecord], path: Path) -> None:
    """Write a list of BugRecords to a CSV file."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for rec in records:
            row = asdict(rec)
            row["modified_files"] = "|".join(rec.modified_files)
            writer.writerow({k: row[k] for k in CSV_FIELDNAMES})
    log.info("Wrote %d records → %s", len(records), path)


def write_summary_json(
    records: List[BugRecord],
    path: Path,
    mode: str,
    version: str,
) -> None:
    """Write a JSON summary with per-project and overall statistics."""

    per_project: Dict[str, Dict] = defaultdict(
        lambda: {"single_file": [], "multi_file": [], "unknown": []}
    )
    for rec in records:
        key = f"{rec.project}-{rec.bug_id}"
        per_project[rec.project][
            rec.patch_type.replace("-", "_")
        ].append(key)

    overall = {
        "total_bugs":   len(records),
        "single_file":  sum(1 for r in records if r.patch_type == "single-file"),
        "multi_file":   sum(1 for r in records if r.patch_type == "multi-file"),
        "unknown":      sum(1 for r in records if r.patch_type == "unknown"),
    }

    summary = {
        "mode":         mode,
        "version":      version,
        "overall":      overall,
        "per_project":  {
            proj: {
                "single_file_count": len(v["single_file"]),
                "multi_file_count":  len(v["multi_file"]),
                "unknown_count":     len(v["unknown"]),
                "single_file_bugs":  v["single_file"],
                "multi_file_bugs":   v["multi_file"],
                "unknown_bugs":      v["unknown"],
            }
            for proj, v in sorted(per_project.items())
        },
        "all_bugs": [asdict(r) for r in records],
    }

    with path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    log.info("Wrote summary JSON → %s", path)


def print_report(records: List[BugRecord]) -> None:
    """Print a human-readable summary table to stdout."""
    single  = [r for r in records if r.patch_type == "single-file"]
    multi   = [r for r in records if r.patch_type == "multi-file"]
    unknown = [r for r in records if r.patch_type == "unknown"]

    print()
    print("=" * 72)
    print("  Defects4J Patch Splitting Summary")
    print("=" * 72)
    print(f"  Total bugs processed : {len(records)}")
    print(f"  Single-file patches  : {len(single)}")
    print(f"  Multi-file  patches  : {len(multi)}")
    print(f"  Unknown / no patch   : {len(unknown)}")
    print()

    # Per-project breakdown
    projects = sorted({r.project for r in records})
    col_w = max(len(p) for p in projects) + 2

    header = f"  {'Project':<{col_w}} {'Total':>7}  {'Single':>7}  {'Multi':>7}  {'Unknown':>8}"
    print(header)
    print("  " + "-" * (col_w + 38))

    per_proj: Dict[str, List[BugRecord]] = defaultdict(list)
    for r in records:
        per_proj[r.project].append(r)

    for proj in projects:
        recs  = per_proj[proj]
        s_cnt = sum(1 for r in recs if r.patch_type == "single-file")
        m_cnt = sum(1 for r in recs if r.patch_type == "multi-file")
        u_cnt = sum(1 for r in recs if r.patch_type == "unknown")
        print(
            f"  {proj:<{col_w}} {len(recs):>7}  {s_cnt:>7}  {m_cnt:>7}  {u_cnt:>8}"
        )

    print()

    # Highlight multi-file bugs
    if multi:
        print("  Multi-file patch bugs:")
        for r in sorted(multi, key=lambda x: (x.project, x.bug_id)):
            files_str = ", ".join(r.modified_files[:3])
            if len(r.modified_files) > 3:
                files_str += f"  (+{len(r.modified_files) - 3} more)"
            print(f"    {r.project}-{r.bug_id:>4d}  ({r.num_files} files)  {files_str}")
        print()

    print("=" * 72)


# Entry point
def main() -> None:
    parser = build_arg_parser()
    args   = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    # ----- Determine target project set -----
    if args.projects:
        projects = set(args.projects)
        version_tag = "custom"
        log.info("Using user-specified projects: %s", sorted(projects))
    else:
        projects = VERSION_MAP.get(args.version, ALL_PROJECTS)
        version_tag = args.version
        log.info(
            "Version filter '%s' → %d projects: %s",
            args.version, len(projects), sorted(projects),
        )

    # ----- Validate arguments -----
    if args.mode == "patch-file" and args.d4j_root is None:
        parser.error(
            "--d4j-root is required when --mode patch-file is used.\n"
            "Point it to the root of your cloned Defects4J repository."
        )

    # ----- Create output directory -----
    args.output.mkdir(parents=True, exist_ok=True)
    log.info("Output directory: %s", args.output.resolve())

    # ----- Collect records -----
    log.info("Mode: %s", args.mode)
    if args.mode == "patch-file":
        records = collect_patch_file_mode(args.d4j_root, projects, version_tag)
    else:
        records = collect_cli_mode(projects, version_tag)

    if not records:
        log.error("No bug records collected – nothing to write.")
        sys.exit(1)

    # ----- Classify and split -----
    single_records  = [r for r in records if r.patch_type == "single-file"]
    multi_records   = [r for r in records if r.patch_type == "multi-file"]
    unknown_records = [r for r in records if r.patch_type == "unknown"]

    # ----- Write outputs -----
    write_csv(single_records, args.output / "single_file_patches.csv")
    write_csv(multi_records,  args.output / "multi_file_patches.csv")

    if args.include_unknown and unknown_records:
        write_csv(unknown_records, args.output / "unknown_patches.csv")

    write_summary_json(
        records,
        args.output / "split_summary.json",
        mode=args.mode,
        version=version_tag,
    )

    # ----- Print human-readable report -----
    print_report(records)

    log.info("Done.")


if __name__ == "__main__":
    main()