"""GP2 assess and replay: create the run directory, collect, decide, and save every artifact."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from cloudfix import __version__
from cloudfix.common.aws import FINDER_READS, Caller, RecordingAws, ReplayAws, evidence_responses
from cloudfix.common.errors import AwsCallError, InputError, MissingEvidence
from cloudfix.common.output import emit, new_run_dir, write_json
from cloudfix.common.settings import load_config, validate_config
from cloudfix.finders.gp2_to_gp3 import SETTINGS, report
from cloudfix.finders.gp2_to_gp3.collect import Collected, collect, parse_time
from cloudfix.finders.gp2_to_gp3.decide import ResourceResult, assess_volume

_EXIT = {"complete": 0, "needs_evidence": 2, "error": 1}
_EVIDENCE_KEYS = {"asOf", "config", "cost_input", "responses"}
_RESPONSE_KEYS = {"service", "operation", "input", "output"}


@dataclass
class _Run:
    """What one run has learned so far, so the artifacts can be written on every outcome."""
    mode: Literal["live", "replay"]
    started_at: datetime
    aws: Caller | None = None
    as_of: datetime | None = None
    config: dict | None = None
    cost_input: Any = None
    replayed: dict | None = None
    collected: Collected | None = None
    pricing_lookups: list[dict] = field(default_factory=list)
    results: list[ResourceResult] = field(default_factory=list)
    status: str = "complete"
    error: dict | None = None
    missing_evidence: dict | None = None


def run_assess(config_path: Path, out: Path, *, aws: Caller | None = None, now: datetime | None = None) -> int:
    run = _Run("live", _utcnow(), aws=aws)
    run.as_of = now if now is not None else run.started_at
    run_dir = _create(out)
    if run_dir is None:
        return 1

    def prepare() -> None:
        run.config = load_config(config_path, SETTINGS)
        run.cost_input = _read_costs(run.config)
        if run.aws is None:
            run.aws = RecordingAws(run.config["region"], FINDER_READS, profile=run.config["profile"])

    _execute(run, prepare)
    return _save(run_dir, run)


def run_replay(evidence_path: Path, out: Path) -> int:
    run = _Run("replay", _utcnow())
    run_dir = _create(out)
    if run_dir is None:
        return 1

    def prepare() -> None:
        run.replayed = evidence = _read_evidence(evidence_path)
        run.as_of = parse_time(evidence["asOf"], "evidence asOf")
        if run.as_of is None:
            raise InputError("evidence asOf is missing")
        # A recorded config carries unset optional settings as null; validate them as absent.
        run.config = validate_config({k: v for k, v in evidence["config"].items() if v is not None}, SETTINGS)
        run.cost_input = evidence["cost_input"]
        run.aws = ReplayAws(evidence["responses"])

    _execute(run, prepare)
    return _save(run_dir, run)


def _execute(run: _Run, prepare: Callable[[], None]) -> None:
    try:
        prepare()
        run.collected, lookups = collect(run.aws, run.config, run.cost_input)
        run.pricing_lookups = lookups.records if lookups is not None else []
        c = run.collected
        run.results = [
            assess_volume(vid, c.volumes[vid], c.instances, *c.costs.get(vid, (None, None)), c.prices,
                          run.config, run.as_of)
            for vid in c.selected_ids
        ]
    except MissingEvidence as e:
        run.status, run.missing_evidence = "needs_evidence", e.request
    except AwsCallError as e:
        run.status = "error"
        run.error = {"code": e.code, "message": str(e), "service": e.service, "operation": e.operation}
    except InputError as e:
        run.status, run.error = "error", {"code": "invalid_input", "message": str(e)}
    except Exception as e:  # noqa: BLE001 - a bug is an outcome too, and still gets its artifacts
        run.status, run.error = "error", _internal(e)


def _save(run_dir: Path, run: _Run) -> int:
    """Build every artifact before writing any, so a failure cannot leave a partial run directory."""
    try:
        files = _artifacts(run_dir, run)
    except Exception as e:  # noqa: BLE001 - fall back to an error set built only from the run's inputs
        run.status, run.error, run.missing_evidence = "error", _internal(e), None
        run.collected, run.results = None, []
        files = _artifacts(run_dir, run)
    for path, content in files.items():
        write_json(path, content)
    line = {"status": run.status, **{f"{n}_file": str(run_dir / f"{n}.json")
                                     for n in ("summary", "result", "evidence", "context")}}
    if run.error is not None:
        line["error"] = run.error
    emit(line)
    return _EXIT[run.status]


def _artifacts(run_dir: Path, run: _Run) -> dict[Path, Any]:
    reads = run.aws.reads if run.aws is not None else []
    names = (["config"] if run.mode == "live" else []) + ["evidence", "context", "result", "summary"]
    paths = {name: run_dir / f"{name}.json" for name in names}
    identity = run.collected.identity if run.collected else _recorded_identity(reads)
    context = {
        "mode": run.mode,
        "started_at": run.started_at,
        "completed_at": _utcnow(),
        "version": __version__,
        "as_of": run.as_of,
        "config": run.config,
        "cost_input": run.cost_input,
        "identity": identity,
        "scope": report.build_scope(run.collected, run.config, identity),
        "reads": reads,
        "pricing_lookups": run.pricing_lookups,
        "status": run.status,
    }
    if run.error is not None:
        context["error"] = run.error
    if run.missing_evidence is not None:
        context["missing_evidence"] = run.missing_evidence
    evidence = run.replayed if run.replayed is not None else {
        "asOf": run.as_of, "config": run.config, "cost_input": run.cost_input,
        "responses": evidence_responses(reads),
    }
    artifacts = {name: str(path) for name, path in paths.items()}
    files = {
        paths["evidence"]: evidence,
        paths["context"]: context,
        paths["result"]: report.build_result(run.collected, run.results),
        paths["summary"]: report.build_summary(run.status, run.mode, context, run.collected, run.results,
                                               artifacts),
    }
    return {paths["config"]: run.config, **files} if "config" in paths else files


def _create(out: Path) -> Path | None:
    """Create the run directory before anything else; on failure print the error line."""
    try:
        return new_run_dir(out)
    except (InputError, OSError) as e:
        emit({"status": "error", "error": {"code": "invalid_input", "message": str(e)}})
        return None


def _read_costs(config: dict) -> Any:
    path = config["costs_file"]
    if config["cost_basis"] != "supplied":
        if path is not None:
            raise InputError("costs_file is set but cost_basis is public; set cost_basis to supplied "
                             "or remove costs_file")
        return None
    if path is None:
        raise InputError("cost_basis supplied requires costs_file")
    return _read_json(Path(path), "costs file")


def _read_evidence(path: Path) -> dict:
    evidence = _read_json(path, "evidence")
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS:
        raise InputError("evidence is not a cloudfix gp2-to-gp3 evidence.json (keys asOf, config, cost_input, "
                         "responses); evidence recorded by gp2.cjs cannot be replayed, run assess again")
    if not isinstance(evidence["config"], dict):
        raise InputError("evidence config must be a JSON object")
    responses = evidence["responses"]
    if not isinstance(responses, list) or any(not isinstance(r, dict) or set(r) != _RESPONSE_KEYS
                                              for r in responses):
        raise InputError("every evidence response needs exactly service, operation, input and output")
    return evidence


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise InputError(f"cannot read {what} {path}: {e.strerror or e}") from e
    except ValueError as e:  # invalid JSON or invalid UTF-8
        raise InputError(f"{what} {path} is not valid JSON: {e}") from e


def _recorded_identity(reads: list[dict]) -> dict | None:
    return next((r["output"] for r in reads
                 if r["operation"] == "GetCallerIdentity" and isinstance(r.get("output"), dict)), None)


def _internal(e: Exception) -> dict:
    return {"code": "internal", "message": f"{type(e).__name__}: {e}"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
