import tiktoken
from cpgqls_client import CPGQLSClient
import requests
from langchain.tools import StructuredTool
from datetime import datetime
from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import io
import json
import multiprocessing as mp
import random
import re
import time
import types
from typing import Any, Dict, List, Optional, Tuple, get_args, get_origin

from src.config import JOERN_ADDR

server_endpoint = JOERN_ADDR
client = CPGQLSClient(server_endpoint)
proj_name_gl = ""


def open_proj(proj_name: str) -> str:
    """
    Open the current analysing project by proj_name.

    @param proj_name: The name of project to open. "
    @return: The result of the request execution.

    """

    if not proj_name.endswith("\n"):
        print(proj_name)
    proj_name = proj_name.strip()
    query = f'open("{proj_name}")'
    print(query)
    result = client.execute(query)
    global proj_name_gl
    proj_name_gl = proj_name
    return result['stdout']


def identify_variable(input_string: str) -> str:
    """
    Identifies variable definitions and usages for a given variable name.

    @param input_string: A string containing both the variable name and file name,
                         separated by a comma. Format: variable_name, file_name
    @return: The result of the query execution.
    """
    variable_name, file_name = input_string.split(',')
    variable_name = variable_name.strip()
    file_name = file_name.strip()
    query = f'cpg.identifier.name("{variable_name}").filter(_.location.filename.matches(".*{file_name}.*")).map(n=>List(n.code, n.typeFullName, n.start.dump)).take(3).l'
    print(query)
    result = client.execute(query)
    return result['stdout']


def trace_method_usage(method_name: str) -> str:
    """
    Traces the usage of a specific method in the code.

    @param method_name: The name of the method to trace.
    @return: The result of the query execution.
    """
    query = f'cpg.call.name("{method_name}").map(n=>List(n.code, n.methodFullName)).l'
    print(query)
    result = client.execute(query)
    return slim_joern_token(result['stdout'])


def find_method_in_file(input_string: str) -> str:
    """
    Finds a specific method within a given file and retrieves its location and the whole method code. DO NOT Add any quota marks(") to the input parameter because the tool call will process it.

    @param input_string: A string containing both the method name and file name, separated by a comma. method_name: The name of the method to find. file_name: The name of the file to search within (only the file name, not the file path). Format: method_name, file_name.
    @return: The result of the query execution.

    input_string Example: visit, CheckSideEffects.java
    """
    method_name, file_name = input_string.split(',')
    method_name = method_name.strip()
    file_name = file_name.strip()
    query = (
        f'cpg.method.name("{method_name}")'
        f'.filter(_.location.filename.matches(".*{file_name}"))'
        f'.map(m => (m.location.filename, m.start.dump))'
        f'.l'
    )
    print(query)
    result = client.execute(query)
    return result['stdout']


def analyze_method_details(input_string: str) -> str:
    """
    Identifies various aspects of a given method in a file. DO NOT Add any quota marks(") to the input parameter because the tool call will process it.

    @param input_string: A string containing both the method name and file name, separated by a comma. method_name: The name of the method to find. file_name: The name of the file to search within (only the file name, not the file path). Format: method_name, file_name. Example: visit, CheckSideEffects.java

    @return: The result of the query execution, including:
        - Method name and parameter types
        - Filename where the method is located
        - All throw statements within the method
        - All if statement conditions within the method
        - All method calls within the method
        - All variable assignments within the method
        - The return statement of the method
    """
    input_string = input_string.replace('"', '')
    method_name, file_name = input_string.split(',')
    method_name = method_name.strip()
    file_name = file_name.strip()
    query = f"""
    cpg.method("{method_name}").filter(_.location.filename.matches(".*{file_name}")).map{{ method =>
      (
        method.name,
        method.parameter.map(_.typeFullName).mkString(", "),
        method.location.filename,
        method.ast.isCall.name("throw.*").code.l,
        method.ast.isControlStructure.isIf.condition.code.l,
        method.ast.isCall.name.l,
        method.ast.isCall.name("<operator>.assignment").code.l,
        method.ast.isReturn.code.l
      )
    }}.l
    """
    print(query)
    result = client.execute(query)
    return result['stdout']

def find_class_loc(input_string: str) -> str:
    """
    Finds the location of a class with a given name.

    @param input_string: The name of the class to locate
    @return: A string containing the location/full type name of the class
    """
    class_name = input_string.strip().replace('"', '')

    query = (
        f'cpg.identifier.name("{class_name}").typeFullName.take(1).l'
    )
    print(query)
    result = client.execute(query)
    return result['stdout']

def identify_class(input_string: str) -> str:
    """
    Identifies the class code matches the given search string for a class name.

    @param input_string: The name of the class
    @return: A string containing the class codes
    """

    class_name = input_string.strip().replace('"', '')

    query = (
        f'cpg.typeDecl.fullName(".*{class_name}.*").astChildren.code.dedup.l'
    )
    print(query)
    result = client.execute(query)
    return result['stdout']



def analyze_method_control_flow(method_name: str) -> str:
    """
    Analyzes the control flow (if, while, for, etc.) structures within a specific method.

    @param method_name: The name of the method to analyze.
    @return: The code of the control flow structures within the method.
    """
    query = f'cpg.method.name("{method_name}").ast.isControlStructure.code.l'
    print(query)
    result = client.execute(query)
    return result['stdout']


def get_imports(file_name: str) -> str:
    """
    Retrieves all import statements from a specific file.

    @param file_name: The name of the file to search within (only the file name, not the file path).
    @return: The code of all import statements within the file.
    """
    query = (
        f'cpg.imports'
        f'.filter(_.location.filename.matches(".*{file_name}"))'
        f'.map(m => (m.code))'
        f'.l'
    )
    print(query)
    result = client.execute(query)
    return result['stdout']

def close_proj(proj_name: str) -> str:
    """
    Close the current analysing project by proj_name.

    @param proj_name: The name of project to close. Example format: "Chart-1_buggy"
    @return: The result of the request execution.

    Example: close_proj("Chart-1_buggy")
    """
    if proj_name.endswith("\n"):
        proj_name.replace("\n", "")
    if not proj_name.endswith("\n"):
        print(proj_name)

    query = f'close("{proj_name}")'
    print(query)
    result = client.execute(query)
    return result['stdout']


#def example_patch_search(input_string: str) -> str:
    """
    Search for the most similar (by similarity rate) fix pattern example in a Knowledge Base and return it.

    @param input_string: Buggy code concatenate with Root cause. input example:  public boolean equals(Object obj) \n\n    if (obj == this) \n   return true, Root Cause: The `equals` method in the `ShapeList` class is calling `super.equals(obj)` which likely only checks the reference equality or fields defined in the superclass, rather than checking the properties specific to `ShapeList`. This would lead to instances that may contain the same shapes being considered unequal if the parent class does not override `equals` appropriately for deep equality checks.
    @return: The most similar fix pattern, including BuggyCode, FixedCode, RootCause

    """
    url = "http://localhost:5000/search"
    data = {
        "query": input_string,
        "n": 1,
        "threshold": 0.6
    }
    start_time = datetime.now()
    response = requests.post(url, json=data)
    # print(response.json())
    end_time = datetime.now()
    time_difference = end_time - start_time
    print(f"Time cost for retrieving: {time_difference}")
    close_proj(proj_name=proj_name_gl)
    if "'similarity'" in str(response.json()):
        print("example found")
        with open("/home/apr/output/searched_example.txt", "a", encoding="utf-8") as f:
            f.write(proj_name_gl + "\n")

    return response.json()

def slim_joern_token(joern_response):
    token_upper_limit = 10000
    response_list = joern_response.split('\n')
    slim_res_list = []
    for line in response_list:
        curr_line_token_cnt = num_tokens_from_string(line)
        token_upper_limit -= curr_line_token_cnt
        if token_upper_limit <= 0:
            break
        slim_res_list.append(line)
    return '\n'.join(slim_res_list)


def num_tokens_from_string(string: str) -> int:
    encoding_name = "cl100k_base"
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens





# -----------------------------
# Result structures
# -----------------------------
@dataclasses.dataclass
class InvariantFailure:
    function: str
    kind: str
    message: str
    sample: Optional[dict] = None


@dataclasses.dataclass
class InvariantReport:
    ok: bool
    summary: Dict[str, Any]
    failures: List[InvariantFailure]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "failures": [dataclasses.asdict(f) for f in self.failures],
            "warnings": list(self.warnings),
        }


# -----------------------------
# Helpers: safe execution
# -----------------------------
_SAFE_BUILTINS = {
    # basic types / constructors
    "None": None,
    "True": True,
    "False": False,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "sorted": sorted,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "all": all,
    "any": any,
    "zip": zip,
    "map": map,
    "filter": filter,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "repr": repr,
    "print": print,  # captured; still useful for debugging invariants
    # exceptions
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AssertionError": AssertionError,
}

# crude denylist for obviously dangerous builtins/modules usage inside source
_DENY_TOKENS = (
    "import os",
    "import sys",
    "import subprocess",
    "import socket",
    "import requests",
    "import urllib",
    "import http",
    "open(",
    "__import__(",
    "eval(",
    "exec(",
)


def _has_imports(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return True
    return False


def _extract_invariant_exprs(source: str) -> List[str]:
    exprs: List[str] = []
    for line in source.splitlines():
        m = re.match(r"\s*#\s*INVARIANT\s*:\s*(.+)\s*$", line)
        if m:
            exprs.append(m.group(1))
    return exprs


def _stable_hash(obj: Any) -> str:
    try:
        raw = json.dumps(obj, sort_keys=True, default=repr).encode("utf-8")
    except Exception:
        raw = repr(obj).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _shallow_globals_snapshot(g: Dict[str, Any]) -> Dict[str, str]:
    snap: Dict[str, str] = {}
    for k, v in g.items():
        if k.startswith("__"):
            continue
        # keep it shallow & stable (repr can still be unstable but ok for a “tripwire”)
        try:
            snap[k] = _stable_hash(v)
        except Exception:
            snap[k] = _stable_hash(repr(v))
    return snap


# -----------------------------
# Input generation
# -----------------------------
def _rand_str(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-\"'./"
    n = rng.randint(0, 30)
    return "".join(rng.choice(alphabet) for _ in range(n))


def _gen_value_for_type(tp: Any, rng: random.Random) -> Any:
    """
    Best-effort value generation for common annotations.
    Falls back to a reasonable primitive if unknown.
    """
    if tp is None or tp is type(None):
        return None

    origin = get_origin(tp)
    args = get_args(tp)

    # Optional[T] is Union[T, NoneType]
    if origin is None and hasattr(tp, "__module__") and tp.__module__ == "typing":
        # typing objects sometimes appear without origin resolution
        pass

    if origin is list and args:
        return [_gen_value_for_type(args[0], rng) for _ in range(rng.randint(0, 5))]
    if origin is dict and len(args) == 2:
        return {
            _gen_value_for_type(args[0], rng): _gen_value_for_type(args[1], rng)
            for _ in range(rng.randint(0, 5))
        }
    if origin is tuple and args:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_gen_value_for_type(args[0], rng) for _ in range(rng.randint(0, 5)))
        return tuple(_gen_value_for_type(a, rng) for a in args)
    if origin is set and args:
        return set(_gen_value_for_type(args[0], rng) for _ in range(rng.randint(0, 5)))

    # Union
    if origin is getattr(types, "UnionType", None) or origin is getattr(__import__("typing"), "Union", None):
        # pick one option
        if args:
            pick = rng.choice(list(args))
            return _gen_value_for_type(pick, rng)

    # primitives
    if tp in (str,):
        return _rand_str(rng)
    if tp in (int,):
        return rng.randint(-100, 100)
    if tp in (float,):
        return rng.uniform(-100.0, 100.0)
    if tp in (bool,):
        return bool(rng.randint(0, 1))

    # fallback: try to call a no-arg constructor
    try:
        return tp()  # type: ignore[misc]
    except Exception:
        # final fallback
        return _rand_str(rng)


def _build_call_samples(
    fn: Any,
    hints: Dict[str, Any],
    rng: random.Random,
    n_samples: int = 5,
) -> List[Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    import inspect

    sig = inspect.signature(fn)
    params = list(sig.parameters.values())

    samples: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    for _ in range(n_samples):
        args: List[Any] = []
        kwargs: Dict[str, Any] = {}
        for p in params:
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                # skip fuzzing *args/**kwargs for now
                continue

            anno = hints.get(p.name, p.annotation)
            if anno is inspect._empty:
                # no hint -> guess
                val = _rand_str(rng) if p.default is inspect._empty else p.default
            else:
                val = _gen_value_for_type(anno, rng)

            if p.default is not inspect._empty and rng.random() < 0.25:
                # sometimes use the default by omitting
                continue

            # prefer positional until we hit keyword-only
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                args.append(val)
            elif p.kind == p.KEYWORD_ONLY:
                kwargs[p.name] = val

        samples.append((tuple(args), kwargs))
    return samples


# -----------------------------
# Worker process: run checks
# -----------------------------
def _worker_run_checks(patched_codes: str, seed: int, timeout_s: float) -> Dict[str, Any]:
    rng = random.Random(seed)
    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # quick deny-token scan (doesn't block, just flags)
    for tok in _DENY_TOKENS:
        if tok in patched_codes:
            warnings.append(f"Potentially dangerous token detected: {tok!r}")

    # parse
    try:
        tree = ast.parse(patched_codes)
    except SyntaxError as e:
        return {
            "ok": False,
            "summary": {"error": "syntax_error", "detail": str(e)},
            "failures": [{"function": "<module>", "kind": "syntax", "message": str(e), "sample": None}],
            "warnings": warnings,
        }

    if _has_imports(tree):
        warnings.append("Imports detected in patched code (review carefully).")

    invariant_exprs = _extract_invariant_exprs(patched_codes)

    # sandbox globals
    sandbox_globals: Dict[str, Any] = {
        "__builtins__": dict(_SAFE_BUILTINS),
    }

    # execute module code
    try:
        exec(compile(tree, filename="<patched_codes>", mode="exec"), sandbox_globals, sandbox_globals)
    except Exception as e:
        return {
            "ok": False,
            "summary": {"error": "exec_error", "detail": repr(e)},
            "failures": [{"function": "<module>", "kind": "exec", "message": repr(e), "sample": None}],
            "warnings": warnings,
        }

    # find functions
    fns: Dict[str, Any] = {
        k: v for k, v in sandbox_globals.items()
        if callable(v) and getattr(v, "__code__", None) is not None and getattr(v, "__name__", "") == k
    }

    if not fns:
        warnings.append("No top-level functions found to check.")

    import inspect
    checked_calls = 0

    for name, fn in fns.items():
        # skip obviously tool/utility internals if desired; here we check all.
        hints = {}
        try:
            hints = getattr(fn, "__annotations__", {}) or {}
        except Exception:
            hints = {}

        samples = _build_call_samples(fn, hints, rng, n_samples=5)

        for args, kwargs in samples:
            start = time.time()
            out_buf = io.StringIO()
            g_before = _shallow_globals_snapshot(sandbox_globals)

            try:
                with contextlib.redirect_stdout(out_buf):
                    ret = fn(*args, **kwargs)
                stdout = out_buf.getvalue()
            except Exception as e:
                failures.append({
                    "function": name,
                    "kind": "call_exception",
                    "message": f"Exception on call: {repr(e)}",
                    "sample": {"args": repr(args), "kwargs": repr(kwargs)},
                })
                continue
            finally:
                if time.time() - start > timeout_s:
                    failures.append({
                        "function": name,
                        "kind": "timeout",
                        "message": f"Call exceeded timeout budget ({timeout_s}s).",
                        "sample": {"args": repr(args), "kwargs": repr(kwargs)},
                    })
                    continue

            checked_calls += 1
            g_after = _shallow_globals_snapshot(sandbox_globals)

            # stdout tripwire (not always bad, but commonly indicates behavior drift)
            if stdout.strip():
                warnings.append(f"{name}: produced stdout on a sample call (review if unintended).")

            # global mutation tripwire (best-effort)
            if g_before != g_after:
                warnings.append(f"{name}: global state appears to change across a call (review if unintended).")

            # return type check (best-effort)
            r_anno = hints.get("return", None)
            if r_anno is not None:
                # handle Optional/Union loosely
                origin = get_origin(r_anno)
                args_u = get_args(r_anno)
                ok_type = True
                if r_anno in (str, int, float, bool, list, dict, set, tuple):
                    ok_type = isinstance(ret, r_anno)
                elif origin is list:
                    ok_type = isinstance(ret, list)
                elif origin is dict:
                    ok_type = isinstance(ret, dict)
                elif origin is tuple:
                    ok_type = isinstance(ret, tuple)
                elif origin is set:
                    ok_type = isinstance(ret, set)
                elif origin is getattr(__import__("typing"), "Union", None) and args_u:
                    ok_type = any((a is type(None) and ret is None) or (a in (str, int, float, bool) and isinstance(ret, a))
                                  for a in args_u if a is not None)
                # if unknown annotation, skip
                if ok_type is False:
                    failures.append({
                        "function": name,
                        "kind": "return_type",
                        "message": f"Return value does not match annotated type {r_anno!r}. Got {type(ret)!r}.",
                        "sample": {"args": repr(args), "kwargs": repr(kwargs), "ret": repr(ret)},
                    })

            # determinism (best-effort): run twice
            try:
                out2 = io.StringIO()
                with contextlib.redirect_stdout(out2):
                    ret2 = fn(*args, **kwargs)
                if _stable_hash(ret) != _stable_hash(ret2):
                    warnings.append(f"{name}: non-deterministic return on identical inputs (might be intended).")
            except Exception:
                # don't double-fail here; already passed one call
                pass

            # custom invariants
            for inv in invariant_exprs:
                try:
                    env = {
                        "f": fn,
                        "args": args,
                        "kwargs": kwargs,
                        "ret": ret,
                        "stdout": stdout,
                        "g_before": g_before,
                        "g_after": g_after,
                    }
                    ok = bool(eval(inv, {"__builtins__": dict(_SAFE_BUILTINS)}, env))
                    if not ok:
                        failures.append({
                            "function": name,
                            "kind": "custom_invariant",
                            "message": f"INVARIANT failed: {inv}",
                            "sample": {"args": repr(args), "kwargs": repr(kwargs), "ret": repr(ret), "stdout": stdout},
                        })
                except Exception as e:
                    failures.append({
                        "function": name,
                        "kind": "custom_invariant_error",
                        "message": f"INVARIANT error for `{inv}`: {repr(e)}",
                        "sample": {"args": repr(args), "kwargs": repr(kwargs), "ret": repr(ret), "stdout": stdout},
                    })

    ok = len(failures) == 0
    return {
        "ok": ok,
        "summary": {
            "functions_checked": sorted(list(fns.keys())),
            "num_functions": len(fns),
            "num_calls_attempted": checked_calls,
            "num_failures": len(failures),
            "num_warnings": len(warnings),
        },
        "failures": failures,
        "warnings": warnings,
    }


# -----------------------------
# Public tool entrypoint
# -----------------------------
def invariant_check(patched_codes: str) -> str:
    """
    Tool entrypoint for an LLM agent.

    Args:
        patched_codes: Python source code as a string.

    Returns:
        JSON string with:
          - ok (bool)
          - summary
          - failures
          - warnings
    """
    # Keep checks bounded
    timeout_s = 2.0
    seed = 1337

    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue()

    def _runner():
        try:
            res = _worker_run_checks(patched_codes, seed=seed, timeout_s=timeout_s)
            q.put(res)
        except Exception as e:
            q.put({
                "ok": False,
                "summary": {"error": "internal_error", "detail": repr(e)},
                "failures": [{"function": "<tool>", "kind": "internal_error", "message": repr(e), "sample": None}],
                "warnings": [],
            })

    p = ctx.Process(target=_runner)
    p.start()
    p.join(timeout=5.0)

    if p.is_alive():
        p.terminate()
        p.join()
        report = InvariantReport(
            ok=False,
            summary={"error": "tool_timeout", "detail": "Invariant tool exceeded hard timeout."},
            failures=[InvariantFailure(function="<tool>", kind="tool_timeout", message="Hard timeout", sample=None)],
            warnings=[],
        )
        return json.dumps(report.to_dict(), indent=2)

    if q.empty():
        report = InvariantReport(
            ok=False,
            summary={"error": "no_result", "detail": "No result returned from worker."},
            failures=[InvariantFailure(function="<tool>", kind="no_result", message="Worker returned nothing", sample=None)],
            warnings=[],
        )
        return json.dumps(report.to_dict(), indent=2)

    return json.dumps(q.get(), indent=2)


def joern_syntax_check(patched_codes: str) -> Dict[str, Any]:
    """
    Syntax correctness gate using Joern parsing.

    Input: patched_codes (str) - a single code snippet (Java or C/C++).
    Output: dict with fields:
      - ok: bool
      - language: "java" | "c" | None
      - exit_code: int | None
      - stderr: str
      - stdout: str
      - notes: str

    Notes:
      - For Java, Joern parse success is a strong signal of syntactic correctness.
      - For C/C++, Joern can be tolerant; parse success is a weaker signal than a real compiler.
    """

    if not isinstance(patched_codes, str) or not patched_codes.strip():
        return {
            "ok": False,
            "language": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "Empty input string.",
            "notes": "Provide non-empty code.",
        }

    joern_parse = _find_joern_parse()
    if joern_parse is None:
        return {
            "ok": False,
            "language": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "joern-parse not found on PATH and JOERN_HOME not set.",
            "notes": "Install Joern and ensure joern-parse is accessible (PATH or JOERN_HOME/bin).",
        }

    # Try Java first, then C. (Still only one input param as requested.)
    attempts = [("java", "Snippet.java"), ("c", "snippet.c")]

    with tempfile.TemporaryDirectory(prefix="joern_syntax_") as tmpdir:
        srcdir = os.path.join(tmpdir, "src")
        os.makedirs(srcdir, exist_ok=True)

        for lang, filename in attempts:
            code_path = os.path.join(srcdir, filename)
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(patched_codes)

            outdir = os.path.join(tmpdir, f"out_{lang}")
            os.makedirs(outdir, exist_ok=True)

            # Run joern-parse on the directory; it will pick a frontend based on files.
            # Some Joern versions accept --output; others may use --out or default output.
            # We'll try a conservative invocation and capture logs.
            cmd = [joern_parse, srcdir, "--output", os.path.join(outdir, "cpg.bin")]
            proc = _run(cmd)

            # Heuristic: treat exit_code == 0 and absence of common parse error markers as OK.
            ok = (proc["exit_code"] == 0) and (not _looks_like_parse_error(proc["stderr"] + "\n" + proc["stdout"]))

            # Some Joern builds may ignore --output; still, if parse succeeded, exit_code=0.
            if ok:
                return {
                    "ok": True,
                    "language": lang,
                    "exit_code": proc["exit_code"],
                    "stdout": proc["stdout"],
                    "stderr": proc["stderr"],
                    "notes": (
                        "Joern parsing succeeded. For Java this is a strong syntax signal. "
                        "For C/C++, consider additionally compiling with clang/gcc for strict validation."
                    ),
                }

        # If both attempts failed, return the last attempt logs (plus note).
        return {
            "ok": False,
            "language": None,
            "exit_code": proc["exit_code"],
            "stdout": proc["stdout"],
            "stderr": proc["stderr"],
            "notes": "Joern parsing failed for both Java and C attempts. Code likely has syntax errors or requires additional context.",
        }


def _find_joern_parse() -> str | None:
    # 1) PATH
    p = shutil.which("joern-parse")
    if p:
        return p

    # 2) JOERN_HOME
    joern_home = os.environ.get("JOERN_HOME")
    if joern_home:
        candidate = os.path.join(joern_home, "bin", "joern-parse")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def _run(cmd, timeout_s: int = 60) -> Dict[str, Any]:
    try:
        cp = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {"exit_code": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}
    except subprocess.TimeoutExpired as e:
        return {
            "exit_code": 124,
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + "\nTimed out while running Joern.",
        }
    except Exception as e:
        return {"exit_code": 125, "stdout": "", "stderr": f"Failed to run Joern: {e}"}


def _looks_like_parse_error(log_text: str) -> bool:
    # Common-ish markers across tools/logs. Adjust to your Joern version if needed.
    patterns = [
        r"\bparse error\b",
        r"\bparser error\b",
        r"\bsyntax error\b",
        r"\bParsing failed\b",
        r"\bfailed to parse\b",
        r"\bException\b.*\bparse\b",
        r"\bERROR\b.*\bparse\b",
    ]
    t = log_text.lower()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patterns)


# Define the tools
open_proj_tool = StructuredTool.from_function(open_proj)
identify_variable_tool = StructuredTool.from_function(identify_variable)
trace_method_usage_tool = StructuredTool.from_function(trace_method_usage)
find_method_in_file_tool = StructuredTool.from_function(find_method_in_file)
analyze_method_details_tool = StructuredTool.from_function(analyze_method_details)
analyze_method_control_flow_tool = StructuredTool.from_function(analyze_method_control_flow)
find_class_loc_tool = StructuredTool.from_function(find_class_loc)
identify_class_tool = StructuredTool.from_function(identify_class)
get_imports_tool = StructuredTool.from_function(get_imports)
close_proj_tool = StructuredTool.from_function(close_proj)
invariant_check_tool = StructuredTool.from_function(invariant_check)
syntax_check = StructuredTool.from_function(joern_syntax_check)
#example_patch_search_tool = StructuredTool.from_function(example_patch_search)


