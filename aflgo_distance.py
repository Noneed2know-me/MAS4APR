#!/usr/bin/env python3
"""
aflgo_distance.py
=================
Pure-Python implementation of the AFLGo (CCS '17) distance metric,
extended to measure the distance between a **target statement** and a
**set of connected statements** (one or many, same file or cross-file).

─────────────────────────────────────────────────────────────────────
AFLGo distance - recap of the two-level formula
─────────────────────────────────────────────────────────────────────
This file models the program as explicit Python data structures
(no LLVM/compilation required):

    Statement  - a source location (file, line, function, BB)
    CFG        - intra-procedural control-flow graph over BBs
    CallGraph  - inter-procedural call graph over functions
    ProgramGraph - wrapper holding both graphs + helpers

Usage
─────
See the __main__ block at the bottom for worked examples that cover:
  1. Same-file, single target
  2. Same-file, multiple targets (harmonic mean kicks in)
  3. Cross-file targets (call-graph edge links the files)
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, eq=True)
class Statement:
    """
    A source location that uniquely identifies a statement.

    Attributes
    ----------
    file     : source file name (basename, e.g. 'foo.c')
    line     : 1-based line number
    function : enclosing function name
    bb       : basic-block label (e.g. 'foo.c:42')
                In AFLGo the BB label is '<basename>:<first-debug-line>'.
                We use the same convention here.
    """
    file:     str
    line:     int
    function: str
    bb:       str = field(default="")   # if empty, computed from file:line

    def __post_init__(self) -> None:
        if not self.bb:
            # Mimic AFLGo's BB naming: "<basename>:<line>"
            object.__setattr__(self, "bb", f"{self.file}:{self.line}")

    def __str__(self) -> str:
        return f"{self.file}:{self.line} [{self.function}/{self.bb}]"


# ─────────────────────────────────────────────────────────────────────────────
# Graph helpers
# ─────────────────────────────────────────────────────────────────────────────

INF = float("inf")


def _bfs_shortest(adj: Dict[str, Set[str]], source: str) -> Dict[str, int]:
    """
    BFS from *source* on an unweighted directed graph.
    Returns a dict {node: hop_count}; unreachable nodes are absent.
    """
    dist: Dict[str, int] = {source: 0}
    queue: deque = deque([source])
    while queue:
        u = queue.popleft()
        for v in adj.get(u, set()):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def _harmonic_mean_distance(
    source: str,
    targets: Set[str],
    shortest: Dict[str, int],
) -> float:
    """
    AFLGo harmonic-mean-based distance aggregation.

        d = |T_s| / Σ_{t ∈ T_s}  1 / (1 + S_{s→t})

    where T_s ⊆ targets contains only targets *reachable* from source
    (S_{s→t} < ∞).  Returns INF when no target is reachable.
    """
    reachable = [t for t in targets if t in shortest]
    if not reachable:
        return INF
    denom = sum(1.0 / (1.0 + shortest[t]) for t in reachable)
    return len(reachable) / denom



# Call-graph distance

def compute_cg_distances(
    cg_adj:   Dict[str, Set[str]],   # function → {callee, ...}
    targets:  Set[str],               # set of target function names
    all_fns:  Set[str],               # all function names in the program
) -> Dict[str, float]:
    """
    Compute AFLGo CG-level distances for every function in *all_fns*.

    For each function f, BFS is run from f on the call graph to find
    S_{f→t} for each target t, then the harmonic-mean formula is applied.

    Returns
    -------
    dict mapping function_name → cg_distance (INF if unreachable).
    """
    cg_distances: Dict[str, float] = {}

    for fn in all_fns:
        shortest = _bfs_shortest(cg_adj, fn)
        d = _harmonic_mean_distance(fn, targets, shortest)
        cg_distances[fn] = d

    # Target functions themselves have CG-distance 0
    for t in targets:
        if t in all_fns:
            cg_distances[t] = 0.0

    return cg_distances



# Basic-block distance

def compute_bb_distances(
    cfg_adj:       Dict[str, Set[str]],    # bb → {successor_bb, ...}
    bb_to_callees: Dict[str, Set[str]],    # bb → {called_function, ...}
    target_bbs:    Set[str],               # BBs that contain a target stmt
    cg_distances:  Dict[str, float],       # from compute_cg_distances
    all_bbs:       Set[str],               # all BBs in this function's CFG
) -> Dict[str, float]:
    """
    Compute AFLGo BB-level distances for every BB in *all_bbs*.

    Steps (mirroring AFLGo distance_calculator/main.cpp):

      1. For each BB b, set bb_dist(b) = min CG-distance of its callees
         that have a finite CG-distance.
      2. For target BBs, override bb_dist(b) = 0.
      3. For every non-target BB n, run BFS on the CFG to find
         S_{n→t} for each target t, then apply the harmonic-mean formula:
             d(n) = |T_n| / Σ_{t ∈ T_n} 1/(1 + 10·bb_dist(t) + S_{n→t})
      4. If bb_dist(n) is already defined (step 1), output 10·bb_dist(n).

    Returns
    -------
    dict mapping bb_label → final_bb_distance (INF if unreachable).
    """
    # ── Step 1: inherit CG distance from callees ─────────────────────────────
    bb_dist: Dict[str, float] = {}
    for bb, callees in bb_to_callees.items():
        if bb not in all_bbs:
            continue
        for callee in callees:
            if callee in cg_distances and cg_distances[callee] < INF:
                prev = bb_dist.get(bb, INF)
                bb_dist[bb] = min(prev, cg_distances[callee])

    # ── Step 2: target BBs have dist 0 ───────────────────────────────────────
    for t in target_bbs:
        if t in all_bbs:
            bb_dist[t] = 0.0

    # ── Step 3 & 4: final per-BB output distance ─────────────────────────────
    output: Dict[str, float] = {}

    for n in all_bbs:
        # If the BB itself has a bb_dist, AFLGo outputs 10 × bb_dist
        if n in bb_dist:
            output[n] = 10.0 * bb_dist[n]
            continue

        # Otherwise: harmonic-mean aggregation over target BBs via CFG BFS
        shortest_from_n = _bfs_shortest(cfg_adj, n)

        denom = 0.0
        count = 0
        for t in (target_bbs & all_bbs):
            if t not in shortest_from_n:
                continue  # unreachable
            s_nt  = shortest_from_n[t]
            bd_t  = bb_dist.get(t, 0.0)          # bb_dist of target is 0
            denom += 1.0 / (1.0 + 10.0 * bd_t + s_nt)
            count += 1

        if count == 0 or denom == 0.0:
            output[n] = INF
        else:
            output[n] = count / denom

    return output

class ProgramGraph:
    """
    Holds the full inter-procedural program graph needed for AFLGo distance
    computation.

    Parameters
    ----------
    cg_adj : dict  { caller_function : {callee_function, ...} }
    cfgs   : dict  { function_name : CFG dict }
             Each CFG dict has two keys:
               'adj'     : { bb_label : {successor_bb, ...} }
               'callees' : { bb_label : {called_function, ...} }
               'bbs'     : set of all bb_labels in this function
    bb_to_fn : dict  { bb_label : function_name }
    """

    def __init__(
        self,
        cg_adj:   Dict[str, Set[str]],
        cfgs:     Dict[str, dict],
        bb_to_fn: Dict[str, str],
    ) -> None:
        self.cg_adj    = cg_adj
        self.cfgs      = cfgs
        self.bb_to_fn  = bb_to_fn
        self.all_fns:  Set[str] = set(cfgs.keys())
        self.all_bbs:  Set[str] = set(bb_to_fn.keys())

    def compute_distance(
        self,
        source_stmt:  Statement,
        target_stmts: List[Statement],
        verbose:      bool = False,
    ) -> float:
        """
        Compute the AFLGo distance from *source_stmt* to the set of
        *target_stmts* (one or many, same file or cross-file).

        The distance represents: how many steps (in call-graph hops +
        CFG hops) does it take to reach *any* of the target statements
        from the source statement, aggregated by AFLGo's harmonic-mean.

        Algorithm
        ---------
        1. Identify the target functions (the functions containing
           each target statement).
        2. Run CG-level distance computation from *all* functions.
        3. Identify the target BBs (the BBs containing each target
           statement).
        4. For each function, run BB-level distance computation.
        5. Look up the final distance of the source BB.

        Returns
        -------
        float  (0.0 if source == target; INF if unreachable)
        """
        # ── Identify targets ─────────────────────────────────────────────────
        target_fns:  Set[str] = {s.function for s in target_stmts}
        target_bbs:  Set[str] = {s.bb       for s in target_stmts}
        source_bb  = source_stmt.bb
        source_fn  = source_stmt.function

        if verbose:
            print(f"\n{'═'*66}")
            print(f"  AFLGo Distance Calculation")
            print(f"{'═'*66}")
            print(f"  Source  : {source_stmt}")
            for t in target_stmts:
                print(f"  Target  : {t}")
            print(f"  #targets (functions): {len(target_fns)}")
            print(f"  #targets (BBs)      : {len(target_bbs)}")

        # ── Edge case: source is already a target ────────────────────────────
        if source_bb in target_bbs:
            if verbose:
                print(f"  → source BB is a target BB: distance = 0.0")
            return 0.0

        # ── Step 1: CG distances ─────────────────────────────────────────────
        cg_dist = compute_cg_distances(
            cg_adj=self.cg_adj,
            targets=target_fns,
            all_fns=self.all_fns,
        )
        if verbose:
            print(f"\n  CG distances (finite only):")
            for fn, d in sorted(cg_dist.items()):
                if d < INF:
                    print(f"    {fn:30s}  {d:.6f}")

        # ── Step 2: BB distances per function ────────────────────────────────
        all_bb_distances: Dict[str, float] = {}

        for fn, cfg in self.cfgs.items():
            bb_out = compute_bb_distances(
                cfg_adj=cfg["adj"],
                bb_to_callees=cfg.get("callees", {}),
                target_bbs=target_bbs,
                cg_distances=cg_dist,
                all_bbs=cfg["bbs"],
            )
            all_bb_distances.update(bb_out)

        if verbose:
            print(f"\n  BB distances (finite only):")
            for bb, d in sorted(all_bb_distances.items()):
                marker = " ← SOURCE" if bb == source_bb else (
                         " ← TARGET" if bb in target_bbs else "")
                if d < INF:
                    print(f"    {bb:40s}  {d:.6f}{marker}")

        # ── Step 3: Look up source BB distance ───────────────────────────────
        result = all_bb_distances.get(source_bb, INF)

        if verbose:
            print(f"\n  ┌{'─'*50}┐")
            print(f"  │  Final distance: {result:<30.6f}  │")
            print(f"  └{'─'*50}┘")

        return result

def build_program_graph(
    functions: List[dict],
    calls:     List[Tuple[str, str]],
) -> ProgramGraph:
    """
    Build a ProgramGraph from a high-level description.

    Parameters
    ----------
    functions : list of function descriptors, each:
        {
          'name' : str,                  # function name
          'bbs'  : [                     # list of basic blocks
              {
                'label'    : str,        # BB label (e.g. 'foo.c:10')
                'succs'    : [str],      # labels of successor BBs
                'callees'  : [str],      # functions called from this BB
              },
              ...
          ]
        }
    calls  : list of (caller_function, callee_function) edges for the CG.
             (These are *in addition* to edges already implied by 'callees'
              in the BB descriptors.)

    Returns
    -------
    ProgramGraph
    """
    cg_adj:   Dict[str, Set[str]] = defaultdict(set)
    cfgs:     Dict[str, dict]     = {}
    bb_to_fn: Dict[str, str]      = {}

    # Add explicit call-graph edges
    for caller, callee in calls:
        cg_adj[caller].add(callee)

    for fn_desc in functions:
        fn_name  = fn_desc["name"]
        bbs_desc = fn_desc.get("bbs", [])

        cfg_adj:      Dict[str, Set[str]] = defaultdict(set)
        cfg_callees:  Dict[str, Set[str]] = defaultdict(set)
        all_bbs:      Set[str]            = set()

        for bb in bbs_desc:
            lbl = bb["label"]
            all_bbs.add(lbl)
            bb_to_fn[lbl] = fn_name

            for succ in bb.get("succs", []):
                cfg_adj[lbl].add(succ)

            for callee in bb.get("callees", []):
                cfg_callees[lbl].add(callee)
                # Add CG edge implied by call-site
                cg_adj[fn_name].add(callee)

        cfgs[fn_name] = {
            "adj":     dict(cfg_adj),
            "callees": dict(cfg_callees),
            "bbs":     all_bbs,
        }

    return ProgramGraph(
        cg_adj=dict(cg_adj),
        cfgs=cfgs,
        bb_to_fn=bb_to_fn,
    )



# Convenience wrapper: statement-to-statement distance
def statement_distance(
    pg:           ProgramGraph,
    source:       Statement,
    targets:      List[Statement],
    verbose:      bool = False,
) -> float:
    """
    High-level entry point: compute the AFLGo distance from *source*
    to the set *targets* (one-to-one or one-to-many).

    - If targets contains a single statement in the same function/BB,
      the distance is purely CFG-based.
    - If targets span multiple functions (same or different files),
      the call graph is used to bridge them.
    - Multiple targets are aggregated via AFLGo's harmonic mean.

    Returns
    -------
    float  distance  (0.0 = same BB; INF = unreachable)
    """
    if not targets:
        raise ValueError("targets must be non-empty")
    return pg.compute_distance(source, targets, verbose=verbose)



def _demo_same_file_single_target() -> None:
    """
    Same file, single target statement.

    Program sketch (one file: vuln.c)
    ──────────────────────────────────
    void helper(void) {          // function: helper
        BB_h1: ...               // vuln.c:5
        BB_h2: use_buf()         // vuln.c:10  ← TARGET
    }
    void process(char *buf) {    // function: process
        BB_p1: ...               // vuln.c:20
        BB_p2: helper()          // vuln.c:25  ← calls helper
        BB_p3: ...               // vuln.c:30  ← SOURCE
    }
    void main(void) {            // function: main
        BB_m1: process(input)    // vuln.c:40
    }
    """
    print("\n" + "═"*68)
    print("  Example 1 – Same file, single target")
    print("═"*68)

    functions = [
        {
            "name": "helper",
            "bbs": [
                {"label": "vuln.c:5",  "succs": ["vuln.c:10"], "callees": []},
                {"label": "vuln.c:10", "succs": [],            "callees": []},
            ],
        },
        {
            "name": "process",
            "bbs": [
                {"label": "vuln.c:20", "succs": ["vuln.c:25"], "callees": []},
                {"label": "vuln.c:25", "succs": ["vuln.c:30"], "callees": ["helper"]},
                {"label": "vuln.c:30", "succs": [],            "callees": []},
            ],
        },
        {
            "name": "main",
            "bbs": [
                {"label": "vuln.c:40", "succs": [], "callees": ["process"]},
            ],
        },
    ]

    pg = build_program_graph(functions, calls=[])

    source = Statement(file="vuln.c", line=30, function="process", bb="vuln.c:30")
    target = Statement(file="vuln.c", line=10, function="helper",  bb="vuln.c:10")

    d = statement_distance(pg, source, [target], verbose=True)
    print(f"\n  → statement_distance = {d:.6f}")


def _demo_same_file_multi_target() -> None:
    """
    Same file, multiple targets (harmonic mean).

    Program sketch (one file: multi.c)
    ──────────────────────────────────
    void sink_a(void) {           // function: sink_a
        BB_a1: free(p)            // multi.c:5   ← TARGET 1
    }
    void sink_b(void) {           // function: sink_b
        BB_b1: strcpy(dst, src)   // multi.c:15  ← TARGET 2
    }
    void caller(void) {           // function: caller
        BB_c1: sink_a()           // multi.c:30
        BB_c2: sink_b()           // multi.c:35
    }
    void entry(void) {            // function: entry
        BB_e1: caller()           // multi.c:50  ← SOURCE
    }
    """
    print("\n" + "═"*68)
    print("  Example 2 – Same file, multiple targets (harmonic mean)")
    print("═"*68)

    functions = [
        {
            "name": "sink_a",
            "bbs": [
                {"label": "multi.c:5",  "succs": [], "callees": []},
            ],
        },
        {
            "name": "sink_b",
            "bbs": [
                {"label": "multi.c:15", "succs": [], "callees": []},
            ],
        },
        {
            "name": "caller",
            "bbs": [
                {"label": "multi.c:30", "succs": ["multi.c:35"], "callees": ["sink_a"]},
                {"label": "multi.c:35", "succs": [],             "callees": ["sink_b"]},
            ],
        },
        {
            "name": "entry",
            "bbs": [
                {"label": "multi.c:50", "succs": [], "callees": ["caller"]},
            ],
        },
    ]

    pg = build_program_graph(functions, calls=[])

    source   = Statement(file="multi.c", line=50, function="entry",  bb="multi.c:50")
    target_a = Statement(file="multi.c", line=5,  function="sink_a", bb="multi.c:5")
    target_b = Statement(file="multi.c", line=15, function="sink_b", bb="multi.c:15")

    d = statement_distance(pg, source, [target_a, target_b], verbose=True)
    print(f"\n  → statement_distance = {d:.6f}")

    # Also show single-target distances for comparison
    d_a = statement_distance(pg, source, [target_a])
    d_b = statement_distance(pg, source, [target_b])
    print(f"  → distance to target_a only = {d_a:.6f}")
    print(f"  → distance to target_b only = {d_b:.6f}")
    print(f"  (harmonic mean result {d:.6f} < max({d_a:.6f}, {d_b:.6f}))")


def _demo_cross_file_targets() -> None:
    """
    Cross-file targets.

    Program spans two files: parse.c and net.c.
    The vulnerability (CVE-style) in net.c is reached from parse.c.

    parse.c
    ───────
    void parse_packet(buf) {       // function: parse_packet
        BB_pp1: validate(buf)      // parse.c:10
        BB_pp2: net_send(buf)      // parse.c:20  ← calls net.c function
        BB_pp3: cleanup()          // parse.c:30  ← SOURCE
    }

    net.c
    ─────
    void net_send(buf) {           // function: net_send
        BB_ns1: memcpy(dst, buf)   // net.c:50    ← TARGET 1 (buffer overread)
        BB_ns2: write(fd, dst)     // net.c:60    ← TARGET 2 (tainted write)
    }
    """
    print("\n" + "═"*68)
    print("  Example 3 – Cross-file targets")
    print("═"*68)

    functions = [
        {
            "name": "parse_packet",
            "bbs": [
                {"label": "parse.c:10", "succs": ["parse.c:20"], "callees": []},
                {"label": "parse.c:20", "succs": ["parse.c:30"], "callees": ["net_send"]},
                {"label": "parse.c:30", "succs": [],             "callees": []},
            ],
        },
        {
            "name": "net_send",
            "bbs": [
                {"label": "net.c:50", "succs": ["net.c:60"], "callees": []},
                {"label": "net.c:60", "succs": [],           "callees": []},
            ],
        },
    ]

    pg = build_program_graph(functions, calls=[])

    source   = Statement(file="parse.c", line=30, function="parse_packet", bb="parse.c:30")
    target_1 = Statement(file="net.c",   line=50, function="net_send",     bb="net.c:50")
    target_2 = Statement(file="net.c",   line=60, function="net_send",     bb="net.c:60")

    d = statement_distance(pg, source, [target_1, target_2], verbose=True)
    print(f"\n  → statement_distance = {d:.6f}")


def _demo_defects4j_style() -> None:
    """
    Defects4J / patch-distance style.

    Simulates the Chart-14 scenario: the source statement is in the
    buggy method (e.g. line 1800 of CategoryPlot.java) and the
    connected target statements are the two null-checks added by the
    developer patch (lines 1810 and 1855).

    CategoryPlot.java (single file)
    ───────────────────────────────
    removeDomainMarker():         // function: removeDomainMarker
        BB_rdm1: ...              // CategoryPlot.java:1800  ← SOURCE
        BB_rdm2: ...              // CategoryPlot.java:1803
        BB_rdm3: markers.remove() // CategoryPlot.java:1813  (buggy call)
        BB_rdm4: [null-check]     // CategoryPlot.java:1810  ← TARGET 1

    removeRangeMarker():          // function: removeRangeMarker
        BB_rrm1: ...              // CategoryPlot.java:1845  
        BB_rrm2: ...              // CategoryPlot.java:1848
        BB_rrm3: markers.remove() // CategoryPlot.java:1858  (buggy call)
        BB_rrm4: [null-check]     // CategoryPlot.java:1855  ← TARGET 2
    """
    print("\n" + "═"*68)
    print("  Example 4 – Defects4J Chart-14 patch distance style")
    print("═"*68)

    F = "CategoryPlot.java"

    functions = [
        {
            "name": "removeDomainMarker",
            "bbs": [
                {"label": f"{F}:1800", "succs": [f"{F}:1803"], "callees": []},
                {"label": f"{F}:1803", "succs": [f"{F}:1810", f"{F}:1813"], "callees": []},
                {"label": f"{F}:1810", "succs": [f"{F}:1813"], "callees": []},   # null-check
                {"label": f"{F}:1813", "succs": [],            "callees": []},   # markers.remove()
            ],
        },
        {
            "name": "removeRangeMarker",
            "bbs": [
                {"label": f"{F}:1845", "succs": [f"{F}:1848"], "callees": []},
                {"label": f"{F}:1848", "succs": [f"{F}:1855", f"{F}:1858"], "callees": []},
                {"label": f"{F}:1855", "succs": [f"{F}:1858"], "callees": []},  # null-check
                {"label": f"{F}:1858", "succs": [],            "callees": []},  # markers.remove()
            ],
        },
        {
            "name": "CategoryPlot",   # hypothetical caller of both methods
            "bbs": [
                {"label": f"{F}:100", "succs": [],
                 "callees": ["removeDomainMarker", "removeRangeMarker"]},
            ],
        },
    ]

    pg = build_program_graph(functions, calls=[])

    source  = Statement(file=F, line=1800, function="removeDomainMarker", bb=f"{F}:1800")
    target1 = Statement(file=F, line=1810, function="removeDomainMarker", bb=f"{F}:1810")
    target2 = Statement(file=F, line=1855, function="removeRangeMarker",  bb=f"{F}:1855")

    # One-to-one (same method, direct CFG path)
    d1 = statement_distance(pg, source, [target1], verbose=False)
    print(f"  Distance source → target1 (same function) = {d1:.6f}")

    # One-to-one (cross-function via CG)
    d2 = statement_distance(pg, source, [target2], verbose=False)
    print(f"  Distance source → target2 (cross function) = {d2:.6f}")

    # One-to-many (both targets, harmonic mean)
    d12 = statement_distance(pg, source, [target1, target2], verbose=True)
    print(f"\n  → One-to-many distance = {d12:.6f}")
    print(f"     (harmonic mean of {d1:.6f} and {d2:.6f})")



# Entry point
if __name__ == "__main__":
    _demo_same_file_single_target()
    _demo_same_file_multi_target()
    _demo_cross_file_targets()
    _demo_defects4j_style()
    print("\nAll demos completed.\n")