import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Union


def _run_cmd(cmd, cwd=None, timeout=1800):
    p = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        text=True,
        check=False  # Allow test failures 
    )
    return p.stdout


def _parse_failed_tests(defects4j_test_output: str) -> List[str]:
    """
    Parse failed tests from defects4j test output.
    Typical lines include:
      - "Failing tests: 2"
      - "  - org.foo.BarTest::testX"
    """
    if isinstance(defects4j_test_output, (bytes, bytearray)):
        defects4j_test_output = defects4j_test_output.decode("utf-8", errors="replace")

    failed = []
    # common pattern: "  - <test>"
    for line in defects4j_test_output.splitlines():
        line = line.strip()
        if line.startswith("- "):
            failed.append(line[2:].strip())
    # some versions might print "  - " or "* " etc; fallback:
    if not failed:
        for line in defects4j_test_output.splitlines():
            m = re.search(r"^\s*-\s+(.+::.+)$", line)
            if m:
                failed.append(m.group(1).strip())
    return failed


def _bug_id_to_proj_bid(bug_id: str) -> (str, str):
    """
    Convert 'Chart-5' -> ('Chart', '5')
    """
    if bug_id.lower().endswith(".json"):
        bug_id = bug_id[:-5]

    m = re.match(r"^([A-Za-z0-9_]+)-(\d+)$", bug_id.strip())
    if not m:
        raise ValueError(f"Invalid bug_id format: {bug_id!r}. Expected like 'Chart-5'.")
    return m.group(1), m.group(2)


def extract_defects4j_tests_and_patches(
    input_json_path: str,
    output_json_path: str,
    defect4j_cmd: str,
    keep_workdirs: bool = False,
) -> Dict[str, Dict[str, Union[List[str], str]]]:
    """
    Given a JSON file with records containing 'bug_id', extract:
      - test_case: failing tests (from running defects4j test on buggy version)
      - patch: diff between buggy and fixed versions (Defect4J-style patch)

    Output JSON format:
    {
      "Chart-5": {
        "test_case": ["...::..."],
        "patch": "diff --git ..."
      },
      ...
    }

    Notes:
    - Requires Defect4J v1.2 installed and accessible via `defects4j` command.
    - Uses `defects4j checkout` for buggy/fixed and `git diff` to compute patch.
    """

    with open(input_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Accept list-of-records OR dict keyed by something
    records: List[Dict[str, Any]]
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # if dict contains list under a key, try to detect; otherwise treat dict values as records
        if all(isinstance(v, dict) for v in data.values()):
            records = list(data.values())
            print(records)
        else:
            raise ValueError("Unsupported JSON structure. Provide a list of records or dict of records.")
    else:
        raise ValueError("Unsupported JSON structure.")

    result: Dict[str, Dict[str, Union[List[str], str]]] = {}

    base_tmp = tempfile.mkdtemp(prefix="defects4j_extract_")
    try:
        for rec in records:
            print(f'Current record: {rec}')
            bug_id = rec
            print(f'Processing bug_id: {bug_id}')
            if not bug_id:
                continue

            print('********* Now processing bug_id:{} *********'.format(bug_id))
            proj, bid = _bug_id_to_proj_bid(bug_id)
            print(f'Project: {proj}, Bug ID: {bid}')

            # Create per-bug temp dirs
            buggy_dir = os.path.join(base_tmp, f"{bug_id}_buggy")
            fixed_dir = os.path.join(base_tmp, f"{bug_id}_fixed")
            os.makedirs(buggy_dir, exist_ok=True)
            os.makedirs(fixed_dir, exist_ok=True)

            # Checkout buggy and fixed
            _run_cmd([defect4j_cmd, "checkout", "-p", proj, "-v", f"{bid}b", "-w", buggy_dir])
            _run_cmd([defect4j_cmd, "checkout", "-p", proj, "-v", f"{bid}f", "-w", fixed_dir])

            # 1) Extract failing tests from buggy version
            # defects4j test returns non-zero on failure; capture output safely
            print('********* Now loading the tests for bug_id:{} *********'.format(bug_id))
            try:
                out = _run_cmd([defect4j_cmd, "test"], cwd=buggy_dir)
            except subprocess.CalledProcessError as e:
                out = e.stdout or ""
            failed_tests = _parse_failed_tests(out)

            print('********* Now loading the ground-truth patch for bug_id:{} *********'.format(bug_id))
            # 2) Extract patch: diff buggy -> fixed
            # Both checkouts are git repos; use git diff between trees.
            # We'll compute diff by copying buggy repo and replacing files, but simplest:
            # Use "diff -u" between dirs (portable) OR git diff using paths.
            # Here: use `diff -ruN` for correctness without relying on git history alignment.
            try:
                patch_text = _run_cmd(["diff", "-u", buggy_dir, fixed_dir], timeout=1800)
            except subprocess.CalledProcessError as e:
                # diff exits code 1 when differences exist; that is expected.
                patch_text = e.stdout or ""

            result[bug_id] = {
                "test_case": failed_tests,
                "patch": patch_text,
            }

            # Optionally clean per-bug dirs
            if not keep_workdirs:
                shutil.rmtree(buggy_dir, ignore_errors=True)
                shutil.rmtree(fixed_dir, ignore_errors=True)

        # Save output
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return result

    finally:
        if not keep_workdirs:
            shutil.rmtree(base_tmp, ignore_errors=True)

extract_defects4j_tests_and_patches(
    input_json_path="/MAS4APR/failed_project.json",
    output_json_path="/MAS4APR/bug_tests_patches.json",
    defect4j_cmd="/MAS4APR/defects4j/framework/bin/defects4j"
)