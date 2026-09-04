"""Executor: request -> validated project -> operation graph -> plan -> ffmpeg-skill calls -> validated artifacts.

Pipeline (docs/architecture.md):
  parse_request (model)  ->  PathPolicy (security)  ->  OperationGraph (graph)  ->  probe + fingerprint sources
  ->  deterministic identities  ->  plan (tool selection, argv templates, expected timeline)  ->  execute in order
  ->  validate every artifact (exists, size, audio stream, duration, channels, sample rate, hash, loudness)
  ->  export outputs  ->  response document (schema audio-production/response@1)

Every intermediate is PCM WAV in <workspace>/.audio-production/<project_id>/<identity16>.wav with a manifest
(<identity16>.json) so a re-run with identical identity reuses it instead of re-processing (idempotency)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill, ToolRun, fmt_db, fmt_seconds
from .canonical import sha256_file, stable_hash
from .errors import AudioError
from .graph import Node, OperationGraph
from .model import CHANNEL_LAYOUTS, INTERMEDIATE_FORMAT, OUTPUT_FORMATS, AudioRequest, TimeRange, parse_request
from .security import PathPolicy
from .timeline import AudioSegment, apply_silence_rules, base_segments, complement, cut, fade_ranges, mix, total_duration, trim

RESPONSE_SCHEMA_ID = f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}"
WORK_DIR_NAME = ".audio-production"
DURATION_TOLERANCE = 0.1     # seconds; ffmpeg-skill/cut lands on packet boundaries (measured about +17 ms on 48 kHz PCM)
MANIFEST_SCHEMA = f"{SKILL_ID}/manifest@1"

# tool selection per node type (ffmpeg-skill tool, extra capabilities beyond ffmpeg/ffprobe)
TOOL_FOR: Dict[str, Tuple[str, List[str]]] = {
    "SOURCE_TRACK": ("probe", []),
    "GAIN": ("audio", ["filter:volume"]),
    "TRIM": ("cut", []),
    "CUT": ("cut", []),
    "SILENCE_REMOVE": ("cut", []),
    "FADE_IN": ("audio", ["filter:afade"]),
    "FADE_OUT": ("audio", ["filter:afade"]),
    "NORMALIZE": ("loudness", ["filter:loudnorm"]),
    "MIX": ("audio", ["filter:amix"]),
    "MONO": ("audio", ["filter:pan"]),
    "STEREO": ("audio", ["filter:aformat"]),
    "DOWNMIX": ("audio", ["filter:pan"]),
    "NOISE_REDUCTION": ("audio", ["filter:afftdn"]),
    "EXPORT": ("audio", []),
}


@dataclass
class Artifact:
    path: Path
    duration: float
    channels: int
    sample_rate: int
    codec: Optional[str]
    size: int
    sha256: str
    channel_layout: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "duration": self.duration, "channels": self.channels, "channel_layout": self.channel_layout,
                "sample_rate": self.sample_rate, "codec": self.codec, "size": self.size, "sha256": self.sha256}


@dataclass
class NodeState:
    node: Node
    identity: str = ""
    tool: str = ""
    capabilities: List[str] = field(default_factory=list)
    segments: List[AudioSegment] = field(default_factory=list)
    artifact: Optional[Artifact] = None
    status: str = "planned"       # planned | reused | completed | failed | skipped | cancelled
    seconds: float = 0.0
    tool_commands: List[str] = field(default_factory=list)
    measurements: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None
    input_hashes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"node_id": self.node.node_id, "operation_id": self.identity, "type": self.node.type, "implicit": self.node.implicit,
                             "tool": f"ffmpeg-skill/{self.tool}" if self.tool else None, "required_capabilities": self.capabilities,
                             "inputs": list(self.node.inputs), "parameters": self.node.parameters, "status": self.status,
                             "segments": [s.to_dict() for s in self.segments], "expected_duration": round(total_duration(self.segments), 6) if self.segments else None,
                             "input_hashes": self.input_hashes, "seconds": self.seconds}
        d["artifact"] = self.artifact.to_dict() if self.artifact else None
        d["tool_commands_observed"] = list(self.tool_commands)
        d["measurements"] = self.measurements
        if self.error:
            d["error"] = self.error
        return d


class Executor:
    def __init__(self, policy: PathPolicy, skill: FfmpegSkill, dry_run: bool = False, reuse: bool = True, timeout: Optional[float] = None,
                 tool_versions: Optional[Dict[str, str]] = None, capabilities: Optional[Dict[str, str]] = None):
        self.policy = policy
        self.skill = skill
        self.dry_run = dry_run
        self.reuse = reuse
        self.timeout = timeout
        self.tool_versions = dict(tool_versions or {})
        self.capabilities = dict(capabilities or {})   # capability -> supported | unsupported | unknown (doctor)
        self.warnings: List[str] = []
        self._states: Dict[str, NodeState] = {}

    # ---- entry points
    def response(self, document: Any, validate_only: bool = False) -> Dict[str, Any]:
        """Always returns one response document; never raises."""
        try:
            req = parse_request(document)
            if validate_only:
                graph = OperationGraph(req.project)
                return self._envelope(True, "ok", {"validation": {"ok": True, "graph": graph.to_dict()}, "dry_run": True})
            return self._run(req)
        except AudioError as e:
            return self._envelope(False, "cancelled" if e.code == "CANCELLED" else "error", {"error": e.to_dict(), "dry_run": self.dry_run})
        except Exception as e:  # a bug in this skill: still one document
            err = AudioError("INTERNAL_ERROR", f"{type(e).__name__}: {e}")
            return self._envelope(False, "error", {"error": err.to_dict(), "dry_run": self.dry_run})

    def _envelope(self, ok: bool, status: str, body: Dict[str, Any]) -> Dict[str, Any]:
        doc: Dict[str, Any] = {"schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": ok, "status": status}
        doc.update(body)
        doc.setdefault("warnings", list(self.warnings))
        return doc

    # ---- run
    def _run(self, req: AudioRequest) -> Dict[str, Any]:
        project = req.project
        timeout = req.options.get("timeout") or self.timeout
        graph = OperationGraph(project)
        # sources: resolve, probe, fingerprint (read-only, also under dry-run)
        sources: Dict[str, Dict[str, Any]] = {}
        for s in project.sources:
            resolved = self.policy.resolve_input(s.path, f"source {s.source_id!r}")
            meta = self.skill.probe(str(resolved), timeout)
            audio = meta.get("audio")
            if not audio or not audio.get("channels"):
                raise AudioError("INVALID_INPUT", f"source {s.source_id!r} has no audio stream", {"source_id": s.source_id, "reason": "no_audio_stream"})
            if meta.get("video"):
                # compatibility gap: ffmpeg-skill/audio always maps the video stream into its output, so an audio-only
                # artifact cannot be produced from a video container through ffmpeg-skill 0.9 (docs/ffmpeg-skill.md)
                raise AudioError("INVALID_INPUT", f"source {s.source_id!r} contains a video stream; audio-production-skill accepts audio-only sources "
                                 "(extract the audio track first; ffmpeg-skill has no audio-extraction tool)", {"source_id": s.source_id, "reason": "video_stream_not_supported"})
            duration = float(meta.get("duration") or 0.0)
            if duration <= 0:
                raise AudioError("INVALID_INPUT", f"source {s.source_id!r} has no positive duration", {"source_id": s.source_id, "reason": "no_duration"})
            sources[s.source_id] = {"source_id": s.source_id, "path": str(resolved), "sha256": sha256_file(str(resolved)), "size": os.path.getsize(str(resolved)),
                                    "duration": duration, "channels": int(audio["channels"]), "sample_rate": int(audio.get("sample_rate") or 0),
                                    "codec": audio.get("codec"), "channel_layout": audio.get("channel_layout"), "has_video": bool(meta.get("video"))}
        # outputs: resolve write paths, refuse collisions
        input_paths = {v["path"] for v in sources.values()}
        outputs: Dict[str, Path] = {}
        for o in project.outputs:
            target = self.policy.resolve_write_path(o.path, f"output {o.output_id!r}")
            if str(target) in input_paths:
                raise AudioError("OUTPUT_ERROR", f"output {o.output_id!r} would overwrite an input", {"reason": "input_output_collision", "path": str(target)})
            if target.exists() and not o.overwrite:
                raise AudioError("OUTPUT_ERROR", f"output {o.output_id!r} already exists (set overwrite: true to replace it)", {"reason": "exists", "path": str(target)})
            fmt = OUTPUT_FORMATS[o.format]
            if not self._capability_available(fmt["capability"]):
                raise AudioError("UNSUPPORTED_FORMAT", f"output {o.output_id!r}: encoder for {o.format!r} is not available ({fmt['capability']})",
                                 {"output_id": o.output_id, "capability": fmt["capability"]})
            outputs[o.output_id] = target
        work_dir = self.policy.resolve_work_dir(os.path.join(WORK_DIR_NAME, project.project_id))
        if str(work_dir) in input_paths or any(str(t).startswith(str(work_dir) + os.sep) for t in outputs.values()):
            raise AudioError("OUTPUT_ERROR", "outputs may not live inside the work directory", {"reason": "output_in_work_dir", "work_dir": str(work_dir)})

        identities = graph.identities({k: v["sha256"] for k, v in sources.items()}, self.tool_versions)
        states: Dict[str, NodeState] = {}
        for n in graph.order:
            node = graph.nodes[n]
            st = NodeState(node, identities[n])
            st.tool, st.capabilities = self._select_tool(node)
            states[n] = st
        self._states = states
        # plan: timeline + tool argv templates (pure; also the dry-run output)
        for n in graph.order:
            self._plan_node(states, states[n], sources)
        plan = self._plan_document(graph, states, sources, outputs, project, work_dir)
        if self.dry_run:
            return self._envelope(True, "ok", {"dry_run": True, "plan": plan, "results": [states[n].to_dict() for n in graph.order], "outputs": plan["outputs"]})

        # execute
        work_dir.mkdir(parents=True, exist_ok=True)
        cancelled = False
        failure: Optional[AudioError] = None
        for n in graph.order:
            st = states[n]
            try:
                self._execute_node(states, st, sources, work_dir, timeout, req.options.get("reuse_intermediates", True) and self.reuse)
            except AudioError as e:
                st.status = "cancelled" if e.code == "CANCELLED" else "failed"
                st.error = e.to_dict()
                failure = e
                cancelled = e.code == "CANCELLED"
                break
        out_results: List[Dict[str, Any]] = []
        if failure is None:
            for o in project.outputs:
                try:
                    out_results.append(self._export(states[graph.output_nodes[o.output_id]], o, outputs[o.output_id], sources, timeout))
                except AudioError as e:
                    failure = e
                    cancelled = e.code == "CANCELLED"
                    out_results.append({"output_id": o.output_id, "status": "cancelled" if cancelled else "failed", "path": str(outputs[o.output_id]), "error": e.to_dict()})
                    break
        for n in graph.order:
            if states[n].status == "planned":
                states[n].status = "skipped"
        body: Dict[str, Any] = {"dry_run": False, "plan": plan, "results": [states[n].to_dict() for n in graph.order], "outputs": out_results,
                                "tool_runs": [self._tool_run_dict(r) for r in self.skill.runs]}
        if failure is not None:
            body["error"] = failure.to_dict()
            return self._envelope(False, "cancelled" if cancelled else "error", body)
        return self._envelope(True, "ok", body)

    # ---- planning
    def _select_tool(self, node: Node) -> Tuple[str, List[str]]:
        tool, extra = TOOL_FOR[node.type]
        caps = ["ffmpeg-skill", "ffmpeg", "ffprobe", *extra]
        if node.type != "SOURCE_TRACK":
            caps.append(OUTPUT_FORMATS[INTERMEDIATE_FORMAT]["capability"])
        missing = [c for c in caps if not self._capability_available(c)]
        if missing:
            raise AudioError("TOOL_ERROR", f"{node.type} needs capabilities that are not available: {missing}", {"node_id": node.node_id, "missing": missing}, retryable=False)
        return tool, caps

    def _capability_available(self, cap: str) -> bool:
        """unknown counts as available: the tool run reports the truth; only a detected 'unsupported' blocks planning."""
        return self.capabilities.get(cap, "unknown") != "unsupported"

    def _plan_node(self, states: Dict[str, NodeState], st: NodeState, sources: Dict[str, Dict[str, Any]]) -> None:
        node = st.node
        p = node.parameters
        ins = [states[i] for i in node.inputs]
        if node.type == "SOURCE_TRACK":
            src = sources[node.track.source_id]  # type: ignore[union-attr]
            layout = node.track.channel_layout   # type: ignore[union-attr]
            if layout is not None and CHANNEL_LAYOUTS[layout] != src["channels"]:
                raise AudioError("INVALID_CHANNEL_LAYOUT", f"track {node.track.track_id!r} expects {layout} ({CHANNEL_LAYOUTS[layout]} ch) but the source has {src['channels']} channel(s)",  # type: ignore[union-attr]
                                 {"track_id": node.track.track_id, "expected": layout, "channels": src["channels"]})  # type: ignore[union-attr]
            st.segments = base_segments(src["source_id"], src["duration"])
            st.measurements = {"channels": src["channels"], "sample_rate": src["sample_rate"], "duration": src["duration"]}
            return
        where = f"operation {node.node_id}"
        if node.type == "TRIM":
            st.segments = trim(ins[0].segments, TimeRange(p["start"], p["end"]), where)
        elif node.type == "CUT":
            st.segments = cut(ins[0].segments, [TimeRange(r["start"], r["end"]) for r in p["remove"]], where)
        elif node.type == "SILENCE_REMOVE":
            ranges = apply_silence_rules([TimeRange(r["start"], r["end"]) for r in p["ranges"]], p.get("margin", 0.0), p.get("min_duration", 0.0))
            st.measurements["effective_ranges"] = [r.to_dict() for r in ranges]
            st.segments = cut(ins[0].segments, ranges, where) if ranges else list(ins[0].segments)
        elif node.type in ("FADE_IN", "FADE_OUT"):
            fade_ranges(ins[0].segments, p["duration"] if node.type == "FADE_IN" else None, p["duration"] if node.type == "FADE_OUT" else None)
            st.segments = list(ins[0].segments)
        elif node.type == "MIX":
            st.segments = mix([i.segments for i in ins])
            if all(lv["mute"] for lv in p["levels"]):
                raise AudioError("INVALID_REQUEST", f"{where}: every MIX input is muted", {"node_id": node.node_id})
        elif node.type in ("MONO", "STEREO", "DOWNMIX"):
            ch = self._expected_channels(states, ins[0], sources)
            if node.type == "MONO" and ch != 2:
                raise AudioError("INVALID_CHANNEL_LAYOUT", f"{where}: MONO needs a 2-channel input (got {ch}); use DOWNMIX for surround, nothing for mono", {"channels": ch})
            if node.type == "STEREO" and ch not in (1, 2):
                raise AudioError("INVALID_CHANNEL_LAYOUT", f"{where}: STEREO needs a 1- or 2-channel input (got {ch}); use DOWNMIX for surround", {"channels": ch})
            if node.type == "DOWNMIX" and ch not in (6, 8):
                raise AudioError("INVALID_CHANNEL_LAYOUT", f"{where}: DOWNMIX needs a 5.1 or 7.1 input (got {ch} channels)", {"channels": ch})
            st.segments = list(ins[0].segments)
        else:
            st.segments = list(ins[0].segments)

    def _expected_channels(self, states: Dict[str, NodeState], st: NodeState, sources: Dict[str, Dict[str, Any]]) -> int:
        """Channel count a node's artifact will have, derived without running anything."""
        node = st.node
        if node.type == "SOURCE_TRACK":
            return int(sources[node.track.source_id]["channels"])  # type: ignore[union-attr]
        if node.type in ("MONO",):
            return 1
        if node.type in ("STEREO", "DOWNMIX"):
            return 2
        return self._expected_channels(states, states[node.inputs[0]], sources)

    def _plan_document(self, graph: OperationGraph, states: Dict[str, NodeState], sources: Dict[str, Dict[str, Any]], outputs: Dict[str, Path],
                       project: Any, work_dir: Path) -> Dict[str, Any]:
        caps = sorted({c for st in states.values() for c in st.capabilities} | {OUTPUT_FORMATS[o.format]["capability"] for o in project.outputs})
        plan_id = stable_hash({"identities": {n: states[n].identity for n in graph.order}, "outputs": [(o.output_id, o.format) for o in project.outputs]})
        return {"plan_id": plan_id, "project_id": project.project_id, "work_dir": str(work_dir), "graph": graph.to_dict(),
                "sources": {k: {kk: vv for kk, vv in v.items()} for k, v in sources.items()},
                "steps": [{"node_id": n, "operation_id": states[n].identity, "type": states[n].node.type, "tool": f"ffmpeg-skill/{states[n].tool}",
                           "inputs": list(states[n].node.inputs), "parameters": states[n].node.parameters, "intermediate": str(self._intermediate_path(work_dir, states[n])),
                           "expected_duration": round(total_duration(states[n].segments), 6)} for n in graph.order if states[n].node.type != "SOURCE_TRACK"],
                "outputs": [{"output_id": o.output_id, "node_id": graph.output_nodes[o.output_id], "path": str(outputs[o.output_id]), "format": o.format,
                             "tool": "ffmpeg-skill/audio", "required_capabilities": [OUTPUT_FORMATS[o.format]["capability"]], "expect": o.expect,
                             "expected_duration": round(total_duration(states[graph.output_nodes[o.output_id]].segments), 6)} for o in project.outputs],
                "required_capabilities": caps, "tool_versions": dict(self.tool_versions),
                "intermediate_format": INTERMEDIATE_FORMAT, "duration_tolerance": DURATION_TOLERANCE}

    def _intermediate_path(self, work_dir: Path, st: NodeState) -> Path:
        return work_dir / f"{st.identity[:16]}.{INTERMEDIATE_FORMAT}"

    # ---- execution
    @staticmethod
    def sidecars(out_path: Path) -> List[Path]:
        """Temporary files a node may create next to its intermediate (decoded copy, MIX fold steps)."""
        base = str(out_path)[:-4]
        return [Path(f"{base}.decode.wav")] + [Path(f"{base}.mix{k}.wav") for k in range(1, 8)]

    def _argv(self, st: NodeState, states: Dict[str, NodeState], sources: Dict[str, Dict[str, Any]], out_path: Path) -> List[Tuple[str, List[str]]]:
        """One or more ffmpeg-skill invocations (tool, argv without the script and --json) for this node. Every value
        is a formatted number or a resolved absolute path; nothing from the request is passed through verbatim."""
        node, p = st.node, st.node.parameters
        if not node.inputs:
            raise AudioError("INTERNAL_ERROR", f"{node.type} has no inputs")
        src: str = self._artifact_path(states[node.inputs[0]], sources)
        o = str(out_path)
        if node.type == "GAIN":
            return [("audio", [src, "--gain", fmt_db(p["gain_db"]), "-o", o])]
        if node.type in ("TRIM", "CUT", "SILENCE_REMOVE"):
            # ffmpeg-skill/cut stream-copies; a compressed source would be copied verbatim into the .wav container,
            # so a non-PCM input is decoded to WAV first (ffmpeg-skill/audio pass-through)
            pre: List[Tuple[str, List[str]]] = []
            first = states[node.inputs[0]]
            if first.artifact is None or first.artifact.codec != OUTPUT_FORMATS[INTERMEDIATE_FORMAT]["codec"]:
                dec = f"{o[:-4]}.decode.wav"
                pre.append(("audio", [src, "-o", dec]))
                src = dec
            if node.type == "TRIM":
                return pre + [("cut", [src, "--start", fmt_seconds(p["start"]), "--end", fmt_seconds(p["end"]), "-o", o])]
            total = total_duration(first.segments)
            rem = [TimeRange(r["start"], r["end"]) for r in (p["remove"] if node.type == "CUT" else st.measurements.get("effective_ranges", []))]
            keep = complement(rem, total) if rem else [TimeRange(0.0, total)]
            if len(keep) == 1:
                return pre + [("cut", [src, "--start", fmt_seconds(keep[0].start), "--end", fmt_seconds(keep[0].end), "-o", o])]
            return pre + [("cut", [src, "--segments", ",".join(f"{fmt_seconds(k.start)}-{fmt_seconds(k.end)}" for k in keep), "-o", o])]
        if node.type == "FADE_IN":
            return [("audio", [src, "--fade-in", fmt_seconds(p["duration"]), "-o", o])]
        if node.type == "FADE_OUT":
            return [("audio", [src, "--fade-out", fmt_seconds(p["duration"]), "-o", o])]
        if node.type == "NORMALIZE":
            args = [src, "-I", fmt_db(p["target_lufs"]), "--tp", fmt_db(p["true_peak_db"])]
            if "loudness_range_lu" in p:
                args += ["--lra", fmt_db(p["loudness_range_lu"])]
            if "sample_rate" in p:
                args += ["--sample-rate", str(int(p["sample_rate"]))]
            return [("loudness", args + ["-o", o])]
        if node.type == "MONO":
            return [("audio", [src, "--mono", "-o", o])]
        if node.type == "STEREO":
            return [("audio", [src, "--stereo", "-o", o])]
        if node.type == "DOWNMIX":
            return [("audio", [src, "--downmix", "-o", o])]
        if node.type == "NOISE_REDUCTION":
            return [("audio", [src, "--denoise", "--denoise-strength", fmt_db(p["strength_db"]), "-o", o])]
        if node.type == "MIX":
            # pairwise fold through ffmpeg-skill/audio --music (main + one bed per call); intermediates next to the output
            live = [(self._artifact_path(states[i], sources), lv["gain_db"]) for i, lv in zip(node.inputs, p["levels"]) if not lv["mute"]]
            calls: List[Tuple[str, List[str]]] = []
            main_path, main_gain = live[0]
            if len(live) == 1:
                return [("audio", [main_path, "--gain", fmt_db(main_gain), "-o", o])]
            for k, (bed, bed_gain) in enumerate(live[1:], start=1):
                target = o if k == len(live) - 1 else f"{o[:-4]}.mix{k}.wav"
                args = [main_path, "--music", bed, "--music-volume", fmt_db(bed_gain)]
                if k == 1 and main_gain:
                    args += ["--gain", fmt_db(main_gain)]
                calls.append(("audio", args + ["-o", target]))
                main_path = target
            return calls
        raise AudioError("INTERNAL_ERROR", f"no argv builder for {node.type}")

    def _artifact_path(self, st: NodeState, sources: Dict[str, Dict[str, Any]]) -> str:
        if st.node.type == "SOURCE_TRACK":
            return sources[st.node.track.source_id]["path"]  # type: ignore[union-attr]
        if st.artifact is None:
            raise AudioError("DEPENDENCY_ERROR", f"input {st.node.node_id!r} has no artifact (status {st.status})", {"node_id": st.node.node_id})
        return str(st.artifact.path)

    def _execute_node(self, states: Dict[str, NodeState], st: NodeState, sources: Dict[str, Dict[str, Any]], work_dir: Path, timeout: Optional[float], reuse: bool) -> None:
        node = st.node
        st.input_hashes = [sources[node.track.source_id]["sha256"]] if node.type == "SOURCE_TRACK" else [states[i].artifact.sha256 if states[i].artifact else sources[states[i].node.track.source_id]["sha256"] for i in node.inputs]  # type: ignore[union-attr]
        if node.type == "SOURCE_TRACK":
            src = sources[node.track.source_id]  # type: ignore[union-attr]
            st.artifact = Artifact(Path(src["path"]), src["duration"], src["channels"], src["sample_rate"], src["codec"], src["size"], src["sha256"], src["channel_layout"])
            st.status = "completed"
            return
        out_path = self._intermediate_path(work_dir, st)
        manifest = out_path.with_suffix(".json")
        if reuse and self._reusable(st, out_path, manifest):
            st.status = "reused"
            return
        for stale in (out_path, manifest):
            if stale.exists():
                stale.unlink()
        try:
            for tool, args in self._argv(st, states, sources, out_path):
                run = self.skill.run_tool(tool, args, timeout)
                st.seconds += run.seconds
                st.tool_commands += run.commands
            st.artifact = self._validate_artifact(out_path, st, expected_channels=self._expected_channels(states, st, sources))
            if node.type == "NORMALIZE":
                self._verify_loudness(st, timeout)
        except AudioError:
            self._remove_partial(out_path)
            raise
        finally:
            for side in self.sidecars(out_path):
                self._remove_partial(side)
        manifest.write_text(json.dumps({"schema": MANIFEST_SCHEMA, "operation_id": st.identity, "type": node.type, "parameters": node.parameters,
                                        "input_hashes": st.input_hashes, "artifact": st.artifact.to_dict(), "measurements": st.measurements,
                                        "tool": f"ffmpeg-skill/{st.tool}", "tool_versions": dict(self.tool_versions),
                                        "skill": SKILL_ID, "skill_version": VERSION}, indent=2, sort_keys=True), encoding="utf-8")
        st.status = "completed"

    def _reusable(self, st: NodeState, out_path: Path, manifest: Path) -> bool:
        if not (out_path.is_file() and manifest.is_file()):
            return False
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if m.get("schema") != MANIFEST_SCHEMA or m.get("operation_id") != st.identity or m.get("input_hashes") != st.input_hashes:
            return False
        art = m.get("artifact") or {}
        if art.get("path") != str(out_path) or out_path.stat().st_size != art.get("size") or sha256_file(str(out_path)) != art.get("sha256"):
            return False
        st.artifact = Artifact(out_path, float(art["duration"]), int(art["channels"]), int(art["sample_rate"]), art.get("codec"), int(art["size"]), art["sha256"], art.get("channel_layout"))
        st.measurements = dict(m.get("measurements") or {})
        st.tool_commands = []
        return True

    @staticmethod
    def _remove_partial(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    # ---- validation
    def _validate_artifact(self, path: Path, st: NodeState, expected_channels: Optional[int] = None, expect: Optional[Dict[str, Any]] = None,
                           fmt: Optional[str] = None) -> Artifact:
        what = f"{st.node.type} {st.node.node_id}"
        if not path.is_file():
            raise AudioError("OUTPUT_ERROR", f"{what}: tool reported success but wrote no file", {"reason": "missing_output", "path": str(path)})
        size = path.stat().st_size
        if size <= 0:
            raise AudioError("OUTPUT_ERROR", f"{what}: output is empty", {"reason": "empty_output", "path": str(path)})
        if not os.access(str(path), os.R_OK):
            raise AudioError("OUTPUT_ERROR", f"{what}: output is not readable", {"reason": "unreadable_output", "path": str(path)})
        try:
            meta = self.skill.probe(str(path))
        except AudioError as e:
            if e.code != "INVALID_INPUT":      # timeout / cancellation / tool failure keep their own code
                raise
            raise AudioError("VALIDATION_ERROR", f"{what}: output is not readable media: {e.message}", {"reason": "corrupt_output", "path": str(path)})
        audio = meta.get("audio") or {}
        if not audio.get("channels"):
            raise AudioError("VALIDATION_ERROR", f"{what}: output has no audio stream", {"reason": "no_audio_stream", "path": str(path)})
        duration = float(meta.get("duration") or 0.0)
        expected = total_duration(st.segments)
        tol = DURATION_TOLERANCE
        if expect and "duration" in expect:
            expected, tol = expect["duration"], expect["duration_tolerance"]
        if expected > 0 and abs(duration - expected) > tol:
            raise AudioError("VALIDATION_ERROR", f"{what}: output duration {duration:.3f}s differs from expected {expected:.3f}s by more than {tol}s",
                             {"reason": "duration_mismatch", "duration": duration, "expected": expected, "tolerance": tol, "path": str(path)})
        channels = int(audio["channels"])
        if expected_channels is not None and channels != expected_channels:
            raise AudioError("VALIDATION_ERROR", f"{what}: output has {channels} channel(s), expected {expected_channels}", {"reason": "channel_mismatch", "path": str(path)})
        if expect and "channels" in expect and channels != expect["channels"]:
            raise AudioError("VALIDATION_ERROR", f"{what}: output has {channels} channel(s), expected {expect['channels']}", {"reason": "channel_mismatch", "path": str(path)})
        if expect and "channel_layout" in expect and channels != CHANNEL_LAYOUTS[expect["channel_layout"]]:
            raise AudioError("VALIDATION_ERROR", f"{what}: output has {channels} channel(s), expected layout {expect['channel_layout']}", {"reason": "channel_mismatch", "path": str(path)})
        sr = int(audio.get("sample_rate") or 0)
        if expect and "sample_rate" in expect and sr != expect["sample_rate"]:
            raise AudioError("VALIDATION_ERROR", f"{what}: output sample rate {sr} differs from expected {expect['sample_rate']}", {"reason": "sample_rate_mismatch", "path": str(path)})
        if st.node.type == "NORMALIZE" and "sample_rate" in st.node.parameters and sr != st.node.parameters["sample_rate"]:
            raise AudioError("VALIDATION_ERROR", f"{what}: output sample rate {sr} differs from requested {st.node.parameters['sample_rate']}", {"reason": "sample_rate_mismatch"})
        codec = audio.get("codec")
        want = OUTPUT_FORMATS[fmt or INTERMEDIATE_FORMAT]["codec"]
        if codec != want:
            raise AudioError("VALIDATION_ERROR", f"{what}: output codec {codec!r} is not {want!r}", {"reason": "codec_mismatch", "path": str(path)})
        return Artifact(path, duration, channels, sr, codec, size, sha256_file(str(path)), audio.get("channel_layout"))

    def _verify_loudness(self, st: NodeState, timeout: Optional[float]) -> None:
        p = st.node.parameters
        assert st.artifact is not None
        m = self.skill.measure_loudness(str(st.artifact.path), p["target_lufs"], p["true_peak_db"], p.get("loudness_range_lu"), timeout)
        measured: Dict[str, Any] = {"silent": bool(m.get("silent"))}
        for key, name in (("input_i", "integrated_lufs"), ("input_tp", "true_peak_dbtp"), ("input_lra", "loudness_range_lu")):
            try:
                measured[name] = float(m[key]) if m.get(key) not in (None, "-inf", "inf") else None
            except (TypeError, ValueError, KeyError):
                measured[name] = None
        st.measurements["loudness"] = {"measured_by": "ffmpeg-skill/loudness --measure-only", "target_lufs": p["target_lufs"], "true_peak_db": p["true_peak_db"],
                                       "tolerance_lufs": p.get("tolerance_lufs"), **measured}
        tol = p.get("tolerance_lufs")
        if tol is not None:
            got = measured.get("integrated_lufs")
            if got is None or abs(got - p["target_lufs"]) > tol:
                raise AudioError("VALIDATION_ERROR", f"NORMALIZE {st.node.node_id}: measured {got} LUFS is not within {tol} LU of {p['target_lufs']}",
                                 {"reason": "loudness_off_target", "measured": got, "target": p["target_lufs"], "tolerance": tol})
            tp = measured.get("true_peak_dbtp")
            if tp is not None and tp > p["true_peak_db"] + 0.5:
                raise AudioError("VALIDATION_ERROR", f"NORMALIZE {st.node.node_id}: true peak {tp} dBTP exceeds ceiling {p['true_peak_db']}",
                                 {"reason": "true_peak_exceeded", "measured": tp, "ceiling": p["true_peak_db"]})

    # ---- outputs
    def _export(self, st: NodeState, out: Any, target: Path, sources: Dict[str, Dict[str, Any]], timeout: Optional[float]) -> Dict[str, Any]:
        src = self._artifact_path(st, sources)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        if existed and not out.overwrite:
            raise AudioError("OUTPUT_ERROR", f"output {out.output_id!r} appeared during execution", {"reason": "exists", "path": str(target)})
        seconds, commands = 0.0, []
        try:
            run: ToolRun = self.skill.run_tool("audio", [src, "-o", str(target)], timeout)
            seconds, commands = run.seconds, run.commands
            export_state = NodeState(Node(f"output:{out.output_id}", "EXPORT", [st.node.node_id]), stable_hash({"export": st.identity, "format": out.format}))
            export_state.segments = st.segments
            art = self._validate_artifact(target, export_state, expected_channels=st.artifact.channels if st.artifact else None, expect=out.expect, fmt=out.format)
        except AudioError:
            self._remove_partial(target)
            raise
        chain = self._provenance_chain(st)
        return {"output_id": out.output_id, "status": "completed", "path": str(target), "format": out.format, "artifact": art.to_dict(),
                "segments": [s.to_dict() for s in st.segments], "seconds": seconds, "tool_commands_observed": commands,
                "provenance": {"skill": SKILL_ID, "skill_version": VERSION, "tool": "ffmpeg-skill/audio",
                               "tool_versions": dict(self.tool_versions),
                               "output_hash": art.sha256, "node_id": st.node.node_id, "operation_id": st.identity, "operations": chain,
                               "sources": {sid: {"path": s["path"], "sha256": s["sha256"], "size": s["size"]} for sid, s in sources.items() if any(seg.source_id == sid for seg in st.segments)}}}

    def _provenance_chain(self, st: NodeState) -> List[Dict[str, Any]]:
        """output -> operation -> ... -> source, as the executor observed it (status, hashes, tool)."""
        chain: List[Dict[str, Any]] = []
        seen: set = set()
        stack = [st]
        while stack:
            s = stack.pop()
            if s.node.node_id in seen:
                continue
            seen.add(s.node.node_id)
            chain.append({"node_id": s.node.node_id, "operation_id": s.identity, "type": s.node.type, "status": s.status, "tool": f"ffmpeg-skill/{s.tool}",
                          "parameters": s.node.parameters, "input_hashes": s.input_hashes, "output_hash": s.artifact.sha256 if s.artifact else None,
                          "measurements": s.measurements})
            stack.extend(self._states_of(s))
        return chain

    def _states_of(self, st: NodeState) -> List[NodeState]:
        return [self._states[i] for i in st.node.inputs]

    @staticmethod
    def _tool_run_dict(r: ToolRun) -> Dict[str, Any]:
        return {"tool": f"ffmpeg-skill/{r.tool}", "exit_code": r.returncode, "seconds": r.seconds, "commands_observed": r.commands}
