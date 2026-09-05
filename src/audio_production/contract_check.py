"""Contract consistency: (a) the live contract against the implementation it describes, (b) a saved contract document
against the live one (drift), classifying every difference as breaking or additive.

`audio-production contract --check [FILE|-]` runs (a) and, with a file, (b); exit 1 on any implementation problem or
breaking drift, 0 when consistent or only additive. CI pins tests/contract/contract.json and fails when a field an
agent relies on changes without a review; regenerate the pin with `audio-production contract --json > tests/contract/contract.json`
when the change is intended.

PINNED_BLOCKS is exactly what video-production-agent's audio-production adapter compares (its DRIFT_KEYS) plus
`operations` (it compares every operation's type / inputs / parameters / tool / required_capabilities /
keeps_timeline / deterministic) and `provides` (cross-repository Capability ids). A change inside them is breaking for
that adapter and makes it mark this Skill MISSING until it re-pins; a new key outside them is additive."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import SKILL_ID, VERSION
from .adapter import FLAGS_USED, SUPPORTED_CONTRACT_VERSION, TOOLS_USED
from .contract import CAPABILITY_IDS, CONTRACT_SCHEMA_ID, skill_contract
from .errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES
from .executor import TOOL_FOR
from .model import OPERATION_TYPES, OUTPUT_FORMATS, UNSUPPORTED_OPERATIONS

PINNED_BLOCKS = ("schema", "skill_id", "version", "kind", "tools", "operations", "provides", "unsupported_operations", "output_formats",
                 "intermediate_format", "channel_layouts", "sample_rates", "execution", "ffmpeg_skill", "request", "response", "provenance",
                 "schema_versions", "errors")
PINNED_OPERATION_FIELDS = ("type", "inputs", "parameters", "tool", "required_capabilities", "keeps_timeline", "deterministic")
DOCS = ("README.md",)


def verify_implementation(contract: Optional[Dict[str, Any]] = None, root: Optional[str] = None) -> List[str]:
    """Problems between the (live) contract and the code / docs it describes."""
    c = contract if contract is not None else skill_contract()
    p: List[str] = []
    if c.get("schema") != CONTRACT_SCHEMA_ID or c.get("skill_id") != SKILL_ID or c.get("id") != SKILL_ID or c.get("version") != VERSION:
        p.append("contract header (schema / skill_id / id / version) does not match the package")
    if c.get("kind") != "execution":
        p.append("contract.kind must be execution")
    ops: Dict[str, Dict[str, Any]] = {str(o.get("type")): o for o in c.get("operations") or [] if isinstance(o, dict)}
    if set(ops) != set(OPERATION_TYPES):
        p.append(f"contract.operations {sorted(ops)} != model.OPERATION_TYPES {sorted(OPERATION_TYPES)}")
    for t, o in ops.items():
        spec = OPERATION_TYPES.get(t)
        if spec is None:
            continue
        tool, extra = TOOL_FOR[t]
        if o.get("tool") != f"ffmpeg-skill/{tool}":
            p.append(f"operation {t}: contract tool {o.get('tool')!r} != executor.TOOL_FOR ffmpeg-skill/{tool}")
        if tool not in TOOLS_USED:
            p.append(f"operation {t}: tool {tool!r} is not in adapter.TOOLS_USED")
        if list(o.get("required_capabilities") or []) != ["ffmpeg-skill", "ffmpeg", "ffprobe", "encoder:pcm_s16le", *extra]:
            p.append(f"operation {t}: required_capabilities differ from executor.TOOL_FOR")
        if set(o.get("parameters") or {}) != set(spec["parameters"]):
            p.append(f"operation {t}: parameters {sorted(o.get('parameters') or {})} != model {sorted(spec['parameters'])}")
        if (o.get("inputs") or {}).get("min") != spec["inputs"][0] or (o.get("inputs") or {}).get("max") != spec["inputs"][1]:
            p.append(f"operation {t}: input arity differs from model")
        for name, ps in (o.get("parameters") or {}).items():
            for k in ("type", "required", "description"):
                if k not in ps:
                    p.append(f"operation {t}.{name}: parameter schema lacks {k!r}")
    unsupported = {str(u.get("type")): u for u in c.get("unsupported_operations") or [] if isinstance(u, dict)}
    if set(unsupported) != set(UNSUPPORTED_OPERATIONS):
        p.append("contract.unsupported_operations differ from model.UNSUPPORTED_OPERATIONS")
    if set(unsupported) & set(ops):
        p.append("an operation is both supported and unsupported")
    prov: Dict[str, Dict[str, Any]] = {str(x.get("operation")): x for x in c.get("provides") or [] if isinstance(x, dict)}
    if set(prov) != set(OPERATION_TYPES) or set(prov) != set(CAPABILITY_IDS):
        p.append("contract.provides does not cover exactly model.OPERATION_TYPES / contract.CAPABILITY_IDS")
    for t, x in prov.items():
        if x.get("id") != CAPABILITY_IDS.get(t) or x.get("tool_id") != f"{SKILL_ID}/run" or x.get("lifecycle") not in ("PROPOSED", "EXPERIMENTAL", "STABLE", "DEPRECATED", "RETIRED"):
            p.append(f"provides[{t}] is malformed")
    tools = c.get("tools") or []
    if len(tools) != 1 or tools[0].get("tool_id") != f"{SKILL_ID}/run" or sorted(tools[0].get("operations") or []) != sorted(OPERATION_TYPES):
        p.append("contract.tools must be the single audio-production/run ToolSpec listing every operation")
    if c.get("output_formats") is None or set(c["output_formats"]) != set(OUTPUT_FORMATS):
        p.append("contract.output_formats differ from model.OUTPUT_FORMATS")
    errs = c.get("errors") or {}
    if list(errs.get("codes") or []) != list(ERROR_CODES) or errs.get("exit_codes") != EXIT_CODES or errs.get("retryable") != {k: ERROR_TABLE[k][1] for k in ERROR_CODES}:
        p.append("contract.errors differ from errors.py")
    ex = c.get("execution") or {}
    for k in ("shell", "arbitrary_executables", "arbitrary_filters", "network", "input_mutation", "ai"):
        if ex.get(k) is not False:
            p.append(f"execution.{k} must be false")
    fs = c.get("ffmpeg_skill") or {}
    if fs.get("contract_version") != SUPPORTED_CONTRACT_VERSION or fs.get("tools_used") != list(TOOLS_USED) or fs.get("flags_used") != {k: list(v) for k, v in FLAGS_USED.items()}:
        p.append("contract.ffmpeg_skill differs from adapter.TOOLS_USED / FLAGS_USED")
    p += verify_docs(root)
    return p


def verify_docs(root: Optional[str] = None) -> List[str]:
    """Every implemented and every declared-unsupported operation type is named in the README."""
    base = root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p: List[str] = []
    for doc in DOCS:
        path = os.path.join(base, doc)
        if not os.path.isfile(path):
            continue     # an installed wheel has no README next to the package
        text = open(path, encoding="utf-8").read()
        for t in list(OPERATION_TYPES) + list(UNSUPPORTED_OPERATIONS):
            if f"`{t}`" not in text:
                p.append(f"{doc} does not mention `{t}`")
    return p


def _changed_paths(saved: Any, live: Any, path: str, out: List[str]) -> None:
    if isinstance(saved, dict) and isinstance(live, dict):
        for k in sorted(set(saved) | set(live)):
            _changed_paths(saved.get(k, _MISSING), live.get(k, _MISSING), f"{path}/{k}" if path else k, out)
    elif saved != live:
        out.append(path)


_MISSING = object()


def contract_drift(saved: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """{"breaking": [...], "additive": [...]} between a saved contract and the live one. Breaking: any difference
    inside PINNED_BLOCKS, any key removed anywhere, or a schema change. Additive: keys added outside the pinned blocks."""
    live = live if live is not None else skill_contract()
    breaking: List[str] = []
    additive: List[str] = []
    if saved.get("schema") != live.get("schema"):
        breaking.append(f"schema: saved {saved.get('schema')!r} != live {live.get('schema')!r}")
    for block in PINNED_BLOCKS:
        if block == "operations":
            so = {str(o.get("type")): o for o in saved.get("operations") or [] if isinstance(o, dict)}
            lo = {str(o.get("type")): o for o in live.get("operations") or [] if isinstance(o, dict)}
            for t in sorted(set(so) | set(lo)):
                if t not in lo:
                    breaking.append(f"operations/{t}: removed")
                elif t not in so:
                    breaking.append(f"operations/{t}: added (an agent must re-pin to use it)")
                else:
                    for f in PINNED_OPERATION_FIELDS:
                        if so[t].get(f) != lo[t].get(f):
                            breaking.append(f"operations/{t}/{f}: changed")
            continue
        if saved.get(block, _MISSING) != live.get(block, _MISSING):
            paths: List[str] = []
            _changed_paths(saved.get(block, _MISSING), live.get(block, _MISSING), block, paths)
            breaking += [f"{x}: changed" for x in paths] or [f"{block}: changed"]
    for k in sorted(set(saved) | set(live)):
        if k in PINNED_BLOCKS or k == "schema":
            continue
        if k not in live:
            breaking.append(f"{k}: removed")
        elif k not in saved:
            additive.append(f"{k}: added")
        elif saved[k] != live[k]:
            paths = []
            _changed_paths(saved[k], live[k], k, paths)
            for x in paths:
                additive.append(f"{x}: changed (outside the pinned blocks)")
    return {"breaking": breaking, "additive": additive}


def run_check(saved: Optional[Dict[str, Any]] = None, root: Optional[str] = None) -> Dict[str, Any]:
    """One report: implementation problems, drift classification, status ok | additive | breaking | fail (fail = the
    live contract is inconsistent with the code / docs, whatever the saved copy says)."""
    live = skill_contract()
    problems = verify_implementation(live, root)
    try:
        json.loads(json.dumps(live, allow_nan=False))
    except ValueError as e:
        problems.append(f"live contract is not serialisable: {e}")
    drift = contract_drift(saved, live) if saved is not None else {"breaking": [], "additive": []}
    if problems:
        status = "fail"
    elif drift["breaking"]:
        status = "breaking"
    elif drift["additive"]:
        status = "additive"
    else:
        status = "ok"
    return {"schema": f"{SKILL_ID}/contract-check@1", "skill": {"id": SKILL_ID, "version": VERSION}, "status": status,
            "pinned_blocks": list(PINNED_BLOCKS), "pinned_operation_fields": list(PINNED_OPERATION_FIELDS),
            "problems": problems, "drift": drift, "compared_with_saved": saved is not None,
            "exit_code": 0 if status in ("ok", "additive") else 1}
