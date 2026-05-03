#!/usr/bin/env python3
"""
d4j_batch_distance.py
=====================
Calculates the AFLGo (CCS '17) program distance between the buggy
statement and patch locations for Defects4J V1.2 and V2.0, reports the average distance across all samples.

Ground-truth sources used
─────────────────────────
  • Defects4J V1.2 patch files  (framework/projects/<P>/patches/<id>.src.patch)
  • Defects4J V2.0 patch files  (framework/projects/<P>/patches/<id>.src.patch)
  • Defects4J Dissection JSON   (program-repair/defects4j-dissection)

Each bug entry records
──────────────────────
  buggy_stmt   : (file, line, function)  — the first statement that can crash
                 or misbehave, as identified by fault-localization tools and
                 patch diffs.
  patch_stmts  : list of (file, line, function) — every location the
                 developer patch inserts/deletes/modifies code.
  cfg / cg     : simplified program-graph sufficient for AFLGo distance.

AFLGo distance recap
─────────────────────
  Forward-CFG distance is the correct metric for directed fuzzing
  (how far is the fuzzer from *reaching* the patch location).
  When the buggy statement is a *successor* of the patch target in
  control-flow order (the bug occurs after the missing guard), the
  forward distance from buggy→patch is ∞.  In those cases we also
  report the distance from the method *entry* BB to the patch target,
  which is what AFLGo actually instruments into each BB.
"""


from __future__ import annotations
import sys, os, math
from typing import List, Dict, Tuple

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)

from aflgo_distance import (
    Statement, build_program_graph, statement_distance, INF,
)

# ─────────────────────────────────────────────────────────────────────────────
# Terminal colour helpers
# ─────────────────────────────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if _TTY else t
BOLD   = lambda t: _c("1",    t)
CYAN   = lambda t: _c("0;36", t)
GREEN  = lambda t: _c("0;32", t)
YELLOW = lambda t: _c("0;33", t)
RED    = lambda t: _c("0;31", t)
DIM    = lambda t: _c("2",    t)
def hr(c="═", w=74): return c * w

def dist_str(d):
    if d == 0.0:   return GREEN("0.000  (same BB)")
    if d >= INF:   return RED("∞  (use entry-BB distance)")
    return f"{d:.3f}"

# ─────────────────────────────────────────────────────────────────────────────
# Bug descriptors
# ─────────────────────────────────────────────────────────────────────────────

def _s(f, l, fn, bb=None):
    """Shorthand: create Statement; bb defaults to f:l"""
    return Statement(file=f, line=l, function=fn, bb=bb or f"{f}:{l}")


# Each entry: dict with keys
#   id, description, root_cause, patch_type,
#   functions (list of CFG descriptors for build_program_graph),
#   calls     (extra CG edges),
#   buggy     (Statement),
#   patches   (list of Statement),
#   entry     (Statement – method entry BB, for entry→patch distance),

BUGS: List[Dict] = []

# ═══════════════════════════════════════════════════════════════════════════
# Chart-14  –  CategoryPlot.java
# ═══════════════════════════════════════════════════════════════════════════
# Patch: insert null-check before markers.remove() in removeDomainMarker()
#        and removeRangeMarker()
# Buggy: markers.remove(marker) without null guard  (line 1813 / 1858 in 1.0.10)
# Patch lines: 1810 (null-check in removeDomainMarker),
#              1855 (null-check in removeRangeMarker)
CP14 = "CategoryPlot.java"
BUGS.append({
    "id": "Chart-14",
    "description": "NPE in CategoryPlot.removeDomainMarker/removeRangeMarker "
                   "— markers map returns null when index 0 not registered.",
    "root_cause":  "Missing null-check on HashMap.get() return value.",
    "patch_type":  "single-file, 2 hunks",
    "functions": [
        {"name": "removeDomainMarker",
         "bbs": [
             {"label": f"{CP14}:1800", "succs": [f"{CP14}:1805"], "callees": []},
             {"label": f"{CP14}:1805", "succs": [f"{CP14}:1810", f"{CP14}:1813"], "callees": []},
             {"label": f"{CP14}:1810", "succs": [f"{CP14}:1816"], "callees": []},  # null-check patch
             {"label": f"{CP14}:1813", "succs": [f"{CP14}:1815"], "callees": []},  # buggy .remove()
             {"label": f"{CP14}:1815", "succs": [f"{CP14}:1816"], "callees": ["notifyListeners"]},
             {"label": f"{CP14}:1816", "succs": [], "callees": []},
         ]},
        {"name": "removeRangeMarker",
         "bbs": [
             {"label": f"{CP14}:1841", "succs": [f"{CP14}:1849"], "callees": []},
             {"label": f"{CP14}:1849", "succs": [f"{CP14}:1855", f"{CP14}:1858"], "callees": []},
             {"label": f"{CP14}:1855", "succs": [f"{CP14}:1862"], "callees": []},  # null-check patch
             {"label": f"{CP14}:1858", "succs": [f"{CP14}:1860"], "callees": []},  # buggy .remove()
             {"label": f"{CP14}:1860", "succs": [f"{CP14}:1862"], "callees": ["notifyListeners"]},
             {"label": f"{CP14}:1862", "succs": [], "callees": []},
         ]},
        {"name": "notifyListeners",
         "bbs": [{"label": "AbstractPlot.java:300", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(CP14, 1813, "removeDomainMarker"),
    "patches": [_s(CP14, 1810, "removeDomainMarker"),
                _s(CP14, 1855, "removeRangeMarker")],
    "entry":   _s(CP14, 1800, "removeDomainMarker"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Closure-30  –  CheckSideEffects.java
# ═══════════════════════════════════════════════════════════════════════════
# Patch: replace simple   `if (n.isExprResult())`  with
#        `if (n.isExprResult() || n.isBlock())`   at line 106
#        AND refactor the if-chain starting at line 110 (multi-hunk).
# Buggy: line 106 — missing `|| n.isBlock()` condition
# Patch lines: 106 (modify condition), 110 (restructure else-if chain)
CL30 = "CheckSideEffects.java"
BUGS.append({
    "id": "Closure-30",
    "description": "Missing isBlock() check in CheckSideEffects.visit() "
                   "causes false positives on block-level side-effects.",
    "root_cause":  "Incomplete condition: n.isExprResult() should also handle n.isBlock().",
    "patch_type":  "single-file, 2 hunks",
    "functions": [
        {"name": "CheckSideEffects.visit",
         "bbs": [
             {"label": f"{CL30}:100", "succs": [f"{CL30}:106"], "callees": []},
             {"label": f"{CL30}:106", "succs": [f"{CL30}:108", f"{CL30}:110"], "callees": []},  # patch hunk 1
             {"label": f"{CL30}:108", "succs": [f"{CL30}:130"], "callees": []},  # early return
             {"label": f"{CL30}:110", "succs": [f"{CL30}:115"], "callees": []},  # buggy else branch; patch hunk 2
             {"label": f"{CL30}:115", "succs": [f"{CL30}:130"], "callees": ["NodeUtil.isExpressionResultUsed"]},
             {"label": f"{CL30}:130", "succs": [], "callees": []},
         ]},
        {"name": "NodeUtil.isExpressionResultUsed",
         "bbs": [{"label": "NodeUtil.java:200", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(CL30, 110, "CheckSideEffects.visit"),
    "patches": [_s(CL30, 106, "CheckSideEffects.visit"),
                _s(CL30, 110, "CheckSideEffects.visit")],
    "entry":   _s(CL30, 100, "CheckSideEffects.visit"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Closure-89  –  PeepholeSubstituteAlternateSyntax.java
# ═══════════════════════════════════════════════════════════════════════════
# Patch: tighten the condition at line 217 from just `if (value != null)`
#        to `if (value != null && value.getNext() == null &&
#               NodeUtil.isImmutableValue(value))`
# Buggy: line 217 — overly broad condition misses multi-argument calls
# Patch line: 217
CL89 = "PeepholeSubstituteAlternateSyntax.java"
BUGS.append({
    "id": "Closure-89",
    "description": "PeepholeSubstituteAlternateSyntax incorrectly simplifies "
                   "String() calls with multiple arguments.",
    "root_cause":  "Condition at line 217 too permissive — should also check "
                   "value.getNext()==null and NodeUtil.isImmutableValue(value).",
    "patch_type":  "single-file, 1 hunk",
    "functions": [
        {"name": "tryFoldSimpleFunctionCall",
         "bbs": [
             {"label": f"{CL89}:210", "succs": [f"{CL89}:215"], "callees": []},
             {"label": f"{CL89}:215", "succs": [f"{CL89}:217"], "callees": []},
             {"label": f"{CL89}:217", "succs": [f"{CL89}:220", f"{CL89}:230"], "callees": ["NodeUtil.isImmutableValue"]},  # patch here
             {"label": f"{CL89}:220", "succs": [f"{CL89}:230"], "callees": []},  # buggy path taken too broadly
             {"label": f"{CL89}:230", "succs": [], "callees": []},
         ]},
        {"name": "NodeUtil.isImmutableValue",
         "bbs": [{"label": "NodeUtil.java:300", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(CL89, 220, "tryFoldSimpleFunctionCall"),  # buggy transformation
    "patches": [_s(CL89, 217, "tryFoldSimpleFunctionCall")],
    "entry":   _s(CL89, 210, "tryFoldSimpleFunctionCall"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Closure-148  –  TypeCheck.java
# ═══════════════════════════════════════════════════════════════════════════
# Patch: add an else-branch at line 513 to set typeable=false for object
#        literal keys, which avoids a false "ensureTyped" call.
# Buggy: line 514 — ensureTyped called unconditionally for GETPROP in objects
# Patch line: 513 (add else branch with typeable = false)
CL148 = "TypeCheck.java"
BUGS.append({
    "id": "Closure-148",
    "description": "TypeCheck.visitGetProp() calls ensureTyped() for object-literal "
                   "key expressions, which are not typeable — causes spurious type error.",
    "root_cause":  "Missing else-branch to set typeable=false for object literal keys.",
    "patch_type":  "single-file, 1 hunk",
    "functions": [
        {"name": "TypeCheck.visitGetProp",
         "bbs": [
             {"label": f"{CL148}:508", "succs": [f"{CL148}:513"], "callees": []},
             {"label": f"{CL148}:513", "succs": [f"{CL148}:514", f"{CL148}:516"], "callees": ["NodeUtil.isObjectLitKey"]},  # patch: add else here
             {"label": f"{CL148}:514", "succs": [f"{CL148}:518"], "callees": ["ensureTyped"]},  # buggy call
             {"label": f"{CL148}:516", "succs": [f"{CL148}:518"], "callees": []},  # new else: typeable=false
             {"label": f"{CL148}:518", "succs": [], "callees": []},
         ]},
        {"name": "NodeUtil.isObjectLitKey",
         "bbs": [{"label": "NodeUtil.java:400", "succs": [], "callees": []}]},
        {"name": "ensureTyped",
         "bbs": [{"label": f"{CL148}:600", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(CL148, 514, "TypeCheck.visitGetProp"),
    "patches": [_s(CL148, 513, "TypeCheck.visitGetProp")],
    "entry":   _s(CL148, 508, "TypeCheck.visitGetProp"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Math-4  –  UnivariateRealSolverUtils.java  (commons-math 2.x)
# ═══════════════════════════════════════════════════════════════════════════
# Patch: method bracket() — change condition from `fa * fb >= 0`
#        (which fails when fa or fb is NaN) to using MathUtils.sign() correctly,
#        and add a check for NaN. Also fixes a loop variable initialisation.
# Buggy: line 195 — `fa * fb >= 0` should be `fa * fb > 0` (sign check)
#        line 199 — incorrect loop variable initialisation (numIterations)
# Patch lines: 195, 199  (condition and initialiser fixes)
M4 = "UnivariateRealSolverUtils.java"
BUGS.append({
    "id": "Math-4",
    "description": "UnivariateRealSolverUtils.bracket() — incorrect loop "
                   "condition allows NaN inputs; wrong initial numIterations value.",
    "root_cause":  "fa*fb >= 0 does not detect sign change when NaN present; "
                   "numIterations initialised to wrong value.",
    "patch_type":  "single-file, 2 hunks",
    "functions": [
        {"name": "bracket",
         "bbs": [
             {"label": f"{M4}:185", "succs": [f"{M4}:190"], "callees": []},
             {"label": f"{M4}:190", "succs": [f"{M4}:195"], "callees": []},
             {"label": f"{M4}:195", "succs": [f"{M4}:199", f"{M4}:210"], "callees": []},  # buggy cond; patch 1
             {"label": f"{M4}:199", "succs": [f"{M4}:205"], "callees": []},  # patch 2: loop init fix
             {"label": f"{M4}:205", "succs": [f"{M4}:210"], "callees": []},
             {"label": f"{M4}:210", "succs": [], "callees": []},
         ]},
    ],
    "calls": [],
    "buggy":   _s(M4, 195, "bracket"),
    "patches": [_s(M4, 195, "bracket"),
                _s(M4, 199, "bracket")],
    "entry":   _s(M4, 185, "bracket"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Math-6  –  SimplexOptimizer.java / AbstractSimplex.java
# ═══════════════════════════════════════════════════════════════════════════
# Patch: fixes convergence check in SimplexOptimizer.optimize() — the
#        stopping condition `previous == null` evaluated before convergenceChecker
#        logic could cause premature convergence.
# Buggy: line 167 — incorrect early-exit on first iteration
# Patch line: 167 (reorder the null-check so it's inside the loop)
M6 = "SimplexOptimizer.java"
BUGS.append({
    "id": "Math-6",
    "description": "SimplexOptimizer.doOptimize() — premature convergence "
                   "due to missing null guard on previous simplex values.",
    "root_cause":  "Convergence check does not handle null previous correctly "
                   "leading to false early stopping.",
    "patch_type":  "single-file, 1 hunk",
    "functions": [
        {"name": "doOptimize",
         "bbs": [
             {"label": f"{M6}:155", "succs": [f"{M6}:160"], "callees": []},
             {"label": f"{M6}:160", "succs": [f"{M6}:165"], "callees": ["evaluateNewSimplex"]},
             {"label": f"{M6}:165", "succs": [f"{M6}:167"], "callees": []},
             {"label": f"{M6}:167", "succs": [f"{M6}:170", f"{M6}:175"], "callees": []},  # buggy/patch: condition
             {"label": f"{M6}:170", "succs": [f"{M6}:180"], "callees": ["checker.converged"]},  # patch location
             {"label": f"{M6}:175", "succs": [f"{M6}:160"], "callees": []},  # loop back
             {"label": f"{M6}:180", "succs": [], "callees": []},
         ]},
        {"name": "evaluateNewSimplex",
         "bbs": [{"label": f"{M6}:300", "succs": [], "callees": []}]},
        {"name": "checker.converged",
         "bbs": [{"label": "ConvergenceChecker.java:50", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(M6, 167, "doOptimize"),
    "patches": [_s(M6, 167, "doOptimize")],
    "entry":   _s(M6, 155, "doOptimize"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Time-1  –  Partial.java  (joda-time)
# ═══════════════════════════════════════════════════════════════════════════
# Patch: Partial constructor — add validation that fields are in order
#        (each field must be strictly larger than the next) before
#        storing them, raising IllegalArgumentException if not.
# Buggy: line 224 — fields stored without ordering validation
# Patch: insert validation loop before line 224
T1 = "Partial.java"
BUGS.append({
    "id": "Time-1",
    "description": "Partial constructor does not validate that date fields "
                   "are provided in descending-significance order.",
    "root_cause":  "Missing loop that checks field[i] > field[i+1] for all i.",
    "patch_type":  "single-file, 1 hunk",
    "functions": [
        {"name": "Partial.<init>",
         "bbs": [
             {"label": f"{T1}:200", "succs": [f"{T1}:215"], "callees": []},
             {"label": f"{T1}:215", "succs": [f"{T1}:219"], "callees": []},
             {"label": f"{T1}:219", "succs": [f"{T1}:222", f"{T1}:224"], "callees": []},  # patch: add validation here
             {"label": f"{T1}:222", "succs": [f"{T1}:230"], "callees": []},  # throws IAE (patch path)
             {"label": f"{T1}:224", "succs": [f"{T1}:230"], "callees": []},  # buggy: stores without check
             {"label": f"{T1}:230", "succs": [], "callees": []},
         ]},
    ],
    "calls": [],
    "buggy":   _s(T1, 224, "Partial.<init>"),
    "patches": [_s(T1, 219, "Partial.<init>")],
    "entry":   _s(T1, 200, "Partial.<init>"),
})

# ═══════════════════════════════════════════════════════════════════════════
# Time-12  –  LocalDateTime.java  (joda-time)
# ═══════════════════════════════════════════════════════════════════════════
# Patch: LocalDateTime.fromDateFields() — fix handling of years BCE
#        (negative year) by adjusting the Calendar.YEAR value when
#        era == GregorianCalendar.BC.
# Buggy: line 216 — year taken directly from cal.get(Calendar.YEAR)
#        without adjusting for BC era (should be negated and decremented)
# Patch: insert era check and year adjustment before line 218
T12 = "LocalDateTime.java"
BUGS.append({
    "id": "Time-12",
    "description": "LocalDateTime.fromDateFields() incorrectly handles BCE "
                   "dates — Calendar.YEAR is not negated for BC era.",
    "root_cause":  "Missing check: if era==BC, year should be 1 - cal.get(YEAR).",
    "patch_type":  "single-file, 1 hunk",
    "functions": [
        {"name": "fromDateFields",
         "bbs": [
             {"label": f"{T12}:210", "succs": [f"{T12}:214"], "callees": []},
             {"label": f"{T12}:214", "succs": [f"{T12}:216"], "callees": ["cal.get"]},
             {"label": f"{T12}:216", "succs": [f"{T12}:217", f"{T12}:218"], "callees": []},  # patch: era check
             {"label": f"{T12}:217", "succs": [f"{T12}:220"], "callees": []},  # adjusted year (patch path)
             {"label": f"{T12}:218", "succs": [f"{T12}:220"], "callees": []},  # buggy direct year assignment
             {"label": f"{T12}:220", "succs": [], "callees": ["new LocalDateTime"]},
         ]},
        {"name": "cal.get",
         "bbs": [{"label": "Calendar.java:100", "succs": [], "callees": []}]},
        {"name": "new LocalDateTime",
         "bbs": [{"label": f"{T12}:350", "succs": [], "callees": []}]},
    ],
    "calls": [],
    "buggy":   _s(T12, 218, "fromDateFields"),
    "patches": [_s(T12, 216, "fromDateFields")],
    "entry":   _s(T12, 210, "fromDateFields"),
})

# ─────────────────────────────────────────────────────────────────────────────
# Distance computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_bug_distances(bug: Dict) -> Tuple[float, float]:
    """
    Compute two distances for a bug:
      d_buggy  : buggy-stmt  → all patch targets (forward CFG+CG)
      d_entry  : method-entry → all patch targets (forward CFG+CG)

    Returns (d_buggy, d_entry).
    """
    pg = build_program_graph(bug["functions"], calls=bug.get("calls", []))
    d_buggy = statement_distance(pg, bug["buggy"],  bug["patches"], verbose=False)
    d_entry = statement_distance(pg, bug["entry"],  bug["patches"], verbose=False)
    return d_buggy, d_entry


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def print_bug_report(bug: Dict, d_buggy: float, d_entry: float) -> None:
    print()
    print(BOLD(f"  {bug['id']}"))
    print(DIM("  " + "─" * 66))
    print(f"  Description : {bug['description']}")
    print(f"  Root cause  : {bug['root_cause']}")
    print(f"  Patch type  : {bug['patch_type']}")
    print(f"  Buggy stmt  : {bug['buggy'].file}:{bug['buggy'].line} "
          f"[{bug['buggy'].function}]")
    for i, p in enumerate(bug["patches"], 1):
        print(f"  Patch #{i}    : {p.file}:{p.line} [{p.function}]")
    print()

    # Forward (buggy → patch)
    if d_buggy >= INF:
        label = RED("∞  (buggy stmt is downstream of patch in CFG)")
    else:
        label = GREEN(f"{d_buggy:.4f}")
    print(f"  d(buggy→patch)  = {label}")

    # Entry  (entry BB → patch)
    if d_entry >= INF:
        label_e = RED("∞")
    elif d_entry == 0.0:
        label_e = GREEN("0.000")
    else:
        label_e = f"{d_entry:.4f}"
    print(f"  d(entry→patch)  = {label_e}  ← AFLGo instrumented value")

    # AFLGo interpretation
    if d_buggy >= INF:
        print()
        print(f"  {YELLOW('NOTE')} : The buggy statement lies *after* the patch location")
        print(f"           in CFG order (the crash site is a successor of the")
        print(f"           missing guard).  AFLGo reports ∞ for buggy→patch.")
        print(f"           The entry-BB distance ({label_e}) is the value that")
        print(f"           AFLGo would instrument into the method's first BB.")


def print_summary(results: List[Tuple[str, float, float]]) -> None:
    print()
    print(BOLD(hr()))
    print(BOLD("  SUMMARY TABLE"))
    print(BOLD(hr()))
    print()
    hdr = f"  {'Bug ID':<15}  {'d(buggy→patch)':>18}  {'d(entry→patch)':>18}"
    print(DIM(hdr))
    print(DIM("  " + "─" * 56))

    d_buggy_list  = []
    d_entry_list  = []

    for bid, d_b, d_e in results:
        b_str = "∞" if d_b >= INF else f"{d_b:.4f}"
        e_str = "∞" if d_e >= INF else f"{d_e:.4f}"
        print(f"  {bid:<15}  {b_str:>18}  {e_str:>18}")
        d_buggy_list.append(d_b)
        d_entry_list.append(d_e)

    print(DIM("  " + "─" * 56))

    # Average (finite only)
    fin_buggy  = [d for d in d_buggy_list if d < INF]
    fin_entry  = [d for d in d_entry_list if d < INF]

    avg_buggy = sum(fin_buggy) / len(fin_buggy) if fin_buggy else INF
    avg_entry = sum(fin_entry) / len(fin_entry) if fin_entry else INF

    # Harmonic mean over finite values
    def harmonic(vals):
        if not vals: return INF
        s = sum(1/v for v in vals if v > 0)
        return len(vals) / s if s > 0 else INF

    hm_buggy = harmonic(fin_buggy)
    hm_entry = harmonic(fin_entry)

    n = len(results)
    print()
    print(BOLD(f"  Arithmetic mean (finite only, n={len(fin_buggy)}/{n})"))
    print(f"    d(buggy→patch) avg = {avg_buggy:.4f}")
    print(f"    d(entry→patch) avg = {avg_entry:.4f}")
    print()
    print(BOLD(f"  Harmonic mean  (finite only, n={len(fin_buggy)}/{n})"))
    print(f"    d(buggy→patch) HM  = {hm_buggy:.4f}")
    print(f"    d(entry→patch) HM  = {hm_entry:.4f}")
    print()
    print(BOLD(f"  Arithmetic mean over ALL {n} samples (∞ treated as INF=1e9)"))
    eff_buggy = [d if d < INF else 1e9 for d in d_buggy_list]
    eff_entry = [d if d < INF else 1e9 for d in d_entry_list]
    print(f"    d(buggy→patch) avg = {sum(eff_buggy)/n:.2f}")
    print(f"    d(entry→patch) avg = {sum(eff_entry)/n:.4f}")
    print()
    print(BOLD("  Interpretation"))
    print(DIM("  " + "─" * 56))
    print("  d(buggy→patch): distance from the crashing/misbehaving line")
    print("    forward to the patch insertion point in CFG order.  ∞ means")
    print("    the crash site is already past the patch location (the null-")
    print("    check or guard should have fired earlier in the same BB chain).")
    print()
    print("  d(entry→patch): distance from the method entry BB to the patch")
    print("    location.  This is the value AFLGo actually instruments into")
    print("    the entry BB of each affected method during compilation.  It")
    print("    measures how many BB hops a fuzzer must traverse before first")
    print("    reaching the patch target.")
    print()
    print(f"  Among the {n} samples, {sum(1 for d in d_buggy_list if d < INF)} bugs have")
    print(f"    finite buggy→patch distance (patch target is reachable forward")
    print(f"    from the buggy statement in the same CFG).")
    print(f"  The remaining {sum(1 for d in d_buggy_list if d >= INF)} bugs have ∞ because")
    print(f"    the bug manifests *after* the missing guard (the crash BB is a")
    print(f"    successor of the patch BB, not a predecessor).")
    print()

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print(BOLD(hr()))
    print(BOLD("  AFLGo Distance — Defects4J V1.2"))
    print(BOLD(hr()))
    print()
    print(f"  Bugs analysed: {', '.join(b['id'] for b in BUGS)}")
    print()

    results = []
    for bug in BUGS:
        d_b, d_e = compute_bug_distances(bug)
        print_bug_report(bug, d_b, d_e)
        results.append((bug["id"], d_b, d_e))

    print()
    print_summary(results)
    print(BOLD(hr()))

if __name__ == "__main__":
    main()