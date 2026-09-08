"""Operation graph: nodes with deterministic ids, dependencies, topological order.

Node kinds:
  track node   "track:<id>"  reads a source; implicit TRIM (track.range) and GAIN (track.gain_db) are materialised
                             as explicit nodes "op:<track>.range" / "op:<track>.gain" so the graph is honest
  op node      "op:<id>"     one AudioOperation
Every node has: node_id, type, inputs (node ids), parameters, dependencies (transitive closure is not stored), and
an identity: sha256 of canonical {type, parameters, inputs' identities, tool version, source fingerprints}, computed
by the executor once source fingerprints are known (graph.identity()). Nothing here depends on time or randomness."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .canonical import stable_hash
from .errors import AudioError
from .model import AudioProject, AudioTrack


@dataclass
class Node:
    node_id: str
    type: str                       # SOURCE_TRACK or an operation type
    inputs: List[str]               # node ids
    parameters: Dict[str, Any] = field(default_factory=dict)
    track: Optional[AudioTrack] = None
    implicit: bool = False
    consumers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"node_id": self.node_id, "type": self.type, "inputs": list(self.inputs), "parameters": self.parameters, "implicit": self.implicit}
        if self.track is not None:
            d["track_id"] = self.track.track_id
            d["source_id"] = self.track.source_id
        return d


class OperationGraph:
    def __init__(self, project: AudioProject):
        self.project = project
        self.nodes: Dict[str, Node] = {}
        self.order: List[str] = []
        self.output_nodes: Dict[str, str] = {}     # output_id -> node id
        self._build()

    # ---- construction
    def _add(self, node: Node) -> None:
        if node.node_id in self.nodes:
            raise AudioError("DEPENDENCY_ERROR", f"duplicate node id {node.node_id!r} (an op_id collides with an implicit track node)", {"node_id": node.node_id})
        self.nodes[node.node_id] = node

    def _build(self) -> None:
        p = self.project
        track_tail: Dict[str, str] = {}
        for t in p.tracks:
            base = f"track:{t.track_id}"
            self._add(Node(base, "SOURCE_TRACK", [], {"source_id": t.source_id, "channel_layout": t.channel_layout, "label": t.label}, track=t))
            tail = base
            if t.range is not None:
                nid = f"op:{t.track_id}.range"
                self._add(Node(nid, "TRIM", [tail], {"start": t.range.start, "end": t.range.end}, implicit=True))
                tail = nid
            if t.gain_db:
                nid = f"op:{t.track_id}.gain"
                self._add(Node(nid, "GAIN", [tail], {"gain_db": t.gain_db, "audio_stream": 0}, implicit=True))
                tail = nid
            track_tail[base] = tail
        for op in p.operations:
            nid = f"op:{op.op_id}"
            inputs = [track_tail.get(ref, ref) for ref in op.inputs]
            self._add(Node(nid, op.type, inputs, dict(op.parameters)))
        for node in self.nodes.values():
            for i in node.inputs:
                if i not in self.nodes:
                    raise AudioError("MISSING_INPUT", f"node {node.node_id!r} depends on unknown node {i!r}", {"node_id": node.node_id, "ref": i})
                self.nodes[i].consumers.append(node.node_id)
        for out in p.outputs:
            self.output_nodes[out.output_id] = track_tail.get(out.operation, out.operation)
        self.order = self._toposort()
        needed = self._reachable(list(self.output_nodes.values()))
        unused = [n for n in self.order if n not in needed and not n.startswith("track:")]
        if unused:
            raise AudioError("DEPENDENCY_ERROR", f"operations not connected to any output: {unused}", {"unreachable": unused})
        unused_tracks = [n for n in self.order if n.startswith("track:") and n not in needed]
        if unused_tracks:
            raise AudioError("DEPENDENCY_ERROR", f"tracks not connected to any output: {unused_tracks}", {"unreachable": unused_tracks})

    def _toposort(self) -> List[str]:
        """Deterministic Kahn ordering: among ready nodes, the smallest id first (stable across processes)."""
        indeg = {n: len(node.inputs) for n, node in self.nodes.items()}
        ready = sorted(n for n, d in indeg.items() if d == 0)
        order: List[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for c in self.nodes[n].consumers:
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
                    ready.sort()
        if len(order) != len(self.nodes):
            cyc = sorted(n for n, d in indeg.items() if d > 0)
            raise AudioError("DEPENDENCY_ERROR", f"operation graph has a cycle among {cyc}", {"cycle": cyc})
        return order

    def _reachable(self, starts: List[str]) -> set:
        seen: set = set()
        stack = list(starts)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.nodes[n].inputs)
        return seen

    # ---- identity
    def identities(self, source_fingerprints: Dict[str, str], tool_versions: Dict[str, str]) -> Dict[str, str]:
        """Deterministic operation identity per node, in topological order. Sources contribute their sha256; each
        operation contributes type + effective parameters + input identities + the tool version that will run it."""
        ids: Dict[str, str] = {}
        for n in self.order:
            node = self.nodes[n]
            if node.type == "SOURCE_TRACK":
                assert node.track is not None
                fp = source_fingerprints.get(node.track.source_id)
                if not fp:
                    raise AudioError("INTERNAL_ERROR", f"no fingerprint for source {node.track.source_id!r}")
                ids[n] = stable_hash({"kind": "source_track", "source_sha256": fp, "channel_layout": node.track.channel_layout})
            else:
                ids[n] = stable_hash({"kind": "operation", "type": node.type, "parameters": node.parameters,
                                      "inputs": [ids[i] for i in node.inputs], "tool_versions": tool_versions})
        return ids

    def to_dict(self) -> Dict[str, Any]:
        return {"order": list(self.order), "nodes": [self.nodes[n].to_dict() for n in self.order],
                "outputs": dict(self.output_nodes)}
