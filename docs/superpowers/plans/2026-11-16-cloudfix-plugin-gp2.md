# CloudFix Plugin (GP2→GP3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 8 MB `gp2.cjs` bundle with a small uv-run Python code base inside one `cloudfix` plugin: a GP2→GP3 finder skill, a fixer skill whose code makes the change, and a generic infrastructure-code PR worker, installable from this repository by both Claude Code and Codex.

**Architecture:** One plugin folder, `plugins/cloudfix/`, holds the skills, two Claude-only agent files, and a Python project (`python/`) exposing one `cloudfix` console script. Every AWS call goes through one recording caller, so the same finder code runs live (recording evidence) and in replay (answering from that evidence). Pure decision code (sizing, checks, savings) is separate from AWS collection and from output shaping; the fixer reuses the finder's savings code for overrides.

**Tech Stack:** Python ≥3.11, boto3, pytest + `botocore.stub.Stubber`, uv (`uv_build` backend), Claude Code and Codex plugin manifests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-16-cloudfix-plugin-gp2-design.md`

## Global Constraints

- Every skill runs the CLI as `uv run --frozen --no-dev --project <plugin>/python cloudfix <args>`. No shell scripts, no `bin/` folder.
- `requires-python = ">=3.11"`. Runtime dependency: `boto3` only. Dev dependency group: `pytest` only. Nothing vendored, bundled or minified.
- Code paths use `pathlib`; no shell-outs. Run directories mode 0700 and files mode 0600 when `os.name == "posix"`.
- Every CLI command prints exactly one JSON line on stdout; errors are `{"status":"error","error":{"code":…,"message":…}}`.
- Finder exit codes: 0 complete (rejections included), 2 needs evidence (replay only), 1 error. Fixer `apply`: 0 completed, 3 in progress, 1 failed or error. `plan`: 0 when a plan with ≥1 volume was written, 1 otherwise.
- Assessment reads only: `sts:GetCallerIdentity`, `ec2:DescribeVolumes`, `ec2:DescribeInstances`, `pricing:GetProducts` (us-east-1). Fixer adds `ec2:DescribeVolumesModifications`, `ec2:DescribeSnapshots`, `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyVolume`, and `ec2:DeleteSnapshot` (cleanup only).
- The fixer writes only `cloudfix-ai:*` tags: `cloudfix-ai:fixer=gp2-to-gp3`, `cloudfix-ai:originalResourceId`, `cloudfix-ai:executionDate` (UTC `YYYY-MM-DD`), `cloudfix-ai:planId`. It never writes `cloudfix:*`. It reads `cloudfix:dontFixIt`.
- Code computes every amount and total; skills tell the agent to report `display` values and never do arithmetic.
- Run artifacts and real account data never enter the repository; test fixtures are synthetic.
- Credentials are never asked for, logged or written; the AWS profile name is the only credential-related value recorded.

## Review Focus

1. **No GP2 volumes in the region.** The user expects a complete run saying nothing to do: exit 0, `no_candidates: true`, `totals.status == "empty"`, no Pricing calls. Pinned in Task 6.
2. **Access denied partway through collection** (e.g. `DescribeInstances` denied). The user expects an error naming the operation and AWS error code, with every artifact saved, never an empty "no candidates" result. Pinned in Task 6.
3. **A multi-volume plan where one volume's modification fails.** The user expects the other volumes to finish and be reported as verified, the failed one reported with AWS's status, and a rerun that does not touch verified volumes. Pinned in Task 9.
4. **Credentials now point at a different account than the plan.** The user expects `apply` to refuse before any write. Pinned in Task 9.
5. **Resuming after the plan has expired.** A run already in progress must continue (refusing would strand a half-done change); a plan that never started must be refused once expired. Pinned in Task 9.

---

## File Structure

```text
.gitignore                                   # add .venv, __pycache__, .pytest_cache
.claude-plugin/marketplace.json              # Task 12
.agents/plugins/marketplace.json             # Task 12
.github/workflows/test.yml                   # Task 12
README.md                                    # Task 12 (rewrite)
skills/co-gp2-to-gp3/                        # Task 12 (delete)
plugins/cloudfix/
  .claude-plugin/plugin.json                 # Task 12
  .codex-plugin/plugin.json                  # Task 12
  agents/iac-pr-worker.md                    # Task 11
  agents/iac-pr-reviewer.md                  # Task 11
  skills/co-gp2-to-gp3/SKILL.md              # Task 7
  skills/co-gp2-to-gp3-fix/SKILL.md          # Task 10
  skills/co-gp2-to-gp3-fix/iac.md            # Task 10
  skills/co-iac-pr/SKILL.md                  # Task 11
  skills/co-iac-pr/review.md                 # Task 11
  python/pyproject.toml, python/uv.lock      # Task 1
  python/cloudfix/__init__.py                # Task 1: __version__ lookup
  python/cloudfix/__main__.py                # Task 1: python -m cloudfix
  python/cloudfix/cli.py                     # Task 1, extended in 6, 8, 9, 10
  python/cloudfix/common/errors.py           # Task 2
  python/cloudfix/common/output.py           # Task 2
  python/cloudfix/common/settings.py         # Task 2
  python/cloudfix/common/money.py            # Task 2
  python/cloudfix/common/aws.py              # Task 3
  python/cloudfix/common/pricing.py          # Task 4
  python/cloudfix/common/iac.py              # Task 8
  python/cloudfix/finders/gp2_to_gp3/__init__.py   # Task 5: NAME, SETTINGS
  python/cloudfix/finders/gp2_to_gp3/decide.py     # Task 5: sizing, checks, savings (pure)
  python/cloudfix/finders/gp2_to_gp3/collect.py    # Task 6: AWS reads -> Collected
  python/cloudfix/finders/gp2_to_gp3/report.py     # Task 6: summary/result/context shapes
  python/cloudfix/finders/gp2_to_gp3/run.py        # Task 6: assess/replay orchestration
  python/cloudfix/fixers/gp2_to_gp3/__init__.py
  python/cloudfix/fixers/gp2_to_gp3/plan.py        # Task 8: plan + change.json
  python/cloudfix/fixers/gp2_to_gp3/apply.py       # Task 9: resumable apply
  python/cloudfix/fixers/gp2_to_gp3/cleanup.py     # Task 10: status + cleanup
  python/tests/factories.py                  # Task 3: synthetic volume/instance/price builders
  python/tests/test_*.py
```

The spec's `finders/gp2_to_gp3.py` and `fixers/gp2_to_gp3.py` become packages of focused modules (module boundaries are the builder's call per the spec). Shared, finder-agnostic code lives in `common/` so the next finder reuses it.

Run tests from the repo root with: `uv run --frozen --directory plugins/cloudfix/python pytest <path> -v` (paths below are relative to `plugins/cloudfix/python`).

---

### Task 1: Python project skeleton and `cloudfix --version`

**Files:**
- Create: `plugins/cloudfix/python/pyproject.toml`, `plugins/cloudfix/python/uv.lock` (generated), `plugins/cloudfix/python/cloudfix/__init__.py`, `plugins/cloudfix/python/cloudfix/__main__.py`, `plugins/cloudfix/python/cloudfix/cli.py`, empty `cloudfix/common/__init__.py`, `cloudfix/finders/__init__.py`, `cloudfix/fixers/__init__.py`
- Modify: `.gitignore` (create if absent)
- Test: `plugins/cloudfix/python/tests/test_cli_version.py`

**Interfaces:**
- Produces: `cloudfix.cli.main(argv: list[str] | None = None) -> int` (prints one JSON line, returns the exit code; console script `cloudfix = "cloudfix.cli:main"` wraps it with `sys.exit`). `cloudfix.cli.build_parser() -> argparse.ArgumentParser` (later tasks add subcommands to it; Task 7 parses skill command lines with it). `cloudfix.__version__: str`.

- [ ] **Step 1: Write `pyproject.toml`** with: `[project] name = "cloudfix"`, `version = "0.1.0"`, `requires-python = ">=3.11"`, `dependencies = ["boto3"]`; `[project.scripts] cloudfix = "cloudfix.cli:main"`; `[dependency-groups] dev = ["pytest"]`; `[build-system] requires = ["uv_build>=0.10,<0.11"]`, `build-backend = "uv_build"`; `[tool.uv.build-backend] module-name = "cloudfix"`, `module-root = ""`; `[tool.pytest.ini_options] testpaths = ["tests"]`. Add `plugins/cloudfix/python/.venv/`, `__pycache__/`, `.pytest_cache/` to `.gitignore`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_cli_version.py
import json
from cloudfix import cli, __version__

def test_version_prints_one_json_line(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"package": "cloudfix", "version": __version__}

def test_no_arguments_prints_usage_and_returns_zero(capsys):
    assert cli.main([]) == 0
    assert "usage: cloudfix" in capsys.readouterr().out

def test_unknown_command_is_a_json_error(capsys):
    assert cli.main(["nope"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
```

- [ ] **Step 3: Lock and run the test to verify it fails**

Run: `uv lock --directory plugins/cloudfix/python && uv run --frozen --directory plugins/cloudfix/python pytest tests/test_cli_version.py -v`
Expected: FAIL (`cloudfix.cli` has no `main`).

- [ ] **Step 4: Implement** `cloudfix/__init__.py` (`__version__ = importlib.metadata.version("cloudfix")`), `cli.build_parser()` and `cli.main()`: argparse with a top-level `--version`; argparse errors and any uncaught exception become the JSON error line with exit 1 (override `ArgumentParser.error` to raise instead of exiting). `__main__.py` calls `sys.exit(main())`.

- [ ] **Step 5: Run the tests and the real command**

Run: `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_cli_version.py -v` → PASS.
Run: `uv run --frozen --no-dev --project plugins/cloudfix/python cloudfix --version` → `{"package": "cloudfix", "version": "0.1.0"}`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore plugins/cloudfix/python
git commit -m "feat(cloudfix): python project skeleton with cloudfix --version"
```

---

### Task 2: Common utilities — errors, output, settings, money

**Files:**
- Create: `cloudfix/common/errors.py`, `cloudfix/common/output.py`, `cloudfix/common/settings.py`, `cloudfix/common/money.py`
- Test: `tests/test_output.py`, `tests/test_settings.py`, `tests/test_money.py`

**Interfaces:**
- Produces:
  - `errors.InputError(Exception)`; `errors.MissingEvidence(Exception)` with `.request: dict` (`{"status":"needs_evidence","service","operation","input"}`); `errors.AwsCallError(Exception)` with `.service, .operation, .code, .message`.
  - `output.new_run_dir(path: str | Path) -> Path` — resolves; raises `InputError` if it exists or its parent does not; creates it (0700 on POSIX).
  - `output.write_json(path: Path, obj: Any) -> None` — `to_jsonable`, indent 2, trailing newline, 0600 on POSIX.
  - `output.to_jsonable(obj: Any) -> Any` — `datetime` → ISO 8601 string, recursively through dicts/lists/tuples; dataclasses via `asdict`.
  - `output.emit(obj: Any) -> None` — one JSON line on stdout.
  - `settings.Setting` frozen dataclass: `name: str, question: str, type: Literal["string","integer","number","string_list","account_id","region","choice","path"], default: Any = None, required: bool = False, choices: tuple[str, ...] = (), minimum: float | None = None`.
  - `settings.describe(settings: Sequence[Setting]) -> list[dict]`; `settings.load_config(path: Path, settings: Sequence[Setting]) -> dict[str, Any]`.
  - `money.AMOUNT_FIELDS: tuple[str, ...]` = `("current_annual_cost","proposed_annual_cost","annual_saving","public_current_annual_cost","public_proposed_annual_cost","public_annual_saving")`.
  - `money.display_usd(value: float | None) -> str | None`; `money.display_amounts(amounts: Mapping[str, float | None]) -> dict[str, str | None]`; `money.summary_totals(resources: Sequence[Mapping], complete: bool) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_money.py
from cloudfix.common.money import display_usd, summary_totals

def test_display_usd():
    assert display_usd(None) is None
    assert display_usd(0) == "$0.00"
    assert display_usd(1234.5) == "$1,234.50"
    assert display_usd(-24) == "-$24.00"
    assert display_usd(0.004) == "Positive, less than $0.01"
    assert display_usd(-0.004) == "Negative, less than $0.01"

def _row(decision, status, saving=1.0):
    amounts = {f: (saving if status == "calculated" else None) for f in
               ("current_annual_cost","proposed_annual_cost","annual_saving",
                "public_current_annual_cost","public_proposed_annual_cost","public_annual_saving")}
    return {"decision": decision, "estimate": {"status": status, **amounts}}

def test_totals_statuses_and_sums():
    assert summary_totals([], True)["status"] == "empty"
    assert summary_totals([_row("rejected", "calculated")], True)["status"] == "no_approved_candidates"
    t = summary_totals([_row("approved", "calculated", 0.1), _row("approved", "calculated", 0.2)], True)
    assert t["status"] == "calculated" and t["annual_saving"] == 0.1 + 0.2  # unrounded sum
    assert t["included_resource_count"] == 2 and t["display"]["annual_saving"] == "$0.30"
    p = summary_totals([_row("approved", "calculated"), _row("not_assessed", "not_calculated")], True)
    assert p["status"] == "partial" and p["unknown_resource_count"] == 1 and p["annual_saving"] == 1.0
    u = summary_totals([_row("approved", "calculated")], False)
    assert u["status"] == "unverified" and u["annual_saving"] is None
```

```python
# tests/test_settings.py — Setting/describe/load_config
# - describe() returns dicts with keys name, question, type, default, required, choices, minimum
# - load_config fills defaults, rejects unknown keys (InputError "unknown setting: x"),
#   rejects a missing required setting, an account_id that is not 12 digits, a region not matching
#   ^[a-z]{2}(-[a-z]+)+-\d$, an integer below minimum, a choice outside choices
```

```python
# tests/test_output.py
# - new_run_dir creates a new directory; raises InputError when it exists; raises InputError when the parent is missing
# - write_json writes indent-2 JSON with trailing newline; datetimes become ISO strings
# - on POSIX (skip on Windows): directory mode 0o700, file mode 0o600
# - emit prints exactly one line
```

Write the settings and output tests as real pytest functions with those assertions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_money.py tests/test_settings.py tests/test_output.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement the four modules.** `summary_totals` rule (same as today's tool): `included` = rows with decision `approved` and estimate status `calculated`; `excluded` = decision `rejected`; `unknown` = the rest. Status: `unverified` if not complete, else `partial` if unknown > 0, else `empty` if no rows, else `no_approved_candidates` if included == 0, else `calculated`. Amounts are summed over included rows only when `complete and (unknown == 0 or included > 0)`, otherwise `None`. Return `{"scope":"approved_calculated_candidates","status","currency":"USD","included_resource_count","excluded_resource_count","unknown_resource_count", **amounts, "display": display_amounts(amounts)}`.

- [ ] **Step 4: Run tests to verify they pass** (same command) → PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/cloudfix/python/cloudfix/common plugins/cloudfix/python/tests
git commit -m "feat(cloudfix): common errors, output, settings and money helpers"
```

---

### Task 3: Recording and replay AWS callers

**Files:**
- Create: `cloudfix/common/aws.py`, `tests/factories.py`
- Test: `tests/test_aws.py`

**Interfaces:**
- Consumes: `errors.InputError`, `errors.MissingEvidence`, `errors.AwsCallError`, `output.to_jsonable`.
- Produces:
  - `aws.Caller` (Protocol): `call(self, service: str, operation: str, params: dict) -> dict`; attribute `reads: list[dict]`.
  - `aws.RecordingAws(region: str, allowed: frozenset[str], *, profile: str | None = None, clients: Mapping[str, Any] | None = None)` — `allowed` holds `"service.Operation"` strings; clients default to a `boto3.Session(profile_name=profile)` client per service (`pricing` always in `us-east-1`, others in `region`). Each call appends `{"service","operation","region","input","observed_at"}` plus `"output"` (JSON-safe) or `"error": {"code","message"}`; a botocore `ClientError` or other exception is re-raised as `AwsCallError`. A non-allowed operation raises `InputError` before any request.
  - `aws.ReplayAws(responses: Sequence[dict])` — `responses` items are `{"service","operation","input","output"}`; `call` returns a deep copy of the single exact match (service, operation, and `input` equal after `to_jsonable`); none → `MissingEvidence`; more than one → `InputError("Ambiguous recorded responses for one request")`. Replayed calls are appended to `reads` the same way.
  - `aws.evidence_responses(reads: Sequence[dict]) -> list[dict]` — successful reads reduced to `{"service","operation","input","output"}`.
  - `aws.FINDER_READS = frozenset({"sts.GetCallerIdentity","ec2.DescribeVolumes","ec2.DescribeInstances","pricing.GetProducts"})`.
  - `tests/factories.py`: `volume(volume_id, size=100, vtype="gp2", create_time="2026-01-01T00:00:00Z", attachments=(), tags=None, state="in-use", iops=None, throughput=None) -> dict`, `attachment(instance_id, attach_time="2026-01-01T00:00:00Z", device="/dev/xvdf", state="attached") -> dict`, `instance(instance_id, launch_time="2026-01-01T00:00:00Z", tags=None, state="running") -> dict`, `price_row(unit: str, usd: str) -> str` (one PriceList JSON string with one OnDemand price dimension), `stubbed_client(service: str, region="us-east-1") -> tuple[client, Stubber]` (dummy credentials).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aws.py
import pytest
from cloudfix.common.aws import RecordingAws, ReplayAws, evidence_responses, FINDER_READS
from cloudfix.common.errors import InputError, MissingEvidence, AwsCallError
from tests.factories import stubbed_client

def test_recording_records_input_output_and_converts_datetimes():
    sts, stub = stubbed_client("sts")
    stub.add_response("get_caller_identity", {"Account": "123456789012", "Arn": "a", "UserId": "u"})
    aws = RecordingAws("eu-west-1", FINDER_READS, clients={"sts": sts})
    assert aws.call("sts", "GetCallerIdentity", {})["Account"] == "123456789012"
    r = aws.reads[0]
    assert (r["service"], r["operation"], r["region"], r["input"]) == ("sts", "GetCallerIdentity", "eu-west-1", {})
    assert "output" in r and "observed_at" in r

def test_recording_turns_client_error_into_aws_call_error_and_records_it():
    ec2, stub = stubbed_client("ec2")
    stub.add_client_error("describe_instances", service_error_code="UnauthorizedOperation", service_message="denied")
    aws = RecordingAws("us-east-1", FINDER_READS, clients={"ec2": ec2})
    with pytest.raises(AwsCallError) as e:
        aws.call("ec2", "DescribeInstances", {"Filters": []})
    assert e.value.code == "UnauthorizedOperation"
    assert aws.reads[0]["error"]["code"] == "UnauthorizedOperation"

def test_disallowed_operation_is_refused_before_any_request():
    aws = RecordingAws("us-east-1", FINDER_READS, clients={})
    with pytest.raises(InputError):
        aws.call("ec2", "ModifyVolume", {"VolumeId": "vol-1"})

def test_replay_exact_match_missing_and_ambiguous():
    row = {"service": "ec2", "operation": "DescribeVolumes", "input": {"Filters": [{"Name": "volume-id", "Values": ["vol-1"]}]}, "output": {"Volumes": []}}
    assert ReplayAws([row]).call("ec2", "DescribeVolumes", row["input"]) == {"Volumes": []}
    with pytest.raises(MissingEvidence) as e:
        ReplayAws([row]).call("ec2", "DescribeVolumes", {"Filters": []})
    assert e.value.request["operation"] == "DescribeVolumes"
    with pytest.raises(InputError):
        ReplayAws([row, row]).call("ec2", "DescribeVolumes", row["input"])

def test_evidence_responses_keeps_only_successful_reads():
    reads = [{"service": "s", "operation": "o", "region": "r", "input": {}, "observed_at": "t", "output": {"a": 1}},
             {"service": "s", "operation": "o", "region": "r", "input": {"x": 1}, "observed_at": "t", "error": {"code": "E", "message": "m"}}]
    assert evidence_responses(reads) == [{"service": "s", "operation": "o", "input": {}, "output": {"a": 1}}]
```

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_aws.py -v` → FAIL.
- [ ] **Step 3: Implement `aws.py` and `tests/factories.py`.** Map API names to boto3 methods with `botocore.xform_name`. Add `tests/__init__.py` (empty) so `tests.factories` imports.
- [ ] **Step 4: Run to verify pass** (same command) → PASS.
- [ ] **Step 5: Commit** — `git add` the two modules and tests; `git commit -m "feat(cloudfix): recording and replay AWS callers"`.

---

### Task 4: EBS and snapshot pricing

**Files:**
- Create: `cloudfix/common/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Consumes: `aws.Caller`.
- Produces:
  - `pricing.EbsPrices` frozen dataclass: `storage_gb_month: float, iops_month: float, throughput_mibps_month: float, free_iops: int, free_throughput_mibps: int`.
  - `pricing.PriceBreakdown` frozen dataclass: `storage_cost, iops_units, iops_cost, throughput_units, throughput_cost, annual` (floats; costs annual).
  - `pricing.annual_price(prices: EbsPrices, size_gib: float, iops: float, throughput: float) -> PriceBreakdown` — `storage_cost = 12·size·storage`; `iops_units = max(0, iops − free_iops)` if `iops_month > 0` else 0; `iops_cost = 12·iops_units·iops_month`; throughput likewise; `annual` = sum.
  - `pricing.PricingLookups(aws: Caller, region: str)` with `ebs(volume_type: Literal["gp2","gp3"]) -> EbsPrices` (cached per type), `snapshot_gb_month() -> float` (cached), and `records: list[dict]` — each `{"query": <GetProducts input>, "region", "unit", "raw_price": float | None, "value_used": float, "fallback_used": bool}`.
  - `pricing.DEFAULTS`: gp2 `storage 0.10`, iops 0 / throughput 0, free 0/0; gp3 `storage 0.08`, `iops 0.005`, `throughput 0.04` (per MiB/s-month), free `3000` IOPS / `125` MiB/s. `pricing.SNAPSHOT_DEFAULT_GB_MONTH = 0.05`.

Queries (all `ServiceCode="AmazonEC2"`, `FormatVersion="aws_v1"`, `MaxResults=100`, filters `Type="TERM_MATCH"` in exactly this order, so replay inputs are deterministic):

| Price | Filters (Field=Value) | Unit | Conversion |
| --- | --- | --- | --- |
| storage gp2/gp3 | `volumeType=General Purpose`, `volumeApiName=<type>`, `regionCode=<region>` | `GB-Mo` | as is |
| gp3 IOPS | `volumeApiName=gp3`, `group=EBS IOPS`, `regionCode=<region>` | `IOPS-Mo` | as is |
| gp3 throughput | `volumeApiName=gp3`, `group=EBS Throughput`, `regionCode=<region>` | `GiBps-mo` | ÷ 1024 → per MiB/s |
| snapshot | `productFamily=Storage Snapshot`, `regionCode=<region>` | `GB-Mo` | as is |

Price extraction: first `PriceList` row whose JSON text contains the unit string; in its `terms.OnDemand`, the first price dimension whose `unit` equals the unit exactly; `float(pricePerUnit["USD"])`. No row, no match, or a `0`/missing price → default with `fallback_used: true`. gp2 makes only the storage lookup (no IOPS or throughput cost).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pricing.py
import pytest
from cloudfix.common.pricing import EbsPrices, annual_price, PricingLookups, DEFAULTS
from cloudfix.common.aws import ReplayAws

GP2 = EbsPrices(0.10, 0.0, 0.0, 0, 0)
GP3 = EbsPrices(0.08, 0.005, 0.04, 3000, 125)

def test_annual_price_gp2_and_gp3():
    assert annual_price(GP2, 1000, 3000, 250).annual == pytest.approx(1200.0)
    b = annual_price(GP3, 1000, 3000, 250)
    assert (b.iops_units, b.throughput_units) == (0, 125)
    assert b.annual == pytest.approx(1020.0)
    assert annual_price(GP3, 100, 4000, 125).iops_cost == pytest.approx(60.0)

from tests.factories import price_row
R = "eu-west-1"

def _q(*filters):
    return {"ServiceCode": "AmazonEC2", "FormatVersion": "aws_v1", "MaxResults": 100,
            "Filters": [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters]}

def _resp(query, rows):
    return {"service": "pricing", "operation": "GetProducts", "input": query, "output": {"PriceList": rows}}

STORAGE_GP2 = _q(("volumeType", "General Purpose"), ("volumeApiName", "gp2"), ("regionCode", R))
STORAGE_GP3 = _q(("volumeType", "General Purpose"), ("volumeApiName", "gp3"), ("regionCode", R))
IOPS_GP3 = _q(("volumeApiName", "gp3"), ("group", "EBS IOPS"), ("regionCode", R))
THR_GP3 = _q(("volumeApiName", "gp3"), ("group", "EBS Throughput"), ("regionCode", R))
SNAPSHOT = _q(("productFamily", "Storage Snapshot"), ("regionCode", R))

def test_lookups_parse_prices_and_record_them():
    aws = ReplayAws([_resp(STORAGE_GP3, [price_row("GB-Mo", "0.088")]),
                     _resp(IOPS_GP3, [price_row("IOPS-Mo", "0.0055")]),
                     _resp(THR_GP3, [price_row("GiBps-mo", "45.056")])])
    lookups = PricingLookups(aws, R)
    p = lookups.ebs("gp3")
    assert (p.storage_gb_month, p.iops_month, p.free_iops, p.free_throughput_mibps) == (0.088, 0.0055, 3000, 125)
    assert p.throughput_mibps_month == pytest.approx(0.044)
    assert [r["fallback_used"] for r in lookups.records] == [False, False, False]
    assert lookups.records[2]["raw_price"] == 45.056 and lookups.records[2]["unit"] == "GiBps-mo"
    lookups.ebs("gp3")
    assert len(aws.reads) == 3  # cached

def test_missing_price_falls_back_and_is_flagged():
    aws = ReplayAws([_resp(STORAGE_GP3, [price_row("GB-Mo", "0.08")]), _resp(IOPS_GP3, []),
                     _resp(THR_GP3, [price_row("GiBps-mo", "40.96")]), _resp(SNAPSHOT, [])])
    lookups = PricingLookups(aws, R)
    assert lookups.ebs("gp3").iops_month == DEFAULTS["gp3"].iops_month == 0.005
    assert lookups.snapshot_gb_month() == 0.05
    assert [r["fallback_used"] for r in lookups.records] == [False, True, False, True]

def test_gp2_makes_only_the_storage_lookup():
    aws = ReplayAws([_resp(STORAGE_GP2, [price_row("GB-Mo", "0.11")])])
    p = PricingLookups(aws, R).ebs("gp2")
    assert (p.storage_gb_month, p.iops_month, p.throughput_mibps_month) == (0.11, 0.0, 0.0) and len(aws.reads) == 1
```

`DEFAULTS` maps `"gp2"`/`"gp3"` to `EbsPrices` instances.

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_pricing.py -v` → FAIL.
- [ ] **Step 3: Implement `pricing.py`.**
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): EBS and snapshot price lookups with recorded fallbacks"`.

---

### Task 5: GP2 decisions — settings, sizing, checks, savings

**Files:**
- Create: `cloudfix/finders/gp2_to_gp3/__init__.py`, `cloudfix/finders/gp2_to_gp3/decide.py`
- Test: `tests/test_gp2_decide.py`

**Interfaces:**
- Consumes: `settings.Setting`, `pricing.EbsPrices`, `pricing.annual_price`.
- Produces:
  - `gp2_to_gp3.NAME = "gp2-to-gp3"`; `gp2_to_gp3.SETTINGS: tuple[Setting, ...]` with names and defaults: `account` (account_id, optional), `region` (region, required), `profile` (string, optional), `volume_ids` (string_list, default `[]`), `cost_basis` (choice `public|supplied`, default `public`), `costs_file` (path, optional), `volume_age_threshold_days` (integer, default 7, minimum 0), `minimal_savings_usd` (number, default 0). Questions are short plain sentences (e.g. "Which AWS region should be assessed?").
  - `decide.Sizing` frozen dataclass: `size_gib, gp2_iops, gp3_iops, provisioned_iops, gp2_throughput, gp3_throughput, provisioned_throughput` (ints).
  - `decide.size_gp3(size_gib: int) -> Sizing` — spec formulas: `gp2_iops = min(16000, max(100, 3·S))`, `gp3_iops = min(80000, max(gp2_iops, 3000))`, `provisioned_iops = max(0, gp2_iops − 3000)`, `gp2_throughput = ceil(min(250, gp2_iops/4))`, `gp3_throughput = min(2000, max(gp2_throughput, 125))`, `provisioned_throughput = max(0, gp3_throughput − 125)`.
  - `decide.Estimate` frozen dataclass with the six `money.AMOUNT_FIELDS`; `as_dict() -> dict`.
  - `decide.estimate_savings(annual_cost: float, size_gib: int, gp2_iops: int, target_iops: int, target_throughput: int, prices: Mapping[str, EbsPrices]) -> tuple[Estimate | None, dict]` — `gp2 = annual_price(prices["gp2"], S, gp2_iops, 0)`, `gp3 = annual_price(prices["gp3"], S, target_iops, target_throughput)`; `None` when `gp2.annual <= 0`; else `proposed = annual_cost × gp3.annual / gp2.annual`, `saving = annual_cost − proposed`, public figures `gp2.annual`, `gp3.annual`, their difference. The dict is the public breakdown `{"gp2": asdict(gp2), "gp3": asdict(gp3)}`.
  - `decide.Check` dataclass: `name: str, passed: bool, details: dict`.
  - `decide.ResourceResult` dataclass: `resource_id: str, checks: list[Check], fail_fast: bool, approved: bool, sizing: Sizing | None, estimate: Estimate | None, public_breakdown: dict | None, annual_cost: float | None, public_annual_cost: float | None`.
  - `decide.assess_volume(resource_id: str, volume: dict | None, instances: Mapping[str, dict], annual_cost: float | None, public_annual_cost: float | None, prices: Mapping[str, EbsPrices] | None, config: Mapping[str, Any], now: datetime) -> ResourceResult`.
  - Check names, in order: `ResourceExists`, `DontFixItTag`, `ResourceFixed`, `VolumeExpectedType` (fail fast: evaluation stops at the first failure among these four), then `VolumeAgeXDays`, `EMRClusterAssociation` or `Ec2InstanceDescribable`, `ElasticVolumeEnabled`, `MinimalSavings`, `MaximumSavings` (all evaluated). `decide.ELASTIC_SUPPORT_DATE = datetime(2016, 12, 3, 23, 40, tzinfo=UTC)`.

Check rules (from the spec): `DontFixItTag` fails when a tag `cloudfix:dontFixIt` has a non-empty value. Age = whole days `(now − CreateTime) // 1 day`; missing `CreateTime` fails at every threshold with `details.reason = "create-time-unreported"`. `EMRClusterAssociation` uses the first attachment's instance; no attachment passes; instance missing from `instances` → record `Ec2InstanceDescribable` failed instead. `ElasticVolumeEnabled`: created after the date → pass; else first attachment's `AttachTime` after the date → pass; else unattached → pass; else instance `LaunchTime` after the date → pass; else fail (missing times count as "not after"). Sizing and the estimate are computed only after the four fail-fast checks pass. `MinimalSavings` passes iff `saving > minimal_savings_usd`; `MaximumSavings` iff `saving <= annual_cost`; with no estimate both fail with `details.reason = "savings_not_finite"`. `approved` = every recorded check passed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gp2_decide.py
from datetime import datetime, timezone
import pytest
from cloudfix.common.pricing import EbsPrices
from cloudfix.finders.gp2_to_gp3.decide import size_gp3, estimate_savings, assess_volume
from tests.factories import volume, attachment, instance

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
PRICES = {"gp2": EbsPrices(0.10, 0, 0, 0, 0), "gp3": EbsPrices(0.08, 0.005, 0.04, 3000, 125)}
CFG = {"volume_age_threshold_days": 7, "minimal_savings_usd": 0}

@pytest.mark.parametrize("size,gp2_iops,gp3_iops,gp2_thr,gp3_thr", [
    (1, 100, 3000, 25, 125), (33, 100, 3000, 25, 125), (34, 102, 3000, 26, 125),
    (170, 510, 3000, 128, 128), (334, 1002, 3000, 250, 250), (1000, 3000, 3000, 250, 250),
    (5334, 16000, 16000, 250, 250), (16384, 16000, 16000, 250, 250)])
def test_size_gp3(size, gp2_iops, gp3_iops, gp2_thr, gp3_thr):
    s = size_gp3(size)
    assert (s.gp2_iops, s.gp3_iops, s.gp2_throughput, s.gp3_throughput) == (gp2_iops, gp3_iops, gp2_thr, gp3_thr)

def test_estimate_public_and_supplied():
    est, _ = estimate_savings(1200.0, 1000, 3000, 3000, 250, PRICES)
    assert (est.current_annual_cost, est.proposed_annual_cost, est.annual_saving) == pytest.approx((1200, 1020, 180))
    est, _ = estimate_savings(600.0, 1000, 3000, 3000, 250, PRICES)
    assert (est.proposed_annual_cost, est.annual_saving) == pytest.approx((510, 90))
    assert est.public_annual_saving == pytest.approx(180)

def test_zero_gp2_price_means_no_estimate():
    est, _ = estimate_savings(100.0, 100, 300, 3000, 125, {**PRICES, "gp2": EbsPrices(0, 0, 0, 0, 0)})
    assert est is None

def test_approved_volume_has_all_checks_and_settings():
    r = assess_volume("vol-1", volume("vol-1", size=100), {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert r.approved and [c.name for c in r.checks] == ["ResourceExists", "DontFixItTag", "ResourceFixed",
        "VolumeExpectedType", "VolumeAgeXDays", "EMRClusterAssociation", "ElasticVolumeEnabled",
        "MinimalSavings", "MaximumSavings"]
    assert (r.sizing.gp3_iops, r.sizing.gp3_throughput) == (3000, 125)

def test_fail_fast_stops_at_first_failure():
    r = assess_volume("vol-1", volume("vol-1", tags={"cloudfix:dontFixIt": "yes"}), {}, 1.0, 1.0, PRICES, CFG, NOW)
    assert r.fail_fast and [c.name for c in r.checks] == ["ResourceExists", "DontFixItTag"] and r.sizing is None
    assert assess_volume("vol-x", None, {}, None, None, PRICES, CFG, NOW).checks[0].passed is False
    r = assess_volume("vol-1", volume("vol-1", vtype="gp3"), {}, 1.0, 1.0, PRICES, CFG, NOW)
    assert [c.name for c in r.checks][-1] == "ResourceFixed"

def test_age_boundary_and_missing_create_time():
    young = assess_volume("v", volume("v", create_time="2026-11-09T00:00:01Z"), {}, 120.0, 120.0, PRICES, CFG, NOW)
    old = assess_volume("v", volume("v", create_time="2026-11-09T00:00:00Z"), {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert _check(young, "VolumeAgeXDays").passed is False and _check(old, "VolumeAgeXDays").passed is True
    v = volume("v"); del v["CreateTime"]
    c = _check(assess_volume("v", v, {}, 120.0, 120.0, PRICES, {**CFG, "volume_age_threshold_days": 0}, NOW), "VolumeAgeXDays")
    assert c.passed is False and c.details["reason"] == "create-time-unreported"

def test_emr_and_undescribable_instance():
    v = volume("v", attachments=[attachment("i-1")])
    emr = instance("i-1", tags={"aws:elasticmapreduce:instance-group-role": "CORE"})
    assert _check(assess_volume("v", v, {"i-1": emr}, 120.0, 120.0, PRICES, CFG, NOW), "EMRClusterAssociation").passed is False
    r = assess_volume("v", v, {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert _check(r, "Ec2InstanceDescribable").passed is False and not r.approved

def test_elastic_support_dates():
    v = volume("v", create_time="2016-01-01T00:00:00Z", attachments=[attachment("i-1", attach_time="2016-06-01T00:00:00Z")])
    assert _check(assess_volume("v", v, {"i-1": instance("i-1", launch_time="2015-01-01T00:00:00Z")}, 120.0, 120.0, PRICES, CFG, NOW), "ElasticVolumeEnabled").passed is False
    assert _check(assess_volume("v", v, {"i-1": instance("i-1", launch_time="2017-01-01T00:00:00Z")}, 120.0, 120.0, PRICES, CFG, NOW), "ElasticVolumeEnabled").passed is True

def test_minimal_savings_is_strict():
    r = assess_volume("v", volume("v", size=100), {}, 120.0, 120.0, PRICES, {**CFG, "minimal_savings_usd": 24}, NOW)
    assert _check(r, "MinimalSavings").passed is False  # saving == 24, not > 24

def _check(result, name):
    return next(c for c in result.checks if c.name == name)
```

`factories.volume` parses its ISO strings into timezone-aware `datetime` objects, as boto3 returns them.

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_gp2_decide.py -v` → FAIL.
- [ ] **Step 3: Implement `__init__.py` and `decide.py`.**
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): GP2 sizing, checks and savings"`.

---

### Task 6: GP2 collection, artifacts and the `describe` / `assess` / `replay` commands

**Files:**
- Create: `cloudfix/finders/gp2_to_gp3/collect.py`, `report.py`, `run.py`
- Modify: `cloudfix/cli.py` (add `gp2-to-gp3` with `describe`, `assess --config FILE --out DIR`, `replay --evidence FILE --out DIR`)
- Test: `tests/test_gp2_run.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `collect.Collected` dataclass: `account: str, identity: dict, region: str, selected_ids: list[str], discovery: Literal["selected_ids","gp2_inventory"], volumes: dict[str, dict | None], instances: dict[str, dict], prices: dict[str, EbsPrices] | None, costs: dict[str, tuple[float, float]]`.
  - `collect.collect(aws: Caller, config: Mapping, cost_input: dict | None) -> tuple[Collected, PricingLookups | None]` — reads in this order: `GetCallerIdentity` (stop with `InputError` if `config["account"]` is set and differs); `DescribeVolumes` with `{"Filters":[{"Name":"volume-id","Values":<≤200 ids>}]}` per batch, or `{"Filters":[{"Name":"volume-type","Values":["gp2"]}]}` for discovery, following `NextToken` (repeated token → `InputError`); `DescribeInstances` with `{"Filters":[{"Name":"instance-id","Values":<≤200 sorted distinct first-attachment ids>}]}` following `NextToken`, only if any volume is attached; Pricing (`ebs("gp2")`, `ebs("gp3")`) only if at least one volume was returned. Costs: `public` → both values `annual_price(prices["gp2"], Size, 0, 0).annual`; `supplied` → from `cost_input`, which must have `currency == "USD"`, non-empty `source` and `period`, unique `resourceId`s, finite non-negative `annualCost` and `publicAnnualCost`, and an entry for every selected volume (`InputError` otherwise).
  - `report.build_result(collected, results) -> dict` — `{"finder":"gp2-to-gp3","account","region","prices": {type: asdict(EbsPrices)} | None,"resources":[{"resource_id","checks":[{"name","passed","details"}],"fail_fast","approved","sizing","estimate","public_breakdown","annual_cost","public_annual_cost"}]}`.
  - `report.build_summary(status: str, mode: Literal["live","replay"], context: dict, collected: Collected | None, results: list[ResourceResult], artifacts: dict[str, str]) -> dict` — the spec's summary shape: `status, mode, assessed_at, scope{account,region,selected_volume_ids,discovery}, cost_basis{kind ("public_price_estimate"|"caller_supplied_costs"),currency,source,period,lookup_count,fallback_count,fallback_used,lookup_evidence{"artifact":"context","json_pointer":"/pricing_lookups"}}, configuration, no_candidates, totals (money.summary_totals), resources[], limitations, execution_outcome "not_executed", observed_saving null, artifacts`, plus `missing_evidence` (needs_evidence) or `error` and `failed_reads` (error). Each resource: `resource_id, account, region, evidence_context{status,volume_state,attachments[{instance_id,attachment_state,instance_state}]}, tags, decision ("approved"|"rejected"; "unverified" when status ≠ complete; "not_assessed" for selected ids without a result), checks[{name,status "passed"|"failed",details}], proposed_settings{volume_type "gp3",size_gib,iops,throughput_mib_per_second} (approved only), estimate{status "calculated"|"not_calculated"|"unverified",currency "USD", six amounts, display}, result_pointer "/resources/<i>"`. `limitations`: today's list from the wrapper's `customerSummary` minus the "Native report text…" line.
  - `run.run_assess(config_path: Path, out: Path, *, aws: Caller | None = None, now: datetime | None = None) -> int` and `run.run_replay(evidence_path: Path, out: Path) -> int`. Both create the run dir first (`new_run_dir`), always write `config.json` (assess), `evidence.json` `{"asOf","config","cost_input","responses"}`, `context.json` `{"mode","started_at","completed_at","version","config","cost_input","identity","scope","reads","pricing_lookups","status","error"?}`, `result.json`, `summary.json`, then `emit({"status","summary_file","result_file","evidence_file","context_file"})`. Replay builds `ReplayAws(evidence["responses"])`, uses `now = asOf`, and re-runs `collect` + `assess_volume`; `MissingEvidence` → status `needs_evidence`, exit 2. Replay accepts only this tool's `evidence.json`; evidence recorded by the old `gp2.cjs` has a different shape and is rejected with `InputError`. Any `AwsCallError`/`InputError` → status `error`, exit 1, artifacts still written.

- [ ] **Step 1: Write the failing tests** (live runs use `RecordingAws` with stubbed clients from `factories`; replay runs use the live run's own `evidence.json`)

```python
# tests/test_gp2_run.py  — names and assertions
def test_describe_lists_settings(capsys):
    # cli.main(["gp2-to-gp3","describe"]) == 0; output settings names == [s.name for s in SETTINGS]

def test_live_assess_writes_all_artifacts_and_approves(tmp_path):
    # one 100 GiB gp2 volume, unattached; prices gp2 0.10 / gp3 0.08,0.005,40.96 GiBps
    # exit 0; summary.resources[0].decision == "approved"
    # proposed_settings == {"volume_type":"gp3","size_gib":100,"iops":3000,"throughput_mib_per_second":125}
    # estimate.annual_saving == approx(24.0); estimate.display.annual_saving == "$24.00"
    # totals.status == "calculated"; cost_basis.lookup_count == 4 and fallback_count == 0
    # all of summary/result/evidence/context/config .json exist

def test_replay_of_live_evidence_reproduces_the_summary(tmp_path):
    # run_replay(<live evidence.json>, new dir) == 0; resources/decisions/estimates equal the live summary's

def test_replay_missing_response_exits_2(tmp_path):
    # drop the DescribeInstances response from evidence -> exit 2, summary.status == "needs_evidence",
    # summary.missing_evidence.operation == "DescribeInstances"

def test_account_mismatch_stops_before_volume_reads(tmp_path):
    # config account "111111111111", STS returns "123456789012" -> exit 1, error message names the mismatch,
    # context.reads has only GetCallerIdentity

def test_supplied_costs_missing_a_selected_volume_is_an_error(tmp_path):
    # cost_basis "supplied", costs file lists vol-1 only, volume_ids [vol-1, vol-2]
    # -> exit 1, summary.error.message contains "vol-2", status "error"

def test_output_dir_that_exists_is_refused_before_any_aws_call(tmp_path):
    # out dir pre-created -> exit 1, stdout error line; the stubbed clients received no calls
    # (Stubber.assert_no_pending_responses with nothing queued, and aws.reads == [])

# Review Focus 1
def test_no_gp2_volumes_is_a_complete_empty_run(tmp_path):
    # discovery returns no volumes -> exit 0, no_candidates True, totals.status "empty",
    # no pricing GetProducts in context.reads

# Review Focus 2
def test_access_denied_mid_collection_saves_artifacts_and_names_the_operation(tmp_path):
    # DescribeInstances -> UnauthorizedOperation: exit 1, summary.status "error",
    # summary.failed_reads[0].operation == "DescribeInstances", error.code == "UnauthorizedOperation",
    # summary.no_candidates is False; evidence/context files exist
```

Write each as a real test with these assertions.

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_gp2_run.py -v` → FAIL.
- [ ] **Step 3: Implement `collect.py`, `report.py`, `run.py`, and the CLI wiring.** The CLI creates `RecordingAws(config["region"], FINDER_READS, profile=config["profile"])` for live runs; `--costs` content is read from `config["costs_file"]` when `cost_basis == "supplied"` (required then, `InputError` otherwise).
- [ ] **Step 4: Run the full suite** — `uv run --frozen --directory plugins/cloudfix/python pytest -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): GP2 assess and replay commands with saved artifacts"`.

---

### Task 7: Finder skill `co-gp2-to-gp3`

**Files:**
- Create: `plugins/cloudfix/skills/co-gp2-to-gp3/SKILL.md`
- Test: `tests/test_skill_commands.py`

**Interfaces:**
- Consumes: `cli.build_parser()`; the CLI surface from Task 6.
- Produces: `tests/test_skill_commands.py::skill_commands(path: Path) -> list[list[str]]` — extracts every line in a SKILL.md code block that starts with `cloudfix ` and splits it (placeholders like `<dir>` are replaced by `X`); later tasks add their skills to the parametrized list.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_commands.py
from pathlib import Path
import shlex, pytest
from cloudfix.cli import build_parser

PLUGIN = Path(__file__).resolve().parents[2]
SKILLS = ["co-gp2-to-gp3"]

def skill_commands(path):
    ...  # lines starting with "cloudfix " inside ``` blocks; replace <...> with X; shlex.split; drop "cloudfix"

@pytest.mark.parametrize("skill", SKILLS)
def test_every_documented_command_parses(skill):
    cmds = skill_commands(PLUGIN / "skills" / skill / "SKILL.md")
    assert cmds, "skill documents no cloudfix commands"
    for argv in cmds:
        build_parser().parse_args(argv)  # raises on unknown subcommands/flags

def test_skill_states_the_uv_command_form():
    text = (PLUGIN / "skills/co-gp2-to-gp3/SKILL.md").read_text()
    assert "uv run --frozen --no-dev --project" in text
```

- [ ] **Step 2: Run to verify failure** → FAIL (no SKILL.md).
- [ ] **Step 3: Write `SKILL.md`.** Frontmatter `name: co-gp2-to-gp3`, `description:` (when to use: assess EBS GP2 volumes for GP3 conversion, or replay recorded evidence; hands over to `co-gp2-to-gp3-fix` for changes). Sections:
  1. **Command form** — the uv command, `<plugin>` = two levels up from this skill's directory; in the rest of the skill `cloudfix …` means that command. If `uv --version` fails, stop and give the per-OS install options from the spec; install only if the user asks. If uv reports the plugin folder is not writable, rerun with `UV_PROJECT_ENVIRONMENT` set to a per-user cache directory (`~/.cache/cloudfix-ai/env` on macOS/Linux, `%LOCALAPPDATA%\cloudfix-ai\env` on Windows), using the shell's own syntax for setting a variable.
  2. **Start** — run `cloudfix --version`; report any error line verbatim.
  3. **Interview** — run `cloudfix gp2-to-gp3 describe`; ask only for settings the request did not answer, one at a time, offering the default; never apply a default silently; write `config.json` in a new run directory's parent; never ask for credentials.
  4. **Run** — `cloudfix gp2-to-gp3 assess --config <config.json> --out <new-dir>`; `cloudfix gp2-to-gp3 replay --evidence <evidence.json> --out <new-dir>`; exit codes 0/2/1; read artifacts on every outcome.
  5. **Read and explain** — carry over sections 3 and 4 of the current `skills/co-gp2-to-gp3/SKILL.md` (complete summary, `display` values, code totals, pricing fallbacks via `cost_basis.lookup_evidence`, evidence_context, limitations, separation of assessment / proposed change / execution / savings), replacing every "native report/result" reference with `result.json` as the Python check ledger. Report opted-out volumes (`DontFixItTag` failed) as excluded by the owner.
  6. **Next step** — assessment-only requests end here; for a change, hand over to `co-gp2-to-gp3-fix` with the run directory.
- [ ] **Step 4: Run to verify pass** — `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_skill_commands.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): co-gp2-to-gp3 finder skill"`.

---

### Task 8: IaC signals, fixer `plan`, and `change.json`

**Files:**
- Create: `cloudfix/common/iac.py`, `cloudfix/fixers/gp2_to_gp3/__init__.py`, `cloudfix/fixers/gp2_to_gp3/plan.py`
- Modify: `cloudfix/cli.py` (add `gp2-to-gp3-fix plan` and `gp2-to-gp3-fix change`)
- Test: `tests/test_iac.py`, `tests/test_gp2_plan.py`

**Interfaces:**
- Consumes: `aws.RecordingAws`, `pricing.PricingLookups`, `decide.estimate_savings`, `pricing.EbsPrices`, `output.*`, `money.display_amounts`; the assessment's `result.json`, `config.json`, `context.json`.
- Produces:
  - `iac.iac_signals(tags: Mapping[str, str]) -> list[str]` — one human-readable string per matching tag: key `aws:cloudformation:stack-name`; any key starting `pulumi:`; key (case-insensitive) in `managedby, managed-by, managed_by` whose value contains (case-insensitive) one of `terraform, opentofu, pulumi, cloudformation, cdk, crossplane, kubernetes, ansible`; key `terraform` (case-insensitive); keys `kubernetes.io/created-for/pvc/name`, `ebs.csi.aws.com/cluster`.
  - `plan.FIXER_OPS = frozenset({"sts.GetCallerIdentity","ec2.DescribeVolumes","ec2.DescribeVolumesModifications","ec2.DescribeSnapshots","ec2.CreateSnapshot","ec2.CreateTags","ec2.ModifyVolume","ec2.DeleteSnapshot","pricing.GetProducts"})`.
  - `plan.validate_gp3_target(size_gib: int, iops: int, throughput: int) -> None` — `InputError` unless `3000 <= iops <= 80000`, `iops <= max(3000, 500·size)`, `125 <= throughput <= 2000`, `throughput <= max(125, iops × 0.25)`.
  - `plan.load_assessment(assessment_dir: Path) -> dict` — reads `result.json`, `config.json`, `context.json`; `InputError` unless context status is `complete`.
  - `plan.make_plan(assessment: dict, aws: Caller, *, volume_ids: list[str] | None, management: Literal["not-managed","unknown"], iops_overrides: dict[str, int], throughput_overrides: dict[str, int], now: datetime) -> dict`.
  - `plan.write_plan(out_dir: Path, plan: dict) -> tuple[Path, str]` — writes `plan.json` (via `new_run_dir` + `write_json`) and returns its path and the SHA-256 hex digest of the file bytes.
  - `plan.make_change(assessment: dict, *, volume_ids: list[str] | None, iops_overrides: dict[str, int], throughput_overrides: dict[str, int], repo_hint: str | None, delivery: Literal["draft_pr","local_patch"]) -> dict` — the spec's `change.json` (`finder, account, region, resources[{id, current, target, deviates_from_assessment, iac_evidence}], assessment, change_note, repo_hint, delivery`); `change_note` = absolute path of `plugins/cloudfix/skills/co-gp2-to-gp3-fix/iac.md`, resolved as `Path(cloudfix.__file__).resolve().parents[2] / "skills/co-gp2-to-gp3-fix/iac.md"`.
  - Plan JSON: `{"plan_id": uuid4 hex, "created_at", "expires_at" (created + 24 h), "assessment": abs path, "account", "region", "profile", "volumes": [{"volume_id","current":{type,size_gib,iops,throughput,state},"target":{type "gp3",size_gib,iops,throughput},"assessed_target":{iops,throughput},"deviates_from_assessment","attachment":{instance_id,device,state}|null,"estimate":{six amounts + display},"management_basis":"not_managed_by_code"|"unknown","snapshot_cost_upper_bound_monthly","snapshot_cost_display","steps":[str]}], "refused": [{"volume_id","reason"}], "pricing_lookups": [...], "reads": [...]}`.

`make_plan` rules: only volumes the assessment approved (others → `refused` "not approved in the assessment"); re-read each volume (`DescribeVolumes` by id filter) and its history (`DescribeVolumesModifications` `{"VolumeIds":[id]}`); refuse with a reason when: a `cloudfix:dontFixIt` tag is present, `iac_signals(tags)` is non-empty (reason lists the signals and says the PR worker owns this volume), type is not `gp2`, size differs from the assessment, state not `in-use`/`available`, latest modification state is `modifying` or `optimizing`, or latest modification `StartTime` is less than 6 hours before `now`. Targets: assessment sizing unless overridden; overrides validated with `validate_gp3_target`, estimate recomputed with `estimate_savings` using the assessment's recorded prices, `deviates_from_assessment = True`. Snapshot bound = `size_gib × PricingLookups.snapshot_gb_month()`. `plan` exits 1 with no file written when every volume is refused.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_iac.py
from cloudfix.common.iac import iac_signals
def test_signals():
    assert iac_signals({"aws:cloudformation:stack-name": "s"})
    assert iac_signals({"pulumi:project": "p"})
    assert iac_signals({"ManagedBy": "Terraform"}) and iac_signals({"terraform": "true"})
    assert iac_signals({"kubernetes.io/created-for/pvc/name": "data"})
    assert iac_signals({"ManagedBy": "ops-team"}) == [] and iac_signals({"Name": "db"}) == []
```

```python
# tests/test_gp2_plan.py — build an assessment dir with Task 6's run_assess (stubbed), then plan with stubbed ec2/pricing
# test_plan_targets_match_assessment_and_hash_is_of_file_bytes:
#   plan volumes[0].target == {"type":"gp3","size_gib":100,"iops":3000,"throughput":125}; deviates False;
#   sha256 returned == hashlib.sha256(plan_path.read_bytes()).hexdigest(); expires_at - created_at == 24h
# test_plan_refusals (parametrized): dontFixIt tag; ManagedBy=terraform tag; type gp3; size 200 vs 100;
#   modification "optimizing"; last modification started 5 h before now; state "creating"
#   -> each lands in refused with a reason mentioning the cause; all refused -> CLI exit 1 and no plan.json
# test_throughput_override_recomputes_estimate_and_marks_deviation:
#   --throughput vol-1=128 on 100 GiB: target throughput 128, deviates True,
#   estimate.proposed_annual_cost == approx(96 + 12*3*0.04) and display present
# test_override_outside_gp3_limits_is_refused: iops 2000; throughput 2001; throughput 1000 with iops 3000
# test_snapshot_cost_upper_bound: snapshot price row 0.05 -> 100 GiB -> 5.0 and "$5.00"
# test_change_json_has_change_note_and_iac_evidence:
#   change["change_note"] ends with "skills/co-gp2-to-gp3-fix/iac.md"; a ManagedBy=terraform volume carries iac_evidence
```

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_iac.py tests/test_gp2_plan.py -v` → FAIL.
- [ ] **Step 3: Implement `iac.py`, `plan.py` and CLI wiring.** CLI flags: `plan --assessment DIR --management {not-managed,unknown} [--volume-id ID]... [--iops VOL=N]... [--throughput VOL=N]... --out DIR`; `change --assessment DIR [--volume-id ID]... [--iops VOL=N]... [--throughput VOL=N]... [--repo-hint X] [--delivery {draft_pr,local_patch}] --out FILE`. `plan` emits `{"status":"planned","plan_file","sha256","volumes":[ids],"refused":[...]}`. The AWS caller uses the assessment's region and profile with `FIXER_OPS`.
- [ ] **Step 4: Run to verify pass** → PASS (full suite).
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): fixer plan with IaC refusal, overrides and change.json"`.

---

### Task 9: Fixer `apply` — approval and resumable execution

**Files:**
- Create: `cloudfix/fixers/gp2_to_gp3/apply.py`
- Modify: `cloudfix/cli.py` (add `gp2-to-gp3-fix apply`)
- Test: `tests/test_gp2_apply.py`

**Interfaces:**
- Consumes: Task 8's plan file and `FIXER_OPS`; `aws.Caller`.
- Produces:
  - `apply.Approval` frozen dataclass: `kind: Literal["after_plan","prior"], wording: str`.
  - `apply.STATES = ("planned","snapshot_requested","snapshot_completed","modification_submitted","optimizing","completed","verified")`; terminal also `"failed"`.
  - `apply.POLL_SECONDS = 15`; `apply.DEFAULT_WAIT_MINUTES = 15`.
  - `apply.apply_plan(plan_path: Path, approve_sha256: str, approval: Approval, aws: Caller, *, confirm_direct: bool, wait_minutes: float, now: Callable[[], datetime], sleep: Callable[[float], None]) -> dict` — returns `{"status": "completed"|"in_progress"|"failed", "volumes": {id: {"state", "snapshot_id", "reason"?}}, "execution_file"}` and persists `execution.json` beside the plan after every state change: `{"plan_id","plan_sha256","approval":{"kind","wording","recorded_at"},"confirmed_direct_management": bool,"volumes":{id:{"state","snapshot_id","already_at_target","history":[{"state","at"}],"reason"?}},"calls":[recorded calls]}`.

Rules:
- Refuse (`InputError`, no AWS write) when: SHA-256 of `plan.json` bytes ≠ `approve_sha256`; `now() > expires_at` **and** no `execution.json` exists yet (an execution already started may resume after expiry); `approval.kind == "prior"` and any volume `deviates_from_assessment` or has `management_basis != "not_managed_by_code"`; any volume has `management_basis == "unknown"` and not `confirm_direct`; `GetCallerIdentity` account ≠ plan account.
- Per volume, reconcile then act, never sending a write that AWS shows already happened:
  - `planned`: `DescribeVolumes`; if already `gp3` with target IOPS/throughput → `verified`, `already_at_target: true`, no writes. Else re-check plan preconditions (still gp2, same size, state ok, no active modification); look for an existing snapshot with `DescribeSnapshots {"OwnerIds":["self"],"Filters":[{"Name":"tag:cloudfix-ai:planId","Values":[plan_id]},{"Name":"tag:cloudfix-ai:originalResourceId","Values":[vol]}]}`; if none, `CreateSnapshot {"VolumeId","Description":"cloudfix-ai gp2-to-gp3 recovery point <plan_id>","TagSpecifications":[{"ResourceType":"snapshot","Tags":<the four cloudfix-ai tags>}]}`. If `CreateSnapshot` raises, query by tag once more before giving up; the state becomes `snapshot_requested` once a snapshot ID is known.
  - `snapshot_requested`: `DescribeSnapshots {"SnapshotIds":[id]}` → `completed` advances, `error` → `failed`, `pending` waits.
  - `snapshot_completed`: `DescribeVolumesModifications`; a modification started after `created_at` targeting gp3 with the planned IOPS/throughput means already submitted; else `ModifyVolume {"VolumeId","VolumeType":"gp3","Iops","Throughput"}` → `modification_submitted`.
  - `modification_submitted`/`optimizing`: latest modification state `modifying` stays, `optimizing` → `optimizing` (in progress), `completed` → `completed`, `failed` → `failed` with AWS's `StatusMessage`. No automatic retry.
  - `completed`: `DescribeVolumes` must show `gp3`, target IOPS and throughput → `CreateTags {"Resources":[vol],"Tags":<four tags>}` → `verified`; mismatch → `failed`.
- Loop over volumes; between polls call `sleep(POLL_SECONDS)` until every volume is terminal or `wait_minutes` elapse by `now()`. Verified and failed volumes are never touched again on a rerun.
- CLI: `apply --plan FILE --approve SHA --approval-kind {after_plan,prior} --approval-text TEXT [--confirm-direct-management] [--wait MINUTES]`; exit 0 completed, 3 in progress, 1 failed or error.

- [ ] **Step 1: Write the failing tests** (stubbed ec2/sts; `now` from a list of datetimes; `sleep` records calls)

```python
# tests/test_gp2_apply.py — names and assertions
# test_hash_mismatch_refused_before_any_call: aws.reads == []
# test_prior_approval_refused_for_override_or_unknown_management
# test_unknown_management_requires_confirm_direct
# test_happy_path_single_run: snapshot pending->completed, modification modifying->completed, verify
#   -> status "completed"; CreateSnapshot TagSpecifications tags == the four cloudfix-ai tags and no key starts "cloudfix:";
#   CreateTags on the volume with the same tags; execution.json history lists every state in STATES order
# test_in_progress_then_resume: first run wait_minutes=0 ends at snapshot_requested -> status "in_progress" (CLI exit 3);
#   second run continues without a second CreateSnapshot
# test_lost_create_snapshot_response_found_by_tag: CreateSnapshot raises; DescribeSnapshots by tag returns snap-1
#   -> continues with snap-1, exactly one CreateSnapshot call recorded
# test_lost_modify_response_found_in_history: execution at snapshot_completed, history already shows a matching
#   modification -> no ModifyVolume call
# test_already_at_target_verified_without_writes: no CreateSnapshot, ModifyVolume or CreateTags
# test_optimizing_is_in_progress_not_done
# Review Focus 3:
# test_one_volume_fails_others_complete: vol-1 modification "failed" with StatusMessage, vol-2 completes
#   -> status "failed", vol-2 "verified", vol-1 reason contains the StatusMessage; a rerun makes no calls for either
# Review Focus 4:
# test_account_mismatch_refused_before_writes: STS returns another account -> InputError; no ec2 write in reads
# Review Focus 5:
# test_expired_plan_refused_to_start_but_resumes_when_started: expired + no execution.json -> InputError;
#   expired + execution.json at snapshot_requested -> continues
```

- [ ] **Step 2: Run to verify failure:** `uv run --frozen --directory plugins/cloudfix/python pytest tests/test_gp2_apply.py -v` → FAIL.
- [ ] **Step 3: Implement `apply.py` and CLI wiring** (CLI passes `datetime.now(timezone.utc)` and `time.sleep`).
- [ ] **Step 4: Run to verify pass** → PASS (full suite).
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): resumable fixer apply with approval checks"`.

---

### Task 10: Fixer `status` and `cleanup`, fixer skill and change note

**Files:**
- Create: `cloudfix/fixers/gp2_to_gp3/cleanup.py`, `plugins/cloudfix/skills/co-gp2-to-gp3-fix/SKILL.md`, `plugins/cloudfix/skills/co-gp2-to-gp3-fix/iac.md`
- Modify: `cloudfix/cli.py` (add `status`, `cleanup`), `tests/test_skill_commands.py` (`SKILLS` += `"co-gp2-to-gp3-fix"`)
- Test: `tests/test_gp2_cleanup.py`

**Interfaces:**
- Consumes: Task 9's `execution.json`, `apply.Approval`.
- Produces:
  - `cleanup.status(plan_path: Path, aws: Caller) -> dict` — per volume: recorded state, snapshot ID, live volume type/IOPS/throughput, latest modification state; reads only.
  - `cleanup.cleanup(plan_path: Path, approve_sha256: str, approval: Approval, aws: Caller) -> dict` — deletes the snapshot only for volumes in `verified` with `already_at_target` false whose snapshot, re-read with `DescribeSnapshots`, carries `cloudfix-ai:planId == plan_id`; records `{"snapshot_deleted": true}` in `execution.json`; others listed as skipped with a reason. Hash mismatch → `InputError`.
  - CLI: `status --plan FILE`; `cleanup --plan FILE --approve SHA --approval-kind {after_plan,prior} --approval-text TEXT`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gp2_cleanup.py
# test_status_makes_no_writes: reads contain only Describe* operations
# test_cleanup_deletes_only_verified_tagged_snapshots: verified vol with tagged snap -> DeleteSnapshot once;
#   in_progress vol skipped "not verified"; snapshot whose planId tag differs skipped "tag mismatch"
# test_cleanup_hash_mismatch_refused
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement `cleanup.py` and CLI wiring.**
- [ ] **Step 4: Write `co-gp2-to-gp3-fix/SKILL.md`.** Frontmatter `name: co-gp2-to-gp3-fix`, description (prepare and carry out an authorized GP2→GP3 change for volumes from a saved assessment, either directly or through the PR worker). Sections, each carrying the spec's rules:
  1. Command form and start (same as Task 7 sections 1–2), plus: the input is a completed assessment run directory.
  2. **Route each volume**: the IaC rule and evidence list; ask the user about management when no signal; the routing table; the future-launches exception and when it does not apply.
  3. **Code-managed path**: `cloudfix gp2-to-gp3-fix change --assessment <dir> --out <change.json>` then hand `change.json` to `co-iac-pr`; with no repository found for an IaC-managed volume, stop and report the evidence.
  4. **Direct path**: state the permissions; `cloudfix gp2-to-gp3-fix plan …`; show the plan with its estimate, snapshot cost bound, performance disclosure (GP2 burst vs target throughput; instance-type support not checked) and refusals.
  5. **Approval**: the two kinds and the exact conditions for prior approval; pass the hash, kind and the user's exact words; preparing a plan or PR is never approval.
  6. **Apply and resume**: `cloudfix gp2-to-gp3-fix apply …`; exit 3 means rerun the same command later; `cloudfix gp2-to-gp3-fix status --plan <plan.json>`; report each volume separately; `optimizing` is in progress.
  7. **Cleanup**: `cloudfix gp2-to-gp3-fix cleanup …` only with its own approval.
  8. **Report**: carry over section 6 of the current `skills/co-gp2-to-gp3/SKILL.md` (proposal vs execution, outcome states, savings remain estimates, private evidence handling).
- [ ] **Step 5: Write `iac.md`** with the spec's change-note content: Terraform/OpenTofu (`aws_ebs_volume` `type`/`iops`/`throughput` in place; `aws_instance` `root_block_device`/`ebs_block_device`; launch templates = future launches), CloudFormation (`AWS::EC2::Volume` `VolumeType`/`Iops`/`Throughput`, update without interruption; launch templates and block device mappings), CDK (`ec2.Volume` `volumeType`, `iops`, `throughput`; `BlockDeviceVolume.ebs(...)`), Pulumi (`aws.ebs.Volume` `type`/`iops`/`throughput`), Kubernetes (StorageClass `parameters.type`; existing PVs are not changed by a StorageClass edit — a future-launch case), what never to change (size, encryption/KMS, deletion policy, unrelated environments), and validators per tool. Use the `target` values from `change.json`; mention `deviates_from_assessment`.
- [ ] **Step 6: Run the suite** — `uv run --frozen --directory plugins/cloudfix/python pytest -v` → PASS (skill command test now covers the fixer skill).
- [ ] **Step 7: Commit** — `git commit -m "feat(cloudfix): fixer status/cleanup, co-gp2-to-gp3-fix skill and change note"`.

---

### Task 11: Generic PR worker skill and Claude agent files

**Files:**
- Create: `plugins/cloudfix/skills/co-iac-pr/SKILL.md`, `plugins/cloudfix/skills/co-iac-pr/review.md`, `plugins/cloudfix/agents/iac-pr-worker.md`, `plugins/cloudfix/agents/iac-pr-reviewer.md`
- Test: `tests/test_pr_worker_docs.py`

**Interfaces:**
- Consumes: the `change.json` shape from Task 8.
- Produces: the skill `co-iac-pr` with sections named exactly `## Orchestrate`, `## Worker`, `## Reviewer`, which the two agent files reference.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pr_worker_docs.py
from pathlib import Path
PLUGIN = Path(__file__).resolve().parents[2]

def test_worker_skill_is_finder_agnostic():
    text = (PLUGIN / "skills/co-iac-pr/SKILL.md").read_text().lower()
    for word in ("gp2", "gp3", "ebs", "volume"):
        assert word not in text

def test_agents_point_at_skill_sections():
    worker = (PLUGIN / "agents/iac-pr-worker.md").read_text()
    reviewer = (PLUGIN / "agents/iac-pr-reviewer.md").read_text()
    assert "co-iac-pr" in worker and "## Worker" in worker
    assert "co-iac-pr" in reviewer and "## Reviewer" in reviewer
    assert "Edit" not in reviewer.split("---")[1] and "Write" not in reviewer.split("---")[1]  # read-only tools in frontmatter
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Write the four files.** `SKILL.md` frontmatter `name: co-iac-pr`, description (turn a fixer's `change.json` into a reviewed draft PR or local patch in the repository that manages the resource). `## Orchestrate`: read `change.json` and the change note; the six spec steps in order (find repo — `repo_hint`, current checkout, user-named local folders, no GitHub search or cloning unless the user names the repo, never invent one; read before editing; dispatch the worker; dispatch a fresh reviewer; up to two fix rounds; deliver per `delivery`; report effect "per provider documentation, not verified by a plan" unless the user asked for a plan). In Claude Code dispatch the `iac-pr-worker` and `iac-pr-reviewer` agents; in Codex use subagents where available, otherwise run the Worker and Reviewer sections as separate passes. `## Worker`: branch, edit per change note, offline validators only (`terraform fmt -check`, `terraform init -backend=false` + `terraform validate`, `cfn-lint`, `cdk synth` only without AWS credentials), never plan/apply/deploy/publish, commit. `## Reviewer`: follow `review.md`. `review.md`: check the diff against `change.json` targets and the change note's "never change" list, unrelated edits, environment scope, validator results, and in-place vs replacement claims; return findings as a list with file:line and severity. PR body rules: evidence summary only, never account-wide evidence, link the assessment privately. Agent files: frontmatter `name`, `description`, `tools` (worker: `Read, Grep, Glob, Bash, Edit, Write`; reviewer: `Read, Grep, Glob, Bash`), body: "Load the co-iac-pr skill and follow its `## Worker` (resp. `## Reviewer`) section with the inputs you were given; return …".
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cloudfix): generic co-iac-pr worker skill and Claude agents"`.

---

### Task 12: Marketplaces, manifests, README, CI and removal of the bundle

**Files:**
- Create: `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, `plugins/cloudfix/.claude-plugin/plugin.json`, `plugins/cloudfix/.codex-plugin/plugin.json`, `.github/workflows/test.yml`
- Modify: `README.md` (rewrite)
- Delete: `skills/co-gp2-to-gp3/` (SKILL.md and `scripts/gp2.cjs`)
- Test: `tests/test_manifests.py`

**Interfaces:**
- Consumes: `cloudfix.__version__`.
- Produces: installable marketplaces named `cloudfix-ai` with one plugin `cloudfix`.

Manifest content (check field names against both vendors' current docs while writing; `claude plugin validate` is authoritative for the Claude files):

```json
// .claude-plugin/marketplace.json
{"name": "cloudfix-ai", "owner": {"name": "CloudFix"},
 "plugins": [{"name": "cloudfix", "source": "./plugins/cloudfix",
              "description": "Assess and fix AWS cost optimization opportunities from your agent."}]}
// .agents/plugins/marketplace.json
{"name": "cloudfix-ai", "interface": {"displayName": "CloudFix AI"},
 "plugins": [{"name": "cloudfix", "source": {"source": "local", "path": "./plugins/cloudfix"},
              "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}
// plugins/cloudfix/.claude-plugin/plugin.json
{"name": "cloudfix", "version": "0.1.0", "description": "<same>", "author": {"name": "CloudFix"}}
// plugins/cloudfix/.codex-plugin/plugin.json
{"name": "cloudfix", "version": "0.1.0", "description": "<same>", "author": {"name": "CloudFix"},
 "skills": "./skills/", "interface": {"displayName": "CloudFix", "shortDescription": "<same>", "developerName": "CloudFix", "category": "Productivity"}}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifests.py
import json
from pathlib import Path
from cloudfix import __version__
REPO = Path(__file__).resolve().parents[4]

def test_versions_agree():
    for p in ("plugins/cloudfix/.claude-plugin/plugin.json", "plugins/cloudfix/.codex-plugin/plugin.json"):
        assert json.loads((REPO / p).read_text())["version"] == __version__

def test_both_marketplaces_point_at_the_plugin():
    claude = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    codex = json.loads((REPO / ".agents/plugins/marketplace.json").read_text())
    assert claude["plugins"][0]["source"] == "./plugins/cloudfix"
    assert codex["plugins"][0]["source"] == {"source": "local", "path": "./plugins/cloudfix"}
    assert claude["name"] == codex["name"] == "cloudfix-ai"

def test_old_bundle_is_gone():
    assert not (REPO / "skills/co-gp2-to-gp3").exists()
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Write the four manifests and delete `skills/co-gp2-to-gp3/`** (`git rm -r skills/co-gp2-to-gp3`).
- [ ] **Step 4: Rewrite `README.md`** around the plugin, keeping its "Access and data" and "Reading the result" substance updated for the Python tool: what the plugin does (three skills); prerequisite uv with per-OS install options; install for Claude Code (`/plugin marketplace add trilogy-ai-group/cloudfix-ai`, `/plugin install cloudfix@cloudfix-ai`) and Codex (`codex plugin marketplace add trilogy-ai-group/cloudfix-ai`, enable `cloudfix@cloudfix-ai`); manual install copies the whole `plugins/cloudfix/`; no setup step (first command builds the environment); access and data (read-only assessment permissions; fixer write permissions; nothing sent to CloudFix; `cloudfix-ai:*` tags and why not `cloudfix:*`; `cloudfix:dontFixIt` honoured); reading results; pilot notes; version via `cloudfix --version` and the git tag; supported platforms. Keep every paragraph on one line (no hard wraps), matching the existing README.
- [ ] **Step 5: Write `.github/workflows/test.yml`**: on push and pull_request; matrix `ubuntu-latest`, `macos-latest`, `windows-latest`; steps: checkout, `astral-sh/setup-uv` pinned to its current major release, `uv lock --check --directory plugins/cloudfix/python`, `uv run --frozen --directory plugins/cloudfix/python pytest -q`.
- [ ] **Step 6: Verify**
  - `uv run --frozen --directory plugins/cloudfix/python pytest -v` → all PASS.
  - `uv lock --check --directory plugins/cloudfix/python` → exit 0.
  - `claude plugin validate plugins/cloudfix` → "Validation passed" (warnings reviewed).
  - `uv run --frozen --no-dev --project plugins/cloudfix/python cloudfix gp2-to-gp3 describe` → settings JSON.
  - `git grep -n "gp2.cjs"` → only the design doc and this plan mention it.
- [ ] **Step 7: Commit** — `git commit -m "feat(cloudfix): dual marketplaces, manifests, README and CI; remove gp2.cjs bundle"`.

- [ ] **Step 8: Manual live check (needs an approved dev/test account with `pricing:GetProducts`)** — run `cloudfix gp2-to-gp3 assess` against one GP2 volume and confirm `cost_basis.fallback_count == 0` (proves the `regionCode` Pricing filters match live products); record the result in the PR description, not in the repo.
