#!/usr/bin/env python3
"""Assess unattached (available) EBS volumes for snapshot-then-delete with the customer's own AWS read access.
Standard library only; AWS reads go through the AWS CLI v2. The rules are the ones in ../references/.
Exit 0: complete. Exit 2: the run is partial (evidence missing, a read failed or the self-check found a problem). Exit 1: the run failed."""
import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import traceback
from decimal import Decimal, ROUND_HALF_UP

# Skill constants the shared skeleton reads.
MODE_FLAG, PRICING_MODES = "--pricing-mode", ("public_prices", "billing", "supplied")
RESOURCE_NOUN, EMPTY_INVENTORY_NOUN = "volume", "available volumes"
REPORT_FOOTER = "Nothing was executed; these are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`."
ALLOWED_READS = frozenset({("sts", "GetCallerIdentity"), ("pricing", "GetProducts"), ("ec2", "DescribeVolumes"), ("ec2", "DescribeSnapshots"),
                           ("cloudwatch", "GetMetricData"), ("ce", "GetDimensionValues"), ("ce", "GetCostAndUsage")})  # the finder's assessment reads; aws() refuses anything else

# --- shared skeleton (identical in every CloudFix skill script) ---
# Reads the skill constants declared above it (MODE_FLAG, PRICING_MODES, RESOURCE_NOUN, EMPTY_INVENTORY_NOUN, REPORT_FOOTER, ALLOWED_READS)
# and these skill functions defined below it: add_skill_arguments, describe_change, report_headline (the null-total
# headline), report_context (lines after the headline), report_notes (before the qualifications), report_bullets (after), run.
# A skill may redefine display_id below the end marker when its guide shows a short name in the report tables.

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXIT_CODES = {"complete": 0, "partial": 2, "failed": 1}
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

class InputError(Exception):
    """Input this run cannot use: a bad argument or unreadable replay evidence. main() prints it and exits 1."""

def parse_time(text):
    # Python 3.9 reads only three or six fraction digits; AWS and JavaScript also write one, two or more.
    moment = datetime.datetime.fromisoformat(re.sub(r"\.(\d+)", lambda digits: "." + (digits.group(1) + "000000")[:6], text.replace("Z", "+00:00"), count=1))
    if moment.tzinfo is None:  # no offset: read as UTC, so the result never depends on the machine's time zone
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)

def iso_js(value):
    """A timestamp the way JavaScript's toISOString writes it (milliseconds, Z)."""
    moment = parse_time(value) if isinstance(value, str) else value
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)

def to_fixed(value, digits):
    """JavaScript's Number.toFixed: half-up on the exact binary value (0.125 -> '0.13')."""
    return str(Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))

def js_number(value):
    """A JavaScript number rendered as String(x): integers without a decimal point."""
    if not isinstance(value, float) or not math.isfinite(value) or not 1e-6 <= abs(value) < 1e21:
        return re.sub(r"e([+-])0", r"e\1", repr(value))  # JavaScript writes 1e-7, not 1e-07
    text = format(Decimal(repr(value)), "f")  # plain decimals from 1e-6 up to 1e21
    return text.rstrip("0").rstrip(".") if "." in text else text

def money(value):
    return "$" + to_fixed(value, 2)

def aws(service, operation, params, region, profile=None):
    """One AWS CLI call, only for a read in ALLOWED_READS. Returns an evidence record; a failure keeps the error, never a fake output."""
    if (service, operation) not in ALLOWED_READS:
        raise RuntimeError("%s:%s is not one of this skill's assessment reads; nothing was called" % (service, operation))
    command = ["aws", service, re.sub(r"(?<!^)(?=[A-Z])", "-", operation).lower(), "--cli-input-json", json.dumps(params),
               "--output", "json", "--no-paginate", "--no-cli-pager", "--region", region] + (["--profile", profile] if profile else [])
    record = {"service": service, "operation": operation, "region": region, "profile": profile,
              "input": params, "observed_at": iso_js(datetime.datetime.now(datetime.timezone.utc))}
    try:
        done = subprocess.run(command, capture_output=True, text=True, env=dict(os.environ, AWS_PAGER=""), timeout=300)
        stderr, returncode = done.stderr.strip(), done.returncode
        output = json.loads(done.stdout) if returncode == 0 else {}  # every allowed read answers with JSON: no output is a failed read
        if not isinstance(output, dict):
            raise ValueError("not a JSON object")
    except (OSError, subprocess.TimeoutExpired, ValueError) as fault:
        stderr, returncode = "The AWS CLI output could not be used: %s" % fault, 1
    if returncode == 0:
        record["output"] = output
        return record
    match = re.search(r"An error occurred \((\w+)\) when calling the \w+ operation(?: \([^)]*\))?: (.*)", stderr, re.S)
    code, message = (match.group(1), match.group(2).strip()) if match else ("CLIError", stderr)
    record["error"] = {"code": code, "message": message, "aws_request": {"action": "%s:%s" % (service, operation), "profile": profile, "region": region, "input": params}}
    return record

class Evidence:
    """Evidence files beside assessment.json: appended live, or answered from the saved records on replay."""
    live = None  # the live run's evidence, which main() saves when the run raises

    def __init__(self, directory, replay):
        self.directory, self.replay, self.files, self.on_disk = directory, replay, {}, set()
        Evidence.live = None if replay else self
        if replay:
            try:
                for name in sorted(os.listdir(directory)):
                    if name.endswith(".json"):
                        with open(os.path.join(directory, name), encoding="utf-8") as handle:
                            self.files[name[:-5]] = json.load(handle)
            except (OSError, ValueError) as fault:
                raise InputError("The replay evidence could not be read: %s" % fault)
        self.used = {name: [False] * len(records) for name, records in self.files.items() if isinstance(records, list)}

    def read(self, file, service, operation, params, region, profile=None):
        """Returns (record, index) or (None, request) when replay holds no such record."""
        if self.replay:
            records = self.files.get(file, [])
            if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
                raise InputError("evidence/%s.json must be a list of read records" % file)
            matches = [index for index, record in enumerate(records) if not self.used[file][index] and (record.get("service"), record.get("operation"), record.get("region"), record.get("input")) == (service, operation, region, params)]  # another region's or service's answer is not this read's answer
            for index in matches:
                fault = records[index].get("error")
                if not (isinstance(fault, dict) and isinstance(fault.get("code"), str) and isinstance(fault.get("message"), str)) if "error" in records[index] else not isinstance(records[index].get("output"), dict):
                    raise InputError("evidence/%s.json#/%d holds neither an output object nor an error with a code and a message" % (file, index))  # never read as an empty answer
                self.used[file][index] = True
            if any("error" not in records[index] for index in matches[:-1]):  # only failed attempts may precede the assessed one
                raise InputError("evidence/%s.json holds more than one answer to the same %s request" % (file, operation))
            if matches:  # a retried read keeps its failed attempts (guided.md); the last attempt is the one assessed
                return records[matches[-1]], matches[-1]
            return None, {"kind": "aws_read", "name": operation, "service": service, "region": region, "parameters": params}
        for name in [name for name in self.files if name not in self.on_disk]:  # what the run recorded itself, such as a snapshot, is on disk before the next call
            self.write(name)
        record = aws(service, operation, params, region, profile)
        self.files.setdefault(file, []).append(record)
        self.write(file)  # ponytail: rewrites the whole file per read; append-only JSON lines if a file ever grows large enough to matter
        return record, len(self.files[file]) - 1

    def pages(self, file, service, operation, params, region, profile=None, token="NextToken"):
        """Every page of one request: (records, failure) with failure {"error", "ref"}, {"missing": request} or None."""
        records, seen = [], set()
        while True:
            record, position = self.read(file, service, operation, params, region, profile)
            if record is None:
                return records, {"missing": position}
            records.append((record, position))
            if "error" in record:
                return records, {"error": record["error"], "ref": self.ref(file, position)}
            following = record["output"].get(token)
            if following is None:
                return records, None
            if not isinstance(following, str) or not following or following in seen:  # an empty token is not the end; a repeated one would page without end
                return records, {"error": {"code": "InvalidPageToken", "message": "%s returned a repeated or malformed %s; the read is incomplete." % (operation, token)},
                                 "ref": self.ref(file, position)}
            seen.add(following)
            params = dict(params, **{token: following})

    def ref(self, file, index, pointer=""):
        return "evidence/%s.json#/%d%s" % (file, index, pointer)

    def write(self, name):
        """One evidence file, replaced whole: a run that is stopped leaves complete files, never half of one."""
        os.makedirs(self.directory, exist_ok=True)
        document = self.files[name]
        text = json.dumps(list(document) if isinstance(document, list) else document, indent=1)
        part = os.path.join(self.directory, "%s.%d-%x.part" % (name, os.getpid(), id(text)))  # its own name, also beside another thread's write
        with open(part, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(part, os.path.join(self.directory, name + ".json"))
        self.on_disk.add(name)

    def save(self):
        for name in list(self.files):
            self.write(name)

def resolve_ref(reference, root):
    path, _, pointer = reference.partition("#")
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        data = json.load(handle)
    for part in pointer.strip("/").split("/") if pointer.strip("/") else []:
        data = data[int(part)] if isinstance(data, list) else data[part.replace("~1", "/").replace("~0", "~")]
    return data

def self_check(out_dir):
    """Stage 5 on the saved files: counts, totals and every evidence reference, re-read from disk."""
    with open(os.path.join(out_dir, "assessment.json"), encoding="utf-8") as handle:
        saved = json.load(handle)
    rows, totals, problems = saved["findings"], saved["totals"], []
    if {d: sum(row["decision"] == d for row in rows) for d in totals["decision_counts"]} != totals["decision_counts"]:
        problems.append("decision counts do not match the rows")
    recommended = [row for row in rows if row["decision"] == "recommended"]
    if recommended and totals["annual_savings"] is None and not totals.get("reason_not_totaled"):
        problems.append("recommended rows without a total need reason_not_totaled")
    included = [row for row in recommended if row["resource"]["id"] not in (totals.get("excluded_ids") or [])]  # as report_table: a resource with several rows is shown row by row, not added
    for field in ("current_annual", "target_annual", "annual_savings"):
        amounts = [row["costs"].get(field) for row in included]
        if totals.get(field) is None:
            continue
        if not amounts or None in amounts:
            problems.append("totals/%s is stated without that amount on every recommended row" % field)
        elif abs(Decimal(str(totals[field])) - sum(Decimal(str(amount)) for amount in amounts)) > Decimal("0.005"):
            problems.append("totals/%s differs from the sum of the recommended rows" % field)
    counts = saved["coverage"]
    if None not in (counts["discovered_count"], counts["assessed_count"]) and counts["discovered_count"] != counts["assessed_count"] + len(counts["unassessed_ids"]):
        problems.append("discovered_count does not equal assessed plus unassessed")
    for holder in [saved] + rows + saved["issues"]:
        for reference in holder.get("evidence_refs", []):
            try:
                resolve_ref(reference, out_dir)
            except (OSError, KeyError, IndexError, ValueError, TypeError):  # TypeError: a pointer into a scalar
                problems.append("evidence reference does not resolve: " + reference)
    return problems

def display_id(row):
    """The Resource cell of the report tables."""
    return row["id"]

def report_table(assessment):
    """The customer report of the assessment guide, from assessment.json only."""
    rows, totals, coverage, run = (assessment[key] for key in ("findings", "totals", "coverage", "run"))
    recommended = sorted((row for row in rows if row["decision"] == "recommended"), key=lambda row: (-(row["costs"].get("annual_savings") or 0), row["id"]))
    excluded = set(totals.get("excluded_ids") or [])
    included = [row for row in recommended if row["resource"]["id"] not in excluded]
    left_out = [row for row in recommended if row["resource"]["id"] in excluded]
    others = [row for row in rows if row["decision"] != "recommended"]
    amount = lambda value: money(value) if value is not None else "—"
    count = lambda n: "one %s" % RESOURCE_NOUN if n == 1 else "%s %ss" % (WORDS.get(n, n), RESOURCE_NOUN)
    if run["status"] == "failed":
        headline = "**Savings not established: the run failed.**"
    elif coverage["discovered_count"] == 0 and run["status"] == "complete":
        headline = "**No %s found, nothing to optimize.**" % EMPTY_INVENTORY_NOUN
    elif totals["annual_savings"] is None:
        headline = report_headline(assessment, recommended)
    elif left_out:
        headline = "**Estimated subtotal: %s/year across %s; %s shown separately.**" % (money(totals["annual_savings"]), count(len(included)), count(len(excluded)))
    else:
        prefix = "Estimated savings (subtotal for the assessed scope)" if run["status"] == "partial" else "Estimated savings"
        headline = "**%s: %s/year across %s.**" % (prefix, money(totals["annual_savings"]), count(len(included)))
    lines = [headline, ""] + report_context(assessment)
    metadata = assessment.get("details", {}).get("run_metadata") or assessment.get("run_metadata") or {}
    if metadata.get("run_classification", "standard") != "standard" or metadata.get("configuration_overrides"):
        lines.append("Run classification: %s; configuration overrides: %s." % (metadata.get("run_classification"), json.dumps(metadata.get("configuration_overrides"))))
    if recommended:
        if totals.get("basis"):
            period = next((row["costs"]["source_period"] for row in recommended if row["costs"].get("source_period")), {"start": "?", "end": "?"})
            caption = {"public_price": "AWS public prices", "caller_supplied": "Supplied prices", "resource_billed": "Billed costs",
                       "account_effective_rate": "Account and region effective billing rate for %s to %s (end exclusive)" % (period["start"][:10], period["end"][:10])}
            lines += ["", caption[totals["basis"]] + "; estimate for the proposed configuration. Currency: %s." % totals["currency"]]
        lines += ["", "| Resource | Region | Proposed change | Current per year | After per year | Savings per year |", "| --- | --- | --- | ---: | ---: | ---: |"]
        lines += ["| %s | %s | %s | %s | %s | %s |" % (display_id(row), row["resource"]["region"], describe_change(row), amount(row["costs"].get("current_annual")),
                  amount(row["costs"].get("target_annual")), amount(row["costs"].get("annual_savings"))) for row in included[:25]]
        if len(included) > 25:
            lines.append("| %d more rows in assessment.json | | | | | |" % (len(included) - 25))
        lines.append("| **Total: %d %s** | | | **%s** | **%s** | **%s** |" % (
            len(included), RESOURCE_NOUN if len(included) == 1 else RESOURCE_NOUN + "s", amount(totals.get("current_annual")),
            amount(totals.get("target_annual")), amount(totals["annual_savings"]) if totals["annual_savings"] is not None else "Not established"))
    if left_out:
        lines += ["", "Shown separately, not added: %s" % totals.get("reason_not_totaled")]
        lines += ["- %s (%s): current %s/year, after %s/year, saving %s/year%s." % (display_id(row), row["resource"]["region"], amount(row["costs"].get("current_annual")),
                  amount(row["costs"].get("target_annual")), amount(row["costs"].get("annual_savings")),
                  ", first year %s" % amount(row["costs"]["first_year_savings"]) if row["costs"].get("first_year_savings") is not None else "") for row in left_out]
    if others:
        outcome = {"not_recommended": "Not recommended", "needs_evidence": "More evidence needed", "error": "Assessment error"}
        lines += ["", "| Resource | Region | Outcome | Reason / what is needed |", "| --- | --- | --- | --- |"]
        groups = {}
        for row in others:
            groups.setdefault((row["decision"], row_reason(row, assessment)), []).append(row)
        for (decision, reason), group in groups.items():
            if len(group) <= 3:
                lines += ["| %s | %s | %s | %s |" % (display_id(row), row["resource"]["region"], outcome[decision], reason) for row in group]
            else:
                lines.append("| %d %ss: %s and %d more | %s | %s | %s |" % (len(group), RESOURCE_NOUN, ", ".join(display_id(row) for row in group[:3]), len(group) - 3,
                                                                       ", ".join(sorted({row["resource"]["region"] for row in group})), outcome[decision], reason))
    lines += report_notes(assessment) + [""] + ["- " + text for text in assessment.get("qualifications", [])]
    lines += ["- Run-level %s: %s" % (issue["kind"].replace("_", " "), issue["message"]) for issue in assessment["issues"]
              if issue.get("resource_id") is None and issue["kind"] != "qualification"]
    lines += report_bullets(assessment) + ["", REPORT_FOOTER, "", "For more AWS optimization options, see [CloudFix](https://cloudfix.com/)."]
    return "\n".join(lines) + "\n"

def row_reason(row, assessment):
    for check in row["checks"]:
        if check["status"] == "fail":
            return check.get("reason", check["name"] + " failed.")
    issues = [issue for issue in assessment["issues"] if issue["kind"] != "qualification"]
    own = [issue for issue in issues if issue.get("resource_id") in (row["id"], row["resource"]["id"])]  # a resource can have several rows
    run_level = [issue for issue in issues if issue.get("resource_id") is None]
    if not own and len(run_level) > 1:  # several run-level problems: the row names the one that stopped it, not the first in the list
        stopped = next((check.get("reason", "") for check in row["checks"] if check["status"] != "pass"), "")
        run_level = ([issue for issue in run_level if (issue.get("request") or {}).get("name", "\0") in stopped]  # the read its first unfinished check names
                     or [issue for issue in run_level if issue.get("stage") == "costing"] or run_level)  # or none: its checks ran, so it waits on costing
    for issue in (own + run_level)[:1]:
        read = issue.get("request") or {}
        if read.get("kind") == "aws_read":
            return issue["message"] + " Next read: " + " ".join([read["service"], read["name"]] + ([json.dumps(read["parameters"])] if read.get("parameters") is not None else [])) + "."
        return issue["message"]
    return "—"

def finish(out_dir, evidence, assessment):
    """Save everything, self-check the saved files, write the report, return the exit code."""
    def write(document):
        with open(os.path.join(out_dir, "assessment.json"), "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=1)
            handle.write("\n")
    evidence.replay or evidence.save()  # a live run keeps what it collected even when the report cannot render
    report_table(assessment)  # a value the report cannot render must stop the run before the assessment is written
    evidence.save()
    write(assessment)
    problems = self_check(out_dir)
    if problems:
        assessment["verification"] = {"method": "self_check", "summary": "Self-check found problems: " + "; ".join(problems)}
        assessment["issues"].append({"kind": "calculation_error", "stage": "verification", "message": "; ".join(problems)})
        assessment["run"]["status"] = "partial" if assessment["run"]["status"] == "complete" else assessment["run"]["status"]
        write(assessment)
    report = report_table(assessment)
    if evidence.replay:  # recorded evidence is never presented as a current reading
        report = "> Replayed from saved evidence observed %s. This is not a current reading of the account.\n\n%s" % (assessment["run"]["observed_at"], report)
    try:
        with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as handle:
            handle.write(report)
    except OSError:  # no finished-looking assessment.json without its report
        os.remove(os.path.join(out_dir, "assessment.json"))
        raise
    sys.stdout.write(report)
    return EXIT_CODES[assessment["run"]["status"]]

def main(argv=None):
    started = datetime.datetime.now(datetime.timezone.utc)
    for stream in (sys.stdout, sys.stderr):  # the report holds characters that some default encodings cannot write
        getattr(stream, "reconfigure", lambda **options: None)(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for flag, options in (("--account", {"help": "expected 12-digit workload account; the run stops on a mismatch"}),
                          ("--region", {"help": "AWS region of the reads"}),
                          ("--profile", {"help": "AWS CLI profile for the workload reads (default: the credential chain)"}),
                          (MODE_FLAG, {"choices": PRICING_MODES, "help": "the price basis the user asked for; see SKILL.md"}),
                          ("--costs", {"help": "supplied-cost JSON file for the supplied mode"}),
                          ("--as-of", {"help": "assessment time of a live run (ISO 8601), for example to repeat a recorded run; default: now. A replay takes the saved time"}),
                          ("--replay", {"metavar": "EVIDENCE_DIR", "help": "run on saved evidence, no AWS call"}),
                          ("--out", {"required": True, "help": "new output directory"})):
        parser.add_argument(flag, **options)
    add_skill_arguments(parser)  # may also set defaults, such as a fixed region or mode
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:  # argparse exits 2 on a usage error, which is this script's code for a partial run
        return 1 if stop.code else 0
    missing = [] if args.replay else [name for name in ("account", "region", MODE_FLAG[2:].replace("-", "_")) if getattr(args, name) is None]
    if (os.path.exists(args.out) and (not os.path.isdir(args.out) or os.listdir(args.out))) or missing:
        sys.stderr.write("--out must be a new or empty directory: %s\n" % args.out if not missing else
                         "A live run needs --%s\n" % ", --".join(name.replace("_", "-") for name in missing))
        return 1
    if not args.replay:
        try:
            version = subprocess.run(["aws", "--version"], capture_output=True, text=True).stdout
        except OSError:
            version = ""
        if not version.startswith("aws-cli/2."):
            sys.stderr.write("AWS CLI v2 is required (found: %s). Install it or follow references/guided.md.\n" % (version.strip() or "no aws on PATH"))
            return 1
    try:
        if args.as_of:
            parse_time(args.as_of)
    except ValueError:
        sys.stderr.write("--as-of must be an ISO 8601 time: %s\n" % args.as_of)
        return 1
    if args.costs and not os.path.isfile(args.costs):
        sys.stderr.write("--costs is not a file: %s\n" % args.costs)
        return 1
    try:
        code = run(args)
        args.replay or sys.stdout.write("\nElapsed: %.0f s for the assessment script.\n" % (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds())  # live only: a replay stays byte-identical
        return code
    except InputError as fault:
        sys.stderr.write("%s\n" % fault)
        return 1
    except (TypeError, KeyError, AttributeError, ValueError, IndexError, ArithmeticError) as fault:
        if not args.replay:  # a live run keeps its traceback: that is a defect here or in AWS output, not in a saved file
            raise
        line = [frame.lineno for frame in traceback.extract_tb(fault.__traceback__) if frame.filename == __file__][-1]
        # ponytail: one message for both causes; separating them needs a guard around every evidence read
        sys.stderr.write("A saved evidence file, or the script itself, has a value of the wrong type or shape (%s: %s, assess.py line %d). Compare the files with references/guided.md; if they match, the defect is in the script at that line.\n" % (type(fault).__name__, fault, line))
        return 1
    finally:
        if not args.replay and Evidence.live:  # a live run keeps the reads it made, however it ends: they cost AWS calls and can be replayed
            Evidence.live.save()

# --- end shared skeleton ---

SKILL_ID, RESOURCE_TYPE = "co-ebs-unattached", "AWS::EC2::Volume"
FINDER_ID = "UnusedEBSVolumes"
CHECK_NAMES = ("RecommendationFinalized", "ResourceNotDeleted", "IsValidState", "VolumeExpectedType", "VolumeAgeXDays",
               "DetachedXDaysAgo", "MinimalSavings", "MaximumSavings")
FAIL_FAST = ("ResourceNotDeleted", "IsValidState", "VolumeExpectedType", "DetachedXDaysAgo")
EXPECTED_TYPES = ["gp2", "gp3", "io1", "io2", "st1", "sc1", "standard"]
IMMEDIATE_ARCHIVAL_TYPES = ("st1", "sc1", "standard")
MIN_AGE_DAYS, LOOKBACK_DAYS, BATCH, QUERY_LIMIT = 30, 365, 200, 500
DEFAULT_CONFIGURATION, ARCHIVE_FALLBACK = {"volumeAgeThreshold": 7, "minimalSavings": 0}, 0.0125
BASIS_BY_MODE = {"public_prices": "public_price", "billing": "account_effective_rate", "supplied": "caller_supplied"}
METRICS = (("readOps", "VolumeReadOps"), ("writeOps", "VolumeWriteOps"), ("queueLength", "VolumeQueueLength"))
NO_HISTORY = "CloudFix recommendation history is not available to a standalone run."
CE_READ = {"kind": "aws_read", "name": "GetCostAndUsage", "service": "ce", "region": "us-east-1"}
PRICING_READ = {"kind": "aws_read", "name": "GetProducts", "service": "pricing", "region": "us-east-1"}
UNITS = {"storage": ("gb-mo", "gb-month", "gb-months"), "iops": ("iops-mo",), "iops1": ("iops-mo",), "iops2": ("iops-mo",), "iops3": ("iops-mo",), "throughput": ("gibps-mo",)}
# Cost Explorer usage types per rate and volume type (billing-cost-explorer.md); the returned values are the authority.
CE_USAGE_TYPES = {"storage": dict({t: "EBS:VolumeUsage." + t for t in ("gp2", "gp3", "st1", "sc1", "io2")}, io1="EBS:VolumeUsage.piops", standard="EBS:VolumeUsage"),
                  "iops": {"gp3": "EBS:VolumeP-IOPS.gp3", "io1": "EBS:VolumeP-IOPS.piops"}, "iops1": {"io2": "EBS:VolumeP-IOPS.io2"},
                  "iops2": {"io2": "EBS:VolumeP-IOPS.io2.tier2"}, "iops3": {"io2": "EBS:VolumeP-IOPS.io2.tier3"}, "throughput": {"gp3": "EBS:VolumeP-Throughput.gp3"}}
# Public catalog components per volume type (public-price-model.md): productFamily, group, unit.
COMPONENTS = {"storage": ("Storage", None, "GB-Mo"), "iops": ("System Operation", "EBS IOPS", "IOPS-Mo"), "throughput": ("Provisioned Throughput", "EBS Throughput", "GiBps-mo"),
              "iops1": ("System Operation", "EBS IOPS", "IOPS-Mo"), "iops2": ("System Operation", "EBS IOPS Tier 2", "IOPS-Mo"),
              "iops3": ("System Operation", "EBS IOPS Tier 3", "IOPS-Mo"), "io": ("System Operation", "EBS I/O Requests", "IOs")}
COMPONENTS_BY_TYPE = {"gp2": ("storage",), "st1": ("storage",), "sc1": ("storage",), "gp3": ("storage", "iops", "throughput"),
                      "io1": ("storage", "iops"), "io2": ("storage", "iops1", "iops2", "iops3"), "standard": ("storage", "io")}
# The native archive lookup filters on the Pricing API location name as well as regionCode.
PRICING_LOCATIONS = {"us-east-2": "US East (Ohio)", "us-east-1": "US East (N. Virginia)", "us-west-1": "US West (N. California)", "us-west-2": "US West (Oregon)",
                     "af-south-1": "Africa (Cape Town)", "ap-east-1": "Asia Pacific (Hong Kong)", "ap-east-2": "Asia Pacific (Taipei)", "ap-south-1": "Asia Pacific (Mumbai)",
                     "ap-south-2": "Asia Pacific (Hyderabad)", "ap-northeast-3": "Asia Pacific (Osaka)", "ap-northeast-2": "Asia Pacific (Seoul)", "ap-southeast-1": "Asia Pacific (Singapore)",
                     "ap-southeast-2": "Asia Pacific (Sydney)", "ap-southeast-3": "Asia Pacific (Jakarta)", "ap-southeast-4": "Asia Pacific (Melbourne)", "ap-southeast-5": "Asia Pacific (Malaysia)",
                     "ap-southeast-6": "Asia Pacific (New Zealand)", "ap-southeast-7": "Asia Pacific (Thailand)", "ap-northeast-1": "Asia Pacific (Tokyo)", "ca-central-1": "Canada (Central)",
                     "ca-west-1": "Canada West (Calgary)", "cn-north-1": "China (Beijing)", "cn-northwest-1": "China (Ningxia)", "eu-central-1": "EU (Frankfurt)", "eu-central-2": "Europe (Zurich)",
                     "eu-west-1": "EU (Ireland)", "eu-west-2": "EU (London)", "eu-south-1": "EU (Milan)", "eu-south-2": "Europe (Spain)", "eu-west-3": "EU (Paris)", "eu-north-1": "EU (Stockholm)",
                     "il-central-1": "Israel (Tel Aviv)", "me-central-1": "Middle East (UAE)", "me-south-1": "Middle East (Bahrain)", "mx-central-1": "Mexico (Central)",
                     "sa-east-1": "South America (Sao Paulo)", "us-gov-west-1": "AWS GovCloud (US-West)", "us-gov-east-1": "AWS GovCloud (US-East)"}

def add_skill_arguments(parser):
    parser.add_argument("--billing-profile", help="AWS CLI profile for Cost Explorer (default: --profile)")
    parser.add_argument("--allow-public-fallback", action="store_true", help="the customer asked for billed costs if possible, otherwise public prices")
    parser.add_argument("--volume-id", action="append", help="assess this volume (repeatable); default: every available volume in the region")
    parser.add_argument("--volume-age-threshold", type=int, help="days unattached; default 7 (1-365)")
    parser.add_argument("--minimal-savings", type=float, help="annual currency units; default 0")

def describe_change(row):
    d = row["details"]
    return "Snapshot, then delete %s, %s GiB; immediate archival: %s" % (d["volume_type"], d["size_gib"], "yes" if row["recommendation"]["parameters"]["immediate_archival"] else "no")

def skill_revision():
    with open(os.path.join(SCRIPT_DIR, "..", "SKILL.md"), encoding="utf-8") as handle:
        return re.search(r'^  version: "([^"]+)"', handle.read(), re.M).group(1)

def report_headline(assessment, recommended):
    return "**Savings not established.**" if not recommended else "**%d opportunities found; savings not totaled.**" % len(recommended)

def report_context(assessment):
    scope, coverage, run = assessment["scope"], assessment["coverage"], assessment["run"]
    unknown = lambda value: "unknown" if value is None else value
    return ["Account %s · %s · %s · %s discovered, %s assessed, %d unassessed · %s · observed %s · %s." % (
        unknown(scope["account_id"]), ", ".join(scope["regions"]), "selected volumes" if scope["resource_ids"] is not None else "all " + EMPTY_INVENTORY_NOUN,
        unknown(coverage["discovered_count"]), unknown(coverage["assessed_count"]), len(coverage["unassessed_ids"]), run["status"], run["observed_at"],
        {"self_check": "self-check", "independent_review": "independent review", "none": "not verified"}[assessment["verification"]["method"]])]

def report_notes(assessment):
    return []

def report_bullets(assessment):
    """One line per recommended volume: state, age, attachment evidence, snapshots."""
    lookback = iso_js(parse_time(assessment["run"]["observed_at"]) - datetime.timedelta(days=LOOKBACK_DAYS))[:10]
    notes = []
    for row in sorted((r for r in assessment["findings"] if r["decision"] == "recommended"), key=lambda r: -r["costs"]["annual_savings"]):
        checks, d = {c["name"]: c for c in row["checks"]}, row["details"]
        detached, snaps = checks["DetachedXDaysAgo"]["value"], d["snapshots"]
        attachment = ("No attachment metrics in the %d-day lookback, which the finder reads as detached before %s." % (LOOKBACK_DAYS, lookback) if detached["detectionMethod"] == "no_data"
                      else "Last attachment evidence %s, before the %s cutoff." % (detached["lastAttachmentDate"][:10], detached["maxLastAttachmentDate"][:10]))
        snapshots = ("Snapshots unknown: the snapshot read failed." if snaps is None else "No snapshots found." if not snaps else "%d existing snapshots, latest %s." % (len(snaps), snaps[0]["startTime"][:10]))
        notes.append("- %s: %s, available, %s days old. %s %s" % (row["id"], d["volume_type"], checks["VolumeAgeXDays"]["value"]["ageDays"], attachment, snapshots))
    return notes

def nonnegative(value, name):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("Missing or invalid " + name)
    return value

def supplied_annual_cost(row, supplied):
    """The supplied annual cost of one volume when it is a number from 0 to 1e12 in USD, else None."""
    cost = (row or {}).get("annualCost")
    # ponytail: 1e12 USD/year ceiling, above any one volume's cost; it keeps to_fixed and the cent totals exact. Raise it if a real cost ever reaches it.
    usable = type(cost) in (int, float) and 0 <= cost <= 1e12 and (row.get("currency") or supplied.get("currency")) == "USD"  # NaN and infinity fail the comparison
    return (cost or 0) if usable else None  # -0.0 is saved as 0

def performance_charges(volume, rates, charge):
    """The IOPS and throughput terms shared by the billed and public calculators (identical in both references)."""
    kind, iops = volume.get("VolumeType"), volume.get("Iops")
    if kind in ("gp2", "st1", "sc1", "standard"):
        return {}
    if kind == "gp3":
        return {"iops": charge(max(nonnegative(iops, "Iops") - 3000, 0), rates.get("iops"), "IOPS rate"),
                "throughput": charge(max(nonnegative(volume.get("Throughput"), "Throughput") - 125, 0) / 1024, rates.get("throughput"), "GiBps-mo rate")}
    if kind == "io1":
        return {"iops": charge(nonnegative(iops, "Iops"), rates.get("iops"), "IOPS rate")}
    if kind == "io2":
        iops = nonnegative(iops, "Iops")
        return {"iops": charge(min(iops, 32000), rates.get("iops1"), "IOPS band 1") + charge(min(max(iops - 32000, 0), 32000), rates.get("iops2"), "IOPS band 2")
                + charge(max(iops - 64000, 0), rates.get("iops3"), "IOPS band 3")}
    raise ValueError("Unsupported volume type")

def billed_months(create_time, window_start):
    """The months of the 31-day window the volume was billed for: each day's part at or after CreateTime, over the days of that day's month."""
    try:
        created, first = parse_time(create_time), parse_time(window_start + "T00:00:00Z")
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Missing or invalid CreateTime")
    months = 0
    for offset in range(31):
        day = first + datetime.timedelta(days=offset)
        month_days = ((day.replace(day=1) + datetime.timedelta(days=31)).replace(day=1) - day.replace(day=1)).days
        months += min(max((day + datetime.timedelta(days=1) - created).total_seconds() / 86400, 0), 1) / month_days
    return months

def estimate_billed_cost(volume, rates, window_start):
    """cost-explorer-ebs-cost-calculation from billing-cost-explorer.md; raises with the function's own messages."""
    size = nonnegative(volume.get("Size"), "Size")
    if not size > 0:
        raise ValueError("Billed estimate requires positive Size")
    def charge(quantity, rate, name):
        if quantity != 0 and not nonnegative(rate, name) > 0:
            raise ValueError("Zero %s cannot price a volume" % name)
        return 0 if quantity == 0 else quantity * rate
    monthly = dict({"storage": charge(size, rates.get("storage"), "storage rate"), "iops": 0, "throughput": 0}, **performance_charges(volume, rates, charge))
    months = billed_months(volume.get("CreateTime"), window_start)
    # The finder's own annualization of a volume's billed cost over the window: ROUND(sum / 31 * 365, 2).
    return {"monthly": monthly, "monthlyCost": sum(monthly.values()), "billedMonths": months, "annualCost": float(to_fixed(sum(monthly.values()) * months / 31 * 365, 2))}

def estimate_public_cost(volume, rates, annual_io_count=None):
    """public-ebs-cost-calculation from public-price-model.md."""
    size = nonnegative(volume.get("Size"), "Size")
    if not size > 0:
        raise ValueError("Public estimate requires positive Size")
    def charge(quantity, rate, name):
        return 0 if quantity == 0 else quantity * nonnegative(rate, name)
    monthly = charge(size, rates.get("storage"), "storage rate") + sum(performance_charges(volume, rates, charge).values())
    if volume.get("VolumeType") != "standard":
        return {"monthlyPublicCost": monthly, "annualPublicCost": 12 * monthly, "missing": []}
    if annual_io_count is None:
        return {"annualStorageSubtotal": 12 * monthly, "annualPublicCost": None, "missing": ["annual I/O quantity"]}
    operations = charge(nonnegative(annual_io_count, "annual I/O quantity"), rates.get("io"), "price per IOs")
    return {"annualStorageSubtotal": 12 * monthly, "annualOperationEstimate": operations, "annualPublicCost": 12 * monthly + operations, "missing": []}

def estimate_regional_archive(size, rate, annual_cost):
    """regional-ebs-archive-calculation from public-price-model.md; the native formula in costing.md is the same arithmetic."""
    archive = 12 * size * rate
    savings = max(0, annual_cost - archive)
    return {"archiveAnnualEstimate": archive, "annualSavingsEstimate": savings, "newCostEstimate": annual_cost - savings}

def dimensions(records):
    """Every OnDemand price dimension of every PriceList item on every page, with the attributes the selection rules read."""
    for record, position in records:
        for text in record.get("output", {}).get("PriceList") or []:
            item = json.loads(text) if isinstance(text, str) else text
            for code, term in (item.get("terms", {}).get("OnDemand") or {}).items():
                for rate_code, dim in (term.get("priceDimensions") or {}).items():
                    yield dict({key: dim.get(key) for key in ("description", "unit", "beginRange", "endRange", "appliesTo")}, sku=(item.get("product") or {}).get("sku"),
                               family=(item.get("product") or {}).get("productFamily"), attributes=(item.get("product") or {}).get("attributes") or {}, term=code,
                               effectiveDate=term.get("effectiveDate"), rateCode=rate_code, usd=(dim.get("pricePerUnit") or {}).get("USD"), evidence_ref="evidence/pricing.json#/%d" % position)

def select_quote(candidates):
    """One compatible flat USD quote, or a problem: identical repeats collapse, conflicting ones are ambiguous."""
    usable = [c for c in candidates if c["usd"] is not None and c["beginRange"] == "0" and c["endRange"] == "Inf"]
    distinct = {(c["sku"], c["rateCode"], c["usd"]) for c in usable}
    if len(distinct) != 1:
        return None, "no compatible quote" if not distinct else "%d conflicting quotes" % len(distinct)
    rate = float(usable[0]["usd"])
    return (dict(usable[0], rate=rate), None) if math.isfinite(rate) and rate >= 0 else (None, "quote is not a finite nonnegative rate")

def catalog_rates(records, kind):
    """public-price-model.md step 3: the components of one volume type from its catalog pages. Returns (rates, quotes, problems)."""
    rates, quotes, problems = {}, {}, {}
    for component in COMPONENTS_BY_TYPE[kind]:
        family, group, unit = COMPONENTS[component]
        quote, problem = select_quote(c for c in dimensions(records) if c["family"] == family and c["unit"] == unit
                                      and c["attributes"].get("volumeApiName") == kind and (group is None or c["attributes"].get("group") == group))
        (rates.__setitem__(component, quote["rate"]), quotes.__setitem__(component, quote)) if quote else problems.__setitem__(component, problem)
    return rates, quotes, problems

def archive_quote(records):
    """public-price-model.md step 4: the regional archive-storage quote; there is no fallback on this path."""
    return select_quote(c for c in dimensions(records) if c["family"] == "Storage Snapshot" and c["unit"] == "GB-Mo"
                        and re.search(r"(^|-)EBS:SnapshotArchiveStorage$", c["attributes"].get("usagetype") or "")
                        and c["attributes"].get("snapshotarchivefeetype") in (None, "SnapshotArchiveStorage"))

def native_archive_rate(evidence, region, profile):
    """costing.md: one unpaged native lookup; undefined or thrown gives the 0.0125 fallback, a thrown one with its error and request.
    Returns (archive, evidence ref), or (None, the request) when replay holds no such read."""
    fields = [("productFamily", "Storage Snapshot"), ("regionCode", region), ("storageMedia", "Amazon S3"), ("usagetype", "EBS:SnapshotArchiveStorage"), ("location", PRICING_LOCATIONS[region])]
    params = {"ServiceCode": "AmazonEC2", "Filters": [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in fields], "MaxResults": 1}
    record, position = evidence.read("pricing", "pricing", "GetProducts", params, "us-east-1", profile)
    if record is None:
        return None, dict(PRICING_READ, parameters=params)
    # MaxResults 1: the first PriceList string holding the unit is the only one, so the first GB-Mo USD dimension outside the free tier is the native pick.
    quote = next((c for c in dimensions([(record, position)]) if c["unit"] == "GB-Mo" and c["usd"] is not None and "(free tier)" not in (c["description"] or "")), None)
    return (dict(rate=float(quote["usd"]), source="native_lookup", selected={"sku": quote["sku"], "dimension": quote["rateCode"]}) if quote
            else dict({"rate": ARCHIVE_FALLBACK, "source": "fallback"}, **({"error": record["error"], "request": dict(PRICING_READ, parameters=params)} if "error" in record else {}))), evidence.ref("pricing", position)

def collect_cost_explorer(evidence, account, region, as_of, profile, kinds):
    """billing-cost-explorer.md: effective rates per volume type for one region. Returns (result, failure)."""
    midnight = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = (midnight - datetime.timedelta(days=32)).date().isoformat(), (midnight - datetime.timedelta(days=1)).date().isoformat()
    common = [{"Dimensions": {"Key": key, "Values": [value]}} for key, value in (("LINKED_ACCOUNT", account), ("REGION", region), ("SERVICE", "EC2 - Other"), ("RECORD_TYPE", "Usage"))]
    identity, position = evidence.read("cost-explorer", "sts", "GetCallerIdentity", {}, "us-east-1", profile)
    if identity is None or "error" in identity:
        return None, {"kind": "read_error" if identity else "missing_evidence", "message": "The billing identity could not be verified.", "error": (identity or {}).get("error"),
                      "ref": evidence.ref("cost-explorer", position) if identity else None, "request": None if identity else position}
    def paged(operation, params):
        records, failure = evidence.pages("cost-explorer", "ce", operation, params, "us-east-1", profile, token="NextPageToken")
        return records, failure and dict(kind="read_error" if "error" in failure else "missing_evidence", message=operation + " did not complete.",
                                         request=failure.get("missing") or dict(CE_READ, name=operation, parameters=records[-1][0]["input"]), **failure)  # the failed page
    params = {"TimePeriod": {"Start": start, "End": end}, "Dimension": "USAGE_TYPE", "Context": "COST_AND_USAGE", "SearchString": "EBS:Volume", "Filter": {"And": common}}
    records, failure = paged("GetDimensionValues", params)
    if failure:
        return None, failure
    matched, unresolved, attribute_units = {}, {}, {}
    for value in (v for record, _ in records for v in record["output"].get("DimensionValues") or []):
        name = value.get("Value")
        if not isinstance(name, str):
            return None, {"kind": "read_error", "message": "GetDimensionValues returned a malformed value."}
        rows = [(rate, kind) for rate, names in CE_USAGE_TYPES.items() for kind, expected in names.items() if name == expected or name.endswith("-" + expected)]
        attribute_units[name] = (value.get("Attributes") or {}).get("unit")
        for row in rows:
            matched.setdefault(row, []).append(name)
        for kind in [] if rows else kinds:  # names a priced type but fits no row: that type's rate stays unresolved
            if "EBS:Volume" in name and "." + ("piops" if kind == "io1" else kind) in name:
                unresolved[(kind, "storage")] = "returned usage type %s fits no known charge" % name
    needed = sorted({name for (rate, kind), names in matched.items() if kind in kinds for name in names})
    if not needed:
        return None, {"kind": "missing_evidence", "message": "Cost Explorer returned no EBS volume usage type for %s in %s to %s; this view may not cover the linked account." % (", ".join(kinds), start, end),
                      "request": {"kind": "aws_read", "name": "GetDimensionValues", "service": "ce", "region": "us-east-1", "parameters": params}}
    params = {"TimePeriod": {"Start": start, "End": end}, "Granularity": "DAILY", "Metrics": ["NetUnblendedCost", "UnblendedCost", "UsageQuantity"],
              "Filter": {"And": common + [{"Dimensions": {"Key": "USAGE_TYPE", "Values": needed}}]}, "GroupBy": [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}]}
    records, failure = paged("GetCostAndUsage", params)
    if failure:
        return None, failure
    sums, days, estimated = {}, set(), 0
    for day in (d for record, _ in records for d in record["output"].get("ResultsByTime") or []):
        window, seen = day.get("TimePeriod") or {}, set()
        if not isinstance(day.get("Estimated"), bool) or window.get("Start") in days or not (start <= window.get("Start", "") < end):
            return None, {"kind": "read_error", "message": "GetCostAndUsage returned a malformed or overlapping daily period."}
        days.add(window["Start"])
        estimated += day["Estimated"]
        for group in day.get("Groups") or []:
            name, metrics = (group.get("Keys") or [None])[0], group.get("Metrics") or {}
            if name not in needed or name in seen:
                return None, {"kind": "read_error", "message": "GetCostAndUsage returned a group that was not requested, or twice in one day."}
            seen.add(name)
            try:
                net, gross, quantity = metrics.get("NetUnblendedCost"), metrics["UnblendedCost"], metrics["UsageQuantity"]
                if net is not None and (not isinstance(net, dict) or "Amount" not in net):  # empty or malformed is an error, not a fallback
                    raise ValueError("net cost")
                net = net or {}
                if gross.get("Unit") != "USD" or (net.get("Amount") is not None and net.get("Unit") != "USD"):
                    raise ValueError("cost unit")
                day_gross, day_quantity = float(gross["Amount"]), float(quantity["Amount"])
                day_selected = day_gross if net.get("Amount") is None else float(net["Amount"])  # net first; unblended only when net is absent
                if not all(math.isfinite(v) for v in (day_gross, day_quantity, day_selected)) or day_quantity < 0 or (day_quantity == 0 and (day_selected or day_gross)):
                    raise ValueError("amount")
            except (KeyError, TypeError, ValueError, AttributeError) as fault:
                return None, {"kind": "read_error", "message": "GetCostAndUsage returned invalid data for %s (%s)." % (name, fault)}
            total = sums.setdefault(name, {"selected_cost": 0.0, "unblended_cost": 0.0, "usage": 0.0, "units": set(), "net_fallback_days": 0})
            total["selected_cost"], total["unblended_cost"], total["usage"] = total["selected_cost"] + day_selected, total["unblended_cost"] + day_gross, total["usage"] + day_quantity
            total["net_fallback_days"] += net.get("Amount") is None
            total["units"].add(quantity.get("Unit", "N/A"))
    rates, provenance = {}, {}
    for (rate, kind), names in matched.items():
        if kind not in kinds or (kind, rate) in unresolved:
            continue
        seen_units = {u.lower() for name in names for u in [attribute_units.get(name)] + list(sums.get(name, {}).get("units", ())) if u and u.lower() != "n/a"}
        unverified = any(attribute_units.get(name) is None and "N/A" in sums.get(name, {}).get("units", ()) for name in names)
        usage, cost = (sum(sums.get(name, {}).get(key, 0.0) for name in names) for key in ("usage", "selected_cost"))
        if len(seen_units) != 1 or next(iter(seen_units)) not in UNITS[rate] or unverified:
            unresolved[(kind, rate)] = "usage type %s has no verifiable %s unit (%s)" % (", ".join(names), rate, ", ".join(sorted(seen_units)) or "none")
        elif not (usage > 0 and math.isfinite(cost) and cost > 0):
            unresolved[(kind, rate)] = "usage type %s shows %s in %s to %s" % (", ".join(names), "no usage" if usage <= 0 else "zero cost over positive usage", start, end)
        else:
            rates.setdefault(kind, {})[rate] = cost / usage
            provenance.setdefault(kind, {})[rate] = {"usage_types": names, "unit": next(iter(seen_units)), "cost_metric": "NetUnblendedCost, UnblendedCost on %d usage-type days" % sum(sums[n]["net_fallback_days"] for n in names if n in sums),
                                                     "selected_cost": cost, "unblended_cost": sum(sums[n]["unblended_cost"] for n in names if n in sums), "usage": usage, "rate": cost / usage,
                                                     "days_returned": len(days), "days_requested": 31, "estimated_days": estimated, "blended": len(names) > 1}
    return {"rates": rates, "provenance": provenance, "unresolved": {"%s %s" % key: reason for key, reason in unresolved.items()}, "window": {"start": start + "T00:00:00Z", "end": end + "T00:00:00Z"},
            "days_returned": len(days), "estimated_days": estimated, "billing_account": identity["output"].get("Account"), "request": dict(CE_READ, parameters=params),
            "refs": ["evidence/cost-explorer.json#/%d" % i for i in range(len(evidence.files.get("cost-explorer") or []))]}, None

def collect_metrics(evidence, volume_ids, region, as_of, profile):
    """collection.md: three AWS/EBS series per included volume, 500 queries per call, every page. Returns ({volume id: outcome}, {call number: issue}).
    assessment-guide.md: a call that failed, lacks a page or left a query unanswered is one run-level issue with the exact request to repeat; its rows name the call."""
    end = as_of.replace(hour=23, minute=59, second=59, microsecond=999000)
    start = (end - datetime.timedelta(days=LOOKBACK_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    outcomes, call_issues, per_call = {}, {}, QUERY_LIMIT // len(METRICS)
    for first in range(0, len(volume_ids), per_call):
        chunk = volume_ids[first:first + per_call]
        queries = [{"Id": "%s_%s" % (prefix, vid.replace("-", "_")), "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": metric, "Dimensions": [{"Name": "VolumeId", "Value": vid}]},
                    "Period": 86400, "Stat": "Sum"}, "ReturnData": True} for vid in chunk for prefix, metric in METRICS]
        params = {"MetricDataQueries": queries, "StartTime": iso_js(start), "EndTime": iso_js(end), "ScanBy": "TimestampDescending"}
        records, failure = evidence.pages("get-metric-data", "cloudwatch", "GetMetricData", params, region, profile)
        refs = [evidence.ref("get-metric-data", position, "/output/MetricDataResults" if "output" in record else "") for record, position in records]
        fault, call = None, first // per_call + 1
        absent = bool(failure) and "missing" in failure
        repeat = records[-1][0]["input"] if failure and not absent else params  # the failed page, else the whole read
        for record, result in ((record, r) for record, _ in records for r in record.get("output", {}).get("MetricDataResults") or []):
            if result.get("StatusCode") in ("InternalError", "Forbidden") and not fault:
                fault, repeat = "GetMetricData returned %s for query %s." % (result["StatusCode"], result.get("Id")), record["input"]
        # The finder's mergePagesBySeries: a series continues on later pages under its Id and Label, but an Id and Label
        # that repeats on one page is several series, so every result under it stays apart, as does one without an Id.
        pages, key = [record.get("output", {}).get("MetricDataResults") or [] for record, _ in records], lambda result: json.dumps([result.get("Id"), result.get("Label")])
        shared, joined = set(), {}
        for page in pages:
            seen = set()
            shared.update(name for name in map(key, page) if name in seen or seen.add(name))
        for number, result in enumerate(result for page in pages for result in page):
            joined.setdefault(number if result.get("Id") is None or key(result) in shared else key(result), []).append(result)
        series = [(parts[0]["Id"], [stamp for part in parts for stamp in part.get("Timestamps") or []]) for parts in joined.values()
                  if parts[0].get("Id") is not None and (len(parts) > 1 or None not in (parts[0].get("Values"), parts[0].get("Timestamps")))]
        for vid in chunk:
            firsts = [stamps[0] for sid, stamps in series if sid.split("_", 1)[-1].replace("_", "-") == vid and stamps]
            try:
                latest, bad = max((parse_time(stamp) for stamp in firsts), default=None), None
            except (ValueError, TypeError, AttributeError):  # checks.md: keep the original evidence, never coerce it to a date
                latest, bad = None, "GetMetricData returned a timestamp that is not a date for %s." % vid
            own = {"%s_%s" % (prefix, vid.replace("-", "_")) for prefix, _ in METRICS}
            unanswered = own - {sid for sid, _ in series}  # collection.md: not the finder's query
            error = None if absent else fault or bad or (failure and "GetMetricData failed: %s: %s" % (failure["error"]["code"], failure["error"]["message"]))
            outcomes[vid] = ({"method": "error", "error": error, "refs": refs, "call": call} if error else
                             {"method": None, "refs": refs, "call": call} if absent or unanswered else
                             {"method": "cloudwatch" if latest else "no_data", "date": iso_js(latest) if latest else None, "refs": refs})
        methods = {outcomes[vid]["method"] for vid in chunk}
        if methods & {None, "error"}:
            reason = ((fault + " " if fault else "") + "A page of the read is missing." if absent else fault or ("The call failed: %s: %s." % (failure["error"]["code"], failure["error"]["message"]) if failure else
                                                                             "A returned timestamp is not a date." if "error" in methods else "The response does not answer every query."))
            call_issues[call] = dict({"kind": "read_error" if "error" in methods else "missing_evidence", "stage": "collection",
                                      "message": "GetMetricData call %d (%d volumes, %s to %s): %s This issue's request is the exact read to repeat." % (call, len(chunk), chunk[0], chunk[-1], reason),
                                      "request": failure["missing"] if absent else {"kind": "aws_read", "name": "GetMetricData", "service": "cloudwatch", "region": region, "parameters": repeat}},
                                     **({"evidence_refs": refs} if refs else {}))
    return outcomes, call_issues

def normalize_snapshots(records):
    """costing.md: records with every required field, grouped by volume, newest first."""
    grouped = {}
    for snap in (s for record, _ in records for s in record["output"].get("Snapshots") or []):
        if all(snap.get(key) is not None for key in ("VolumeId", "SnapshotId", "StartTime", "StorageTier", "State")):
            grouped.setdefault(snap["VolumeId"], []).append({"snapshotId": snap["SnapshotId"], "startTime": iso_js(snap["StartTime"]), "state": snap["State"],
                                                              "volumeSize": snap.get("VolumeSize", 0), "description": snap.get("Description", "")})
    return {vid: sorted(snaps, key=lambda s: s["startTime"], reverse=True) for vid, snaps in grouped.items()}

def batched(evidence, file, operation, ids, region, profile, extra=None):
    """DescribeVolumes or DescribeSnapshots for explicit IDs: 200 per request, every page. Returns (records, failure)."""
    all_records, failure = [], None
    for start in range(0, len(ids), BATCH):
        records, failure = evidence.pages(file, "ec2", operation, dict({"Filters": [{"Name": "volume-id", "Values": ids[start:start + BATCH]}]}, **(extra or {})), region, profile)
        all_records += records
        if failure:
            break
    return all_records, failure

def parse_volumes(records):
    """The source's map: VolumeId, VolumeType and State present, last duplicate wins, raw position kept. Returns (map, dropped count)."""
    complete = lambda volume: isinstance(volume, dict) and all(volume.get(key) is not None for key in ("VolumeId", "VolumeType", "State"))
    kept = {volume["VolumeId"]: (volume, position, index) for record, position in records for index, volume in enumerate(record["output"].get("Volumes") or []) if complete(volume)}
    return kept, sum(not complete(volume) for record, _ in records for volume in record["output"].get("Volumes") or [])

def early_checks(volume_id, volumes, as_of, complete=True):
    """Rules 1-5 (checks.md). Returns (rows, stopped); each row is (name, approved, value, reason)."""
    rows = [("RecommendationFinalized", None, None, NO_HISTORY)]
    if volume_id not in volumes:  # an incomplete read does not decide absence
        return rows + ([("ResourceNotDeleted", False, {"resourceId": volume_id}, "The completed volume read did not return this volume.")] if complete else []), True
    volume = volumes[volume_id][0]
    state, kind, create_time = volume["State"], volume["VolumeType"], volume.get("CreateTime")
    rows += [("ResourceNotDeleted", True, {"resourceId": volume_id}, None),
             ("IsValidState", state == "available", {"volumeState": state, "requiredState": "available"}, "The volume is %s, not available." % state),
             ("VolumeExpectedType", kind in EXPECTED_TYPES, {"volumeType": kind, "expectedTypes": EXPECTED_TYPES}, "The volume type %s is not supported." % kind)]
    if not all(row[1] for row in rows[1:]):
        return rows[:[row[1] for row in rows].index(False) + 1], True
    try:
        age_days = None if create_time is None else int((as_of - parse_time(create_time)).total_seconds() * 1000 / 86400000)  # Math.trunc of whole days
    except (ValueError, TypeError, AttributeError):  # the finder's NaN age fails the comparison; the value keeps the original text
        return rows + [("VolumeAgeXDays", False, {"createTime": create_time, "ageDays": None, "minAgeDays": MIN_AGE_DAYS}, "The volume's creation time is not a valid date.")], False
    rows.append(("VolumeAgeXDays", False, {"minAgeDays": MIN_AGE_DAYS, "reason": "create-time-unreported"}, "The volume reports no creation time.") if age_days is None else
                ("VolumeAgeXDays", age_days >= MIN_AGE_DAYS, {"createTime": iso_js(create_time), "ageDays": age_days, "minAgeDays": MIN_AGE_DAYS}, "%d days old; the minimum is %d days." % (age_days, MIN_AGE_DAYS)))
    return rows, False

def check_rows(rows, tail):
    """Rows for assessment.json: pass, fail with reason, or not_evaluated; everything after a fail-fast stop is not evaluated."""
    saved, stop = [], None
    for name, approved, value, reason in rows:
        if stop or approved is None:
            saved.append(dict({"name": name, "status": "not_evaluated", "reason": stop or reason}, **({"value": value} if value and not stop else {})))
        else:
            saved.append(dict({"name": name, "status": "pass" if approved else "fail", "value": value}, **({"reason": reason} if reason and (not approved or reason.startswith("model_checks")) else {})))
            stop = "Not run after the earlier rejection." if not approved and name in FAIL_FAST else stop
    return saved + [{"name": name, "status": "not_evaluated", "reason": stop or tail} for name in CHECK_NAMES[len(saved):]]

def run(args):
    issues, qualifications, calculations = [], [], {}
    if args.replay:
        evidence = Evidence(args.replay, True)
        saved = evidence.files.get("run-metadata") if isinstance(evidence.files.get("run-metadata"), dict) else {}
        metadata, request = saved.get("run_metadata"), saved.get("request")
        if not metadata or not request:
            raise InputError("The replay folder needs a run-metadata.json holding {\"run_metadata\", \"request\"}; references/guided.md gives its layout.")
        evidence.directory = os.path.join(args.out, "evidence")
    else:
        evidence = Evidence(os.path.join(args.out, "evidence"), False)
        configuration = dict(DEFAULT_CONFIGURATION, **{key: value for key, value in (("volumeAgeThreshold", args.volume_age_threshold), ("minimalSavings", args.minimal_savings)) if value is not None})
        metadata = {"finderId": FINDER_ID, "skill_revision": None, "accountId": args.account, "region": args.region, "asOf": iso_js(parse_time(args.as_of) if args.as_of else datetime.datetime.now(datetime.timezone.utc)),
                    "evaluation_timezone": "UTC", "configuration": configuration, "configuration_overrides": {key: value for key, value in configuration.items() if value != DEFAULT_CONFIGURATION[key]},
                    "run_classification": "standard", "experiment": None, "public_price_fallback_allowed": bool(args.allow_public_fallback)}
        request = {"pricing_mode": args.pricing_mode, "resource_ids": args.volume_id, "allow_public_fallback": bool(args.allow_public_fallback), "profile": args.profile, "billing_profile": args.billing_profile}
        evidence.files["run-metadata"] = {"run_metadata": metadata, "request": request}
        if args.pricing_mode == "supplied" and not args.costs:
            sys.stderr.write("--pricing-mode supplied needs --costs FILE\n")
            return 1
        if args.pricing_mode == "supplied":
            try:
                with open(args.costs, encoding="utf-8") as handle:
                    evidence.files["supplied-costs"] = json.load(handle)
            except (OSError, ValueError) as fault:
                raise InputError("The --costs file could not be read as JSON: %s" % fault)
    try:
        as_of, account, region, configuration = parse_time(metadata["asOf"]), metadata["accountId"], metadata["region"], metadata["configuration"]
        pricing_mode, selection, profile = request["pricing_mode"], request.get("resource_ids"), request.get("profile")
        selection = None if selection is None else list(dict.fromkeys(selection))  # a volume named twice is one volume
    except (KeyError, TypeError, ValueError, AttributeError) as fault:
        raise InputError("run-metadata.json lacks a usable asOf, accountId, region, configuration or pricing_mode: %r" % fault)
    if pricing_mode not in PRICING_MODES or not isinstance(configuration, dict):
        raise InputError("run-metadata.json needs a configuration object and a pricing_mode of %s" % ", ".join(PRICING_MODES))
    assessment = {
        "schema_version": "1.0", "skill": {"id": SKILL_ID, "revision": skill_revision(), "source_ref": None},
        "run": {"observed_at": metadata["asOf"], "mode": "local_code", "status": "complete"},
        "scope": {"account_id": None, "regions": [region], "resource_ids": selection, "pricing_mode": pricing_mode},
        "coverage": {"inventory_complete": False, "discovered_count": None, "assessed_count": None, "unassessed_ids": []},
        "run_metadata": metadata, "findings": [], "totals": {"decision_counts": {"recommended": 0, "not_recommended": 0, "needs_evidence": 0, "error": 0}, "annual_savings": None},
        "issues": issues, "qualifications": qualifications, "verification": {"method": "self_check", "summary": "The run stopped before analysis; the saved counts and evidence references were self-checked."},
        "evidence_refs": ["evidence/run-metadata.json", "evidence/get-caller-identity.json#/0"], "execution_status": "not_executed"}

    def close():  # run-wide reads that were actually recorded
        assessment["evidence_refs"] += ["evidence/%s.json" % name for name in ("describe-volumes", "describe-snapshots", "pricing", "cost-explorer") if evidence.files.get(name)]
        return finish(args.out, evidence, assessment)
    def failed(stage, message, failure=None):
        issue = {"kind": "read_error", "stage": stage, "message": message}
        if failure and failure.get("error"):
            issue["message"] += " %s: %s" % (failure["error"]["code"], failure["error"]["message"])
            issue["evidence_refs"] = [failure["ref"]]
        if failure and (failure.get("missing") or failure.get("input")):
            issue["kind"], issue["request"] = "missing_evidence", failure.get("missing") or {"kind": "input", "name": failure["input"]}
        issues.append(issue)
        assessment["run"]["status"] = "failed"
        return close()
    threshold, minimal = configuration.get("volumeAgeThreshold"), configuration.get("minimalSavings")
    if type(threshold) is not int or not 1 <= threshold <= 365:
        return failed("scope", "volumeAgeThreshold must be an integer from 1 to 365.", {"input": "configuration.volumeAgeThreshold"})
    if type(minimal) not in (int, float) or not math.isfinite(minimal) or minimal < 0:
        return failed("scope", "minimalSavings must be a finite nonnegative amount.", {"input": "configuration.minimalSavings"})
    supplied = evidence.files.get("supplied-costs") if pricing_mode == "supplied" else None
    if pricing_mode == "supplied" and not (isinstance(supplied, dict) and isinstance(supplied.get("resources"), list) and all(isinstance(row, dict) for row in supplied["resources"])):
        return failed("scope", "The supplied cost file needs a resources list with resourceId and annualCost.", {"input": "supplied costs"})
    # 1. identity
    identity, position = evidence.read("get-caller-identity", "sts", "GetCallerIdentity", {}, region, profile)
    if identity is None:  # replay holds no identity read: no record to point at
        assessment["evidence_refs"].remove("evidence/get-caller-identity.json#/0")
    if identity is None or "error" in identity:
        return failed("scope", "Identity could not be verified.", {"error": identity["error"], "ref": evidence.ref("get-caller-identity", position)} if identity else {"missing": position})
    if identity["output"].get("Account") != account:
        return failed("scope", "The credentials belong to account %s, not the requested %s." % (identity["output"].get("Account"), account))
    assessment["scope"]["account_id"] = account
    # 2. inventory: every available volume, or the selected IDs
    if selection is None:
        records, failure = evidence.pages("describe-volumes", "ec2", "DescribeVolumes", {"Filters": [{"Name": "status", "Values": ["available"]}]}, region, profile)
    else:
        records, failure = batched(evidence, "describe-volumes", "DescribeVolumes", selection, region, profile)
    malformed = [position for record, position in records if "output" in record and not isinstance(record["output"].get("Volumes"), list)]
    if malformed:  # an answer without a Volumes list is a failed read, not an empty inventory
        failure = {"error": {"code": "MalformedResponse", "message": "DescribeVolumes returned no Volumes list."}, "ref": evidence.ref("describe-volumes", malformed[0])}
    if failure and "error" in failure:
        for volume_id in selection or []:  # a denied batch leaves every rule unevaluated: the finder's fresh error row, not a verdict
            assessment["findings"].append({"id": volume_id, "resource": {"id": volume_id, "type": RESOURCE_TYPE, "region": region}, "decision": "error",
                                           "checks": check_rows([], "DescribeVolumes was denied or failed for the batch."), "costs": {"status": "not_calculated"}})
        assessment["totals"]["decision_counts"]["error"] = len(assessment["findings"])
        assessment["coverage"].update(discovered_count=None if selection is None else len(selection), assessed_count=0, unassessed_ids=list(selection or []))
        return failed("collection", "The volume inventory was denied or failed.", failure)
    volumes, dropped = parse_volumes(records)
    ids, inventory_complete, missing_page = (selection if selection is not None else list(volumes)), failure is None, (failure or {}).get("missing")
    if failure:
        issues.append({"kind": "missing_evidence", "stage": "collection", "message": "A page of the volume read is missing.", "request": failure["missing"]})
    if dropped:
        qualifications.append("%d returned volume objects lacked VolumeId, VolumeType or State and were not parsed; a parsing omission, not a deletion." % dropped)
    if not ids:
        assessment["coverage"] = {"inventory_complete": inventory_complete, "discovered_count": 0, "assessed_count": 0, "unassessed_ids": []}
        assessment["run"]["status"] = "complete" if inventory_complete and not dropped else "partial"
        if dropped:
            issues.append({"kind": "read_error", "stage": "collection", "message": "No volume was assessed: all %d returned volume objects lacked VolumeId, VolumeType or State." % dropped,
                           "request": {"kind": "aws_read", "name": "DescribeVolumes", "service": "ec2", "region": region, "parameters": records[0][0]["input"]}})
        qualifications.extend([] if dropped and inventory_complete else ["A completed inventory of available volumes in %s returned no volumes." % region if inventory_complete else
                              "The inventory of available volumes in %s was not completed: the pages read returned no volumes and one is still to be read. Next read: ec2 DescribeVolumes %s." % (region, json.dumps(missing_page["parameters"]))])
        return close()
    # snapshots for the original batch IDs; a failure never changes a decision
    records, failure = batched(evidence, "describe-snapshots", "DescribeSnapshots", ids, region, profile, {"OwnerIds": ["self"]})
    snapshots = None if failure else normalize_snapshots(records)
    if failure:
        issues.append(dict({"kind": "read_error" if "error" in failure else "missing_evidence", "stage": "collection", "request": failure.get("missing") or {"kind": "aws_read", "name": "DescribeSnapshots", "service": "ec2", "region": region, "parameters": records[-1][0]["input"]},
                            "message": "The snapshot read %s; existing snapshots are unknown." % ("failed: %s: %s" % (failure["error"]["code"], failure["error"]["message"]) if "error" in failure else "is incomplete")},
                           **({"evidence_refs": [failure["ref"]]} if failure.get("ref") else {})))
    # 3. rules 1-5, then the price reads for the types still being assessed
    early = {vid: early_checks(vid, volumes, as_of, inventory_complete) for vid in ids}
    open_ids = [vid for vid in ids if not early[vid][1]]
    kinds = sorted({volumes[vid][0]["VolumeType"] for vid in open_ids})
    billing = billing_failure = archive = archive_ref = regional = regional_problem = None
    catalog, catalog_refs, catalog_reads = {}, {}, {}
    if open_ids and pricing_mode == "billing":
        billing, billing_failure = collect_cost_explorer(evidence, account, region, as_of, request.get("billing_profile") or profile, kinds)
        for vid in open_ids if billing and request.get("allow_public_fallback") else []:
            kind = volumes[vid][0]["VolumeType"]
            try:
                if all(row[1] is not False for row in early[vid][0]):  # rules 1-5 rejected it: never priced, so it cannot move the run. ponytail: a rule 6 rejection still can; the metrics are read after this
                    estimate_billed_cost(volumes[vid][0], billing["rates"].get(kind, {}), billing["window"]["start"][:10])
            except ValueError as fault:
                if str(fault).split()[-1] in ("Size", "Iops", "Throughput"):  # a field the row itself asks for, not a missing rate
                    continue
                reasons = "; ".join(reason for key, reason in billing["unresolved"].items() if key.startswith(kind + " "))
                billing_failure, billing = {"kind": "missing_evidence", "message": "%s could not be priced (%s): %s." % (vid, fault, reasons or "no usage type resolved")}, None
                break
        if billing_failure and request.get("allow_public_fallback"):
            pricing_mode = assessment["scope"]["pricing_mode"] = "public_prices"
            reason = "Billed costs were requested first. Cost Explorer could not produce a rate because %s As the request allowed, every amount is an AWS public price, not a billed cost." % (
                billing_failure["message"] + (" %s: %s" % (billing_failure["error"]["code"], billing_failure["error"]["message"]) if billing_failure.get("error") else ""))
            qualifications.append(reason)
            issues.append(dict({"kind": "qualification", "stage": "costing", "message": reason, "evidence_refs": [billing_failure["ref"]] if billing_failure.get("ref") else ["evidence/cost-explorer.json"] if evidence.files.get("cost-explorer") else []},
                               **({"request": billing_failure["request"]} if billing_failure.get("request") else {})))
            billing_failure = None
    if open_ids and pricing_mode in ("billing", "supplied"):
        # No location name: the finder's toPricingLocation throws before any Pricing call and populateSavings keeps the fallback. The ref is where the region is recorded.
        archive, archive_ref = (native_archive_rate(evidence, region, profile) if region in PRICING_LOCATIONS
                                else (dict(rate=ARCHIVE_FALLBACK, source="fallback"), "evidence/run-metadata.json#/run_metadata/region"))
    if open_ids and pricing_mode == "public_prices":
        for kind in kinds + ["archive"]:
            filters = [("volumeApiName", kind)] if kind != "archive" else [("productFamily", "Storage Snapshot"), ("storageMedia", "Amazon S3")]
            first = dict(PRICING_READ, parameters={"ServiceCode": "AmazonEC2", "FormatVersion": "aws_v1", "MaxResults": 100, "Filters": [
                {"Type": "TERM_MATCH", "Field": field, "Value": value} for field, value in [("regionCode", region), ("locationType", "AWS Region")] + filters]})
            records, failure = evidence.pages("pricing", "pricing", "GetProducts", first["parameters"], "us-east-1", profile)
            catalog_reads[kind] = (failure or {}).get("missing") or first  # the page still to read, or the read to repeat
            catalog_refs[kind] = [evidence.ref("pricing", position) for _, position in records]
            catalog[kind] = (None, failure) if failure else (archive_quote(records) if kind == "archive" else catalog_rates(records, kind))
        regional, regional_problem = catalog.pop("archive")
    # metrics for every volume rules 2-4 did not stop (their state is available), then rule 6 and the costing
    (metrics, metric_issues), failed_calls = collect_metrics(evidence, open_ids, region, as_of, profile) if open_ids else ({}, {}), set()
    cutoff, basis = as_of - datetime.timedelta(days=threshold), BASIS_BY_MODE[pricing_mode]
    shared_costs = dict({"basis": basis, "estimation_method": "model_estimate", "currency": "USD"}, **({"source_period": billing["window"]} if billing else {}))
    listed = [row.get("resourceId") for row in (supplied or {}).get("resources", [])]
    supplied_rows = {row.get("resourceId"): row for row in (supplied or {}).get("resources", [])
                     if isinstance(row.get("resourceId"), str) and listed.count(row.get("resourceId")) == 1}  # a volume listed twice has no one cost; a non-text id names no volume
    fallback_volumes, findings = [], []
    fallback_cause = ("no AWS Pricing lookup was made, as %s has no Pricing location name" % region if region not in PRICING_LOCATIONS else
                      "the AWS Pricing lookup failed (%s)" % archive["error"]["code"] if (archive or {}).get("error") else "the AWS Pricing lookup returned no archive SKU")
    if (archive or {}).get("error"):  # assessment-guide.md: not an error, the fallback applies; costing.md: preserve the failure
        issues.append({"kind": "qualification", "stage": "costing", "request": archive["request"], "evidence_refs": [archive_ref],
                       "message": "The archive price lookup failed: %s: %s. The finder's %s USD/GB-month fallback applies." % (archive["error"]["code"], archive["error"]["message"], js_number(ARCHIVE_FALLBACK))})
    for volume_id in ids:
        rows, stopped = early[volume_id]
        volume = volumes[volume_id][0] if volume_id in volumes else {}
        kind, size = volume.get("VolumeType"), volume.get("Size")
        details = dict({"volume_type": kind, "size_gib": None if isinstance(size, float) and not math.isfinite(size) else size} if volume else {}, framework_context_complete=False)
        zone = volume.get("AvailabilityZone")  # public-price-model.md step 1: the catalog prices ordinary regional zones only
        placement = (volume.get("OutpostArn") or not re.fullmatch(re.escape(region) + "[a-z]", zone or "")) and (
            "needs_evidence", "Public prices cover the regional Availability Zones of %s only; this volume is in %s%s." % (region, zone or "an unreported zone", ", on an Outpost" if volume.get("OutpostArn") else ""),
            {"kind": "input", "name": "a supplied or billed annual cost for " + volume_id})
        calc, refs, extra_refs, row_quals, problem = {}, [], [], [], None  # problem: (decision, message, request)
        annual_cost = archive_rate = archive_source = result = None
        if volume_id in volumes:
            refs.append(evidence.ref("describe-volumes", volumes[volume_id][1], "/output/Volumes/%d" % volumes[volume_id][2]))
        elif not inventory_complete:
            problem = ("needs_evidence", "The volume read is incomplete, so whether this volume exists is not decided.", missing_page)
        if not stopped:
            outcome = metrics[volume_id]
            refs += outcome["refs"]
            value = {"detectionMethod": outcome["method"], "maxLastAttachmentDate": iso_js(cutoff)}
            if outcome["method"] == "cloudwatch":
                value = {"detectionMethod": "cloudwatch", "lastAttachmentDate": outcome["date"], "maxLastAttachmentDate": iso_js(cutoff)}
                rows.append(("DetachedXDaysAgo", parse_time(outcome["date"]) < cutoff, value, "Last attachment evidence on %s is not before the %s cutoff (%d days)." % (outcome["date"][:10], iso_js(cutoff)[:10], threshold)))
            elif outcome["method"] == "no_data":
                rows.append(("DetachedXDaysAgo", True, value, None))
            elif outcome["method"] == "error":
                rows.append(("DetachedXDaysAgo", None, value, "%s Attachment history is unknown. The finder itself records this as a failed row." % outcome["error"]))
                problem = ("error", "%s The request to repeat is in the run-level issue for GetMetricData call %d." % (outcome["error"], outcome["call"]), None)
            else:
                rows.append(("DetachedXDaysAgo", None, None, "The GetMetricData response for this volume was not supplied."))
                problem = ("needs_evidence", "The GetMetricData response for this volume was not supplied. The request to supply is in the run-level issue for GetMetricData call %d." % outcome["call"], None)
        priced = not stopped and rows[-1][1] is True  # rules 2-4 and 6 passed; an age rejection still gets the savings rows
        if priced and pricing_mode == "billing" and billing_failure:
            problem = ("error" if billing_failure["kind"] == "read_error" else "needs_evidence", "Billing unavailable: " + billing_failure["message"], billing_failure.get("request"))
        elif priced and pricing_mode == "billing":
            try:
                billed = estimate_billed_cost(volume, billing["rates"].get(kind, {}), billing["window"]["start"][:10])
                annual_cost, extra_refs = billed["annualCost"], billing["refs"]
                calc["cost_explorer_cost"] = {"input": {k: volume[k] for k in ("VolumeType", "Size", "Iops", "Throughput", "CreateTime") if k in volume}, "window_start": billing["window"]["start"][:10], "rates": billing["rates"].get(kind, {}), "result": billed}
                details.update(effective_rates=billing["provenance"].get(kind, {}), cost_components=billed["monthly"])
            except ValueError as fault:
                reasons, field = "; ".join(reason for key, reason in billing["unresolved"].items() if key.startswith(kind + " ")) or "no matching usage type was returned", str(fault).split()[-1]
                problem = ("needs_evidence", "The billed estimate needs a valid %s; DescribeVolumes reports %s for this volume." % (field, json.dumps(volume.get(field))), {"kind": "input", "name": "a valid %s for %s" % (field, volume_id)}) if field in ("Size", "Iops", "Throughput") else ("needs_evidence", "Cost Explorer could not price this volume (%s): %s. A CUR table, a supplied annual cost or the customer's own choice of public prices resolves it." % (fault, reasons), billing["request"])
        elif priced and pricing_mode == "supplied":
            annual_cost = supplied_annual_cost(supplied_rows.get(volume_id), supplied)
            if annual_cost is None:
                problem = ("needs_evidence", "The supplied costs hold no single usable annual cost in USD for this volume.", {"kind": "input", "name": "annual cost in USD with period, source and method"})
        elif priced and catalog[kind][0] is None:
            extra_refs, problem = catalog_refs[kind] + catalog_refs["archive"], ("needs_evidence" if "missing" in catalog[kind][1] else "error", "The public catalog read for %s %s." % (kind, "is incomplete" if "missing" in catalog[kind][1] else "failed"), catalog_reads[kind])
        elif priced:
            rates, quotes, problems = catalog[kind]
            extra_refs = catalog_refs[kind] + catalog_refs["archive"]
            unquoted = set(problems) - {"io"} and ("needs_evidence", "Public prices for %s are missing or ambiguous: %s." % (kind, "; ".join("%s %s" % item for item in problems.items())), catalog_reads[kind])
            try:
                public = estimate_public_cost(volume, rates)
                calc["public_price_model"] = {"input": {k: volume[k] for k in ("VolumeType", "Size", "Iops", "Throughput") if k in volume}, "rates": rates, "quotes": quotes, "result": public}
                if placement:
                    details["public_price_model"], problem = public, placement
                elif unquoted:
                    problem = unquoted
                elif public["annualPublicCost"] is None:
                    details["public_price_model"] = public
                    problem = ("needs_evidence", "A magnetic volume's public cost needs its annual I/O quantity; the storage subtotal is in details.public_price_model.", {"kind": "input", "name": "annual I/O quantity for " + volume_id})
                elif regional is None:
                    problem = ("needs_evidence" if isinstance(regional_problem, str) or "missing" in regional_problem else "error", "Regional archive storage price unavailable: %s." % (
                        regional_problem if isinstance(regional_problem, str) else "the catalog read is incomplete" if "missing" in regional_problem else "the catalog read failed"), catalog_reads["archive"])
                else:
                    annual_cost, archive_rate, archive_source = public["annualPublicCost"], regional["rate"], "regional_quote"
                    result = estimate_regional_archive(size, archive_rate, annual_cost)
                    calc["regional_estimate"] = {"input": {"size": size, "regionalArchiveRate": archive_rate, "supportedAnnualCost": annual_cost}, "quote": regional, "result": result}
            except ValueError as fault:
                problem = placement or unquoted or ("needs_evidence", "The public-price estimate needs %s." % str(fault).replace("Missing or invalid ", "a valid "), {"kind": "input", "name": str(fault)})
        if annual_cost is not None and pricing_mode != "public_prices" and archive is None:
            problem, annual_cost = ("needs_evidence", "The archive price lookup was not performed.", archive_ref), None
        elif annual_cost is not None and pricing_mode != "public_prices" and size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or size < 0):
            problem = ("needs_evidence", "The savings formula needs a valid Size; DescribeVolumes reports %s for this volume." % json.dumps(size), {"kind": "input", "name": "a valid Size for " + volume_id})
        elif annual_cost is not None and pricing_mode != "public_prices":
            archive_rate, archive_source, extra_refs = archive["rate"], archive["source"], extra_refs + [archive_ref]
            result = estimate_regional_archive(size or 0, archive_rate, annual_cost)
            calc["native_formula"] = {"input": {"annualCost": annual_cost, "volumeSize": size or 0, "archiveSnapshotPrice": archive_rate},
                                      "expression": "archiveSnapshotAnnualCost = price * size * 12; annualSavings = max(0, annualCost - archive); newCost = annualCost - annualSavings", "result": result}
            row_quals += ["Size was not reported; the native formula used 0, so the archive cost is 0."] if size is None else []
        if not priced and volume and annual_cost is None:  # a rejected or unresolved row still carries a current cost that needs no further read
            if pricing_mode == "supplied":
                annual_cost = supplied_annual_cost(supplied_rows.get(volume_id), supplied)
            elif pricing_mode == "billing" and billing and not billing_failure:
                try:
                    annual_cost = estimate_billed_cost(volume, billing["rates"].get(kind, {}), billing["window"]["start"][:10])["annualCost"]
                except ValueError:
                    pass
            elif pricing_mode == "public_prices" and not placement and catalog.get(kind) and catalog[kind][0] is not None:
                try:
                    annual_cost = estimate_public_cost(volume, catalog[kind][0])["annualPublicCost"]
                except ValueError:
                    pass
            if annual_cost is not None:
                calc["current_cost"] = {"input": {k: volume[k] for k in ("VolumeType", "Size", "Iops", "Throughput") if k in volume}, "basis": basis, "result": annual_cost}
        if result:
            savings, target = result["annualSavingsEstimate"], result["newCostEstimate"]
            prefix = "model_checks: public-price estimate, " if pricing_mode == "public_prices" else ""
            rows += [("MinimalSavings", savings > minimal, {"annualSavings": savings, "annualCost": annual_cost, "minimalSavings": minimal},
                      "%s%s %s %s." % (prefix, js_number(savings), ">" if savings > minimal else "is not greater than the minimum", js_number(minimal))),
                     ("MaximumSavings", savings <= annual_cost, {"annualSavings": savings, "annualCost": annual_cost},
                      "%s%s %s %s." % (prefix, js_number(savings), "<=" if savings <= annual_cost else "exceeds the annual cost", js_number(annual_cost)))]
            details.update(archive_rate=archive_rate, archive_rate_source=archive_source, archive_annual=result["archiveAnnualEstimate"])
            if archive_source == "fallback":
                row_quals.append("Archive rate is the %s USD/GB-month fallback; %s." % (js_number(ARCHIVE_FALLBACK), fallback_cause + (" for " + region if region in PRICING_LOCATIONS else "")))
                fallback_volumes.append(volume_id)
            costs = dict({"status": "calculated"}, **shared_costs, current_annual=annual_cost, target_annual=target, annual_savings=savings, **({"qualifications": row_quals} if row_quals else {}))
        else:
            costs = dict({"status": "not_calculated"}, **({"basis": basis, "currency": "USD", "current_annual": annual_cost} if annual_cost is not None else {}))
        rejected = any(approved is False for _, approved, _, _ in rows)
        tail = {"error": "Not run: a required read failed.", "needs_evidence": "Not run: a required input is missing."}[problem[0]] if problem and not rejected else "Not run after the earlier rejection."
        finding = {"id": volume_id, "resource": {"id": volume_id, "type": RESOURCE_TYPE, "region": region}, "decision": "not_recommended" if rejected else problem[0] if problem else "recommended",
                   "checks": check_rows(rows, tail)}
        if finding["decision"] == "recommended":
            finding["recommendation"] = {"action": "snapshot_then_delete_volume", "parameters": {"immediate_archival": kind in IMMEDIATE_ARCHIVAL_TYPES}}
            details["snapshots"] = None if snapshots is None else snapshots.get(volume_id, [])
        elif problem and not rejected:
            issues.append(dict({"kind": "read_error" if problem[0] == "error" else "missing_evidence", "stage": "costing" if priced else "collection", "resource_id": volume_id, "message": problem[1]},
                               **({"request": problem[2]} if problem[2] else {})))
            failed_calls.add(metrics.get(volume_id, {}).get("call"))
        if priced and finding["decision"] != "recommended" and not any(r["status"] == "fail" for r in finding["checks"][:6]):
            finding["technical_eligibility"] = "eligible"
        finding["costs"], finding["details"] = costs, details
        finding["evidence_refs"] = refs + extra_refs + (["evidence/calculations.json#/" + volume_id] if calc else [])
        calculations.update({volume_id: calc} if calc else {})
        findings.append(finding)
    issues += [issue for call, issue in metric_issues.items() if call in failed_calls]  # after the rows' own issues: the report gives a row its own reason
    calculations.update({"cost_explorer_rates": {region: billing["provenance"]}} if billing and calculations else {})
    evidence.files["calculations"] = calculations
    if billing_failure:
        issues.append(dict({"kind": billing_failure["kind"], "stage": "costing", "message": "Billing unavailable: " + billing_failure["message"] + (" %s: %s" % (billing_failure["error"]["code"], billing_failure["error"]["message"]) if billing_failure.get("error") else "")},
                           **({"evidence_refs": [billing_failure["ref"]]} if billing_failure.get("ref") else {}), **({"request": billing_failure["request"]} if billing_failure.get("request") else {})))
    recommended = [f for f in findings if f["decision"] == "recommended"]
    unassessed = [f["id"] for f in findings if f["decision"] in ("needs_evidence", "error")]
    totals = {"decision_counts": {d: sum(f["decision"] == d for f in findings) for d in ("recommended", "not_recommended", "needs_evidence", "error")}, "annual_savings": None}
    if recommended:
        totals.update({key: shared_costs[key] for key in ("currency", "basis", "estimation_method")})
        totals.update({field: sum(f["costs"][field] for f in recommended) for field in ("current_annual", "target_annual", "annual_savings")})
    assessment.update(findings=findings, totals=totals, coverage={"inventory_complete": inventory_complete, "discovered_count": len(ids), "assessed_count": len(findings) - len(unassessed), "unassessed_ids": unassessed})
    assessment["run"]["status"] = "complete" if not unassessed and inventory_complete else "partial"
    archive_basis = "the finder's %s USD/GB-month fallback rate" % js_number(ARCHIVE_FALLBACK) if (archive or {}).get("source") == "fallback" else "the archive price lookup"
    qualifications += [text for text in (
        "Model-based estimate: the after cost prices an archived snapshot at the full volume size for twelve months at %s. Volume size does not establish what a snapshot is billed for." % (
            "the regional archive rate" if pricing_mode == "public_prices" else archive_basis),
        "Not priced: existing snapshots, standard-tier residence before archiving, the 90-day archive minimum, retrieval and early-deletion charges, discounts and taxes. One-time costs and first-year savings are unknown.",
        "Current cost is a twelve-month AWS public-price run rate for the current settings, not billed cost." if pricing_mode == "public_prices" else
        "Current costs were supplied by the caller (%s) and used unchanged." % ((supplied or {}).get("source") or "no source stated") if pricing_mode == "supplied" else
        "Current cost is an account-and-region effective rate from Cost Explorer applied to each volume's current capacity for the part of the 31-day window after its creation, annualized as the finder does (sum / 31 * 365). It is not an individual-volume invoice." if billing else None,
        "This is not CUR equivalence: CloudFix's own figure uses each volume's billed cost, so the two can differ; a volume's lifetime, setting changes and individual adjustments are not reconstructed; a visible net field does not prove that every payer discount is visible, and the billing view may be pro forma; magnetic I/O request charges are not included; the after cost uses %s, not a negotiated rate." % archive_basis if billing else None,
        "Cost Explorer: %d of 31 days returned, %d marked Estimated; net-cost fallbacks and blended rates are recorded per rate in evidence/calculations.json." % (billing["days_returned"], billing["estimated_days"]) if billing else None,
        "Archive rate: the finder's %s USD/GB-month fallback was used for %s because %s; their savings depend on that rate." % (js_number(ARCHIVE_FALLBACK), ", ".join(fallback_volumes), fallback_cause) if fallback_volumes else None,
        "CloudFix recommendation history was not available, so RecommendationFinalized was not evaluated for any volume. The total covers %d volumes and %s USD assessed without that history." % (
            len(recommended), js_number(float(totals["annual_savings"]))) if recommended else None,
        "Volumes were discovered by status=available; parity with CloudFix billing-based discovery was not checked." if selection is None
        else "Only the selected volumes were assessed; this is not an account-wide inventory and CloudFix's discovery conditions were not checked.",
        "An available state and missing attachment metrics do not establish owner intent or recovery readiness.",
        "Detachment threshold: %d days (%s); creation age minimum %d days; dates evaluated in UTC." % (threshold, "default" if threshold == DEFAULT_CONFIGURATION["volumeAgeThreshold"] else "configured", MIN_AGE_DAYS)) if text]
    assessment["verification"]["summary"] = ("Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. "
                                             "Each volume's calculator inputs and results are in evidence/calculations.json; its check values are in assessment.json. AWS facts were not re-read. No independent review took place.")
    return close()

if __name__ == "__main__":
    sys.exit(main())
