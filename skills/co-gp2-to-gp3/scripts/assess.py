#!/usr/bin/env python3
"""Assess gp2 volumes for conversion to gp3 with the customer's own AWS read access.
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
RESOURCE_NOUN, EMPTY_INVENTORY_NOUN = "volume", "gp2 volumes"
REPORT_FOOTER = "Nothing was executed; these are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`."
ALLOWED_READS = frozenset({("sts", "GetCallerIdentity"), ("pricing", "GetProducts"), ("ec2", "DescribeVolumes"), ("ec2", "DescribeInstances"),
                           ("ce", "GetDimensionValues"), ("ce", "GetCostAndUsage")})  # the finder's assessment reads; aws() refuses anything else

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

SKILL_ID, RESOURCE_TYPE = "co-gp2-to-gp3", "AWS::EC2::Volume"
CHECK_NAMES = ("ResourceExists", "ResourceFixed", "VolumeExpectedType", "VolumeAgeXDays", "EMRClusterAssociation", "ElasticVolumeEnabled", "MinimalSavings", "MaximumSavings")
DEFAULT_CONFIGURATION, BATCH = {"volumeAgeThreshold": 7, "minimalSavings": 0}, 200
ELASTIC_SUPPORT_DATE, EMR_TAG = "2016-12-03T23:40:00.000Z", "aws:elasticmapreduce:instance-group-role"
FALLBACK_PRICES = {"gp2_storage": 0.10, "gp3_storage": 0.08, "gp3_iops": 0.005, "gp3_throughput": 0.06}
BASIS_BY_MODE = {"public_prices": "public_price", "billing": "account_effective_rate", "supplied": "caller_supplied"}
GB_MONTH_UNITS, USAGE_TYPE = ("gb-mo", "gb-month", "gb-months"), re.compile(r"(?:^|-)EBS:VolumeUsage\.gp2$")
# The native request filters on the Pricing API location name as well as regionCode.
PRICING_LOCATIONS = {"us-east-2": "US East (Ohio)", "us-east-1": "US East (N. Virginia)", "us-west-1": "US West (N. California)",
                     "us-west-2": "US West (Oregon)", "af-south-1": "Africa (Cape Town)", "ap-east-1": "Asia Pacific (Hong Kong)",
                     "ap-east-2": "Asia Pacific (Taipei)", "ap-south-1": "Asia Pacific (Mumbai)", "ap-south-2": "Asia Pacific (Hyderabad)",
                     "ap-northeast-3": "Asia Pacific (Osaka)", "ap-northeast-2": "Asia Pacific (Seoul)", "ap-southeast-1": "Asia Pacific (Singapore)",
                     "ap-southeast-2": "Asia Pacific (Sydney)", "ap-southeast-3": "Asia Pacific (Jakarta)", "ap-southeast-4": "Asia Pacific (Melbourne)",
                     "ap-southeast-5": "Asia Pacific (Malaysia)", "ap-southeast-6": "Asia Pacific (New Zealand)", "ap-southeast-7": "Asia Pacific (Thailand)",
                     "ap-northeast-1": "Asia Pacific (Tokyo)", "ca-central-1": "Canada (Central)", "ca-west-1": "Canada West (Calgary)",
                     "cn-north-1": "China (Beijing)", "cn-northwest-1": "China (Ningxia)", "eu-central-1": "EU (Frankfurt)", "eu-central-2": "Europe (Zurich)",
                     "eu-west-1": "EU (Ireland)", "eu-west-2": "EU (London)", "eu-south-1": "EU (Milan)", "eu-south-2": "Europe (Spain)", "eu-west-3": "EU (Paris)",
                     "eu-north-1": "EU (Stockholm)", "il-central-1": "Israel (Tel Aviv)", "me-central-1": "Middle East (UAE)", "me-south-1": "Middle East (Bahrain)",
                     "mx-central-1": "Mexico (Central)", "sa-east-1": "South America (Sao Paulo)", "us-gov-west-1": "AWS GovCloud (US-West)",
                     "us-gov-east-1": "AWS GovCloud (US-East)"}
PRICE_QUERIES = {  # name: (TERM_MATCH fields, unit, add regionCode); every query also carries the location
    "gp2_storage": ({"volumeType": "General Purpose", "volumeApiName": "gp2"}, "GB-Mo", True), "gp3_storage": ({"volumeType": "General Purpose", "volumeApiName": "gp3"}, "GB-Mo", True),
    "gp3_iops": ({"volumeApiName": "gp3", "group": "EBS IOPS"}, "IOPS-Mo", False), "gp3_throughput": ({"volumeApiName": "gp3", "group": "EBS Throughput"}, "GiBps-mo", False)}

def add_skill_arguments(parser):
    parser.add_argument("--billing-profile", help="AWS CLI profile for Cost Explorer (default: --profile)")
    parser.add_argument("--allow-public-fallback", action="store_true", help="the customer asked for billed costs if possible, otherwise public prices")
    parser.add_argument("--volume-id", action="append", help="assess this volume (repeatable); default: every gp2 volume in the region")
    parser.add_argument("--volume-age-threshold", type=int, help="days; default 7")
    parser.add_argument("--minimal-savings", type=float, help="annual currency units; default 0")

def describe_change(row):
    p = row["recommendation"]["parameters"]
    return "gp3, %s GiB, %s IOPS, %s MiB/s" % (p["size_gib"], "{:,}".format(p["iops"]), p["throughput_mibps"])

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
    return []

def skill_revision():
    with open(os.path.join(SCRIPT_DIR, "..", "SKILL.md"), encoding="utf-8") as handle:
        return re.search(r'^  version: "([^"]+)"', handle.read(), re.M).group(1)

def sizing(size_gib):
    gp2_iops = min(16000, max(100, 3 * size_gib))
    gp3_iops = min(80000, max(gp2_iops, 3000))
    gp2_throughput = math.ceil(min(250, gp2_iops / 4))
    gp3_throughput = min(2000, max(gp2_throughput, 125))
    return {"gp2Iops": gp2_iops, "gp3Iops": gp3_iops, "provisionedIops": max(0, gp2_iops - 3000),
            "gp2Throughput": gp2_throughput, "gp3Throughput": gp3_throughput, "provisionedThroughput": max(0, gp3_throughput - 125)}

def volume_price(size, pricing, iops, throughput):
    storage_cost = size * pricing["storagePrice"] * 12
    iops_value = max(0, iops - pricing["freeIops"]) if pricing["iopsPrice"] > 0 else 0
    iops_cost = iops_value * pricing["iopsPrice"] * 12
    throughput_value = max(0, throughput - pricing["freeThroughput"]) if pricing["throughputPrice"] > 0 else 0
    throughput_cost = throughput_value * pricing["throughputPrice"] * 12
    return {"storageCost": storage_cost, "iopsCost": iops_cost, "throughputCost": throughput_cost,
            "iopsValue": iops_value, "throughputValue": throughput_value, "finalPrice": storage_cost + iops_cost + throughput_cost}

def rate(cost, value):
    """'$' + parseFloat((cost / value / 12).toFixed(6)), or '$0.00' when nothing is billed."""
    return "$" + js_number(float(to_fixed(cost / value / 12, 6))) if value > 0 else "$0.00"

def savings_fields(size, annual_cost, public_annual_cost, prices, settings):
    gp2 = volume_price(size, prices["gp2"], settings["gp2Iops"], 0)
    gp3 = volume_price(size, prices["gp3"], settings["gp3Iops"], settings["gp3Throughput"])
    # JavaScript divides by zero to Infinity or NaN; the result is then annotated as not finite.
    ratio = gp3["finalPrice"] / gp2["finalPrice"] if gp2["finalPrice"] else (math.nan if gp3["finalPrice"] == 0 else math.inf)
    target = annual_cost * ratio
    savings = annual_cost - target
    public_savings = gp2["finalPrice"] - gp3["finalPrice"]
    report = {
        "annualSavings": savings, "gp2Cost": annual_cost, "gp3Cost": target,
        "savingsPercentage": to_fixed(savings / annual_cost * 100, 1) if annual_cost > 0 else "0.0",
        "gp2PublicCost": gp2["finalPrice"], "gp3PublicCost": gp3["finalPrice"], "publicSavings": public_savings,
        "publicSavingsPercentage": to_fixed(public_savings / gp2["finalPrice"] * 100, 1) if gp2["finalPrice"] > 0 else "0.0",
        "discountRatio": min(annual_cost / public_annual_cost, 1) if all(math.isfinite(v) and v > 0 for v in (annual_cost, public_annual_cost)) else 1,
        "gp2StorageRate": rate(gp2["storageCost"], size),
        "gp3StorageRate": rate(gp3["storageCost"], size),
        "gp3IopsRate": rate(gp3["iopsCost"], gp3["iopsValue"]), "gp3ThroughputRate": rate(gp3["throughputCost"], gp3["throughputValue"]),
        "gp2StorageCost": gp2["storageCost"], "gp3StorageCost": gp3["storageCost"], "gp3IopsCost": gp3["iopsCost"],
        "gp3ThroughputCost": gp3["throughputCost"], "calculatedGp2Cost": gp2["finalPrice"], "calculatedGp3Cost": gp3["finalPrice"],
        "hourlyGp2Cost": gp2["finalPrice"] / (365 * 24), "hourlyGp3Cost": gp3["finalPrice"] / (365 * 24)}
    return report, {"gp2Public": gp2, "gp3Public": gp3, "annualSavings": {"expression": "annualCost - gp3TargetCost", "result": savings},
                    "gp3TargetCost": {"expression": "annualCost * (gp3Public.finalPrice / gp2Public.finalPrice)", "result": target}}

def select_price(output, unit):
    """First PriceList string holding the unit, first exact-unit USD OnDemand dimension that is not '(free tier)'."""
    for text in output.get("PriceList", []):
        if not isinstance(text, str):  # a parsed object would otherwise be skipped as "no price"
            raise TypeError("a PriceList entry is not a JSON string")
        if unit not in text:
            continue
        item = json.loads(text)
        for term in (item.get("terms", {}).get("OnDemand") or {}).values():
            for key, dimension in (term.get("priceDimensions") or {}).items():
                if "(free tier)" not in (dimension.get("description") or "") and dimension.get("unit") == unit \
                        and dimension.get("pricePerUnit", {}).get("USD") is not None:
                    price = float(dimension["pricePerUnit"]["USD"])
                    if not (math.isfinite(price) and price >= 0):  # NaN, Infinity or a negative amount is not a price and not a missing one
                        raise ValueError("price %r is not a finite non-negative number" % dimension["pricePerUnit"]["USD"])
                    return price, {"sku": item.get("product", {}).get("sku"), "dimension": key}
    return None, None

def resolve_prices(evidence, region, profile):
    """Four Pricing lookups in us-east-1 with the source's fallbacks. Returns (prices, provenance, failure)."""
    raw, provenance = {}, {}
    for name, (fields, unit, with_region_code) in PRICE_QUERIES.items():
        fields = dict(fields, **({"regionCode": region} if with_region_code else {}), location=PRICING_LOCATIONS[region])
        params = {"ServiceCode": "AmazonEC2", "Filters": [{"Type": "TERM_MATCH", "Field": field, "Value": value} for field, value in fields.items()], "MaxResults": 1}
        record, position = evidence.read("pricing", "pricing", "GetProducts", params, "us-east-1", profile)
        if record is None:
            return None, None, {"missing": position}
        if "error" in record:
            return None, None, {"error": record["error"], "ref": evidence.ref("pricing", position)}
        try:
            raw[name], selected = select_price(record["output"], unit)
        except (ValueError, TypeError, AttributeError) as fault:  # a PriceList entry that is not the Pricing JSON: a failed lookup, not "no price"
            return None, None, {"error": {"code": "InvalidPriceList", "message": "GetProducts returned a PriceList entry that could not be read (%s)." % fault},
                                "ref": evidence.ref("pricing", position)}
        provenance[name] = {"unit": unit, "retrieved": raw[name], "selected": selected,
                            "fallback": None if raw[name] is not None else FALLBACK_PRICES[name], "evidence_ref": evidence.ref("pricing", position)}
    resolved = {name: FALLBACK_PRICES[name] if raw[name] is None else raw[name] for name in raw}
    # GiBps-mo divided by 1024 when truthy; a zero or absent throughput price falls back (source truthiness).
    resolved["gp3_throughput"] = raw["gp3_throughput"] / 1024 if raw["gp3_throughput"] else FALLBACK_PRICES["gp3_throughput"]
    if not raw["gp3_throughput"]:
        provenance["gp3_throughput"]["fallback"] = FALLBACK_PRICES["gp3_throughput"]
    return {"gp2": {"storagePrice": resolved["gp2_storage"], "iopsPrice": 0, "throughputPrice": 0, "freeIops": 0, "freeThroughput": 0},
            "gp3": {"storagePrice": resolved["gp3_storage"], "iopsPrice": resolved["gp3_iops"], "throughputPrice": resolved["gp3_throughput"],
                    "freeIops": 3000, "freeThroughput": 125}}, provenance, None

def describe(evidence, file, operation, filter_name, ids, region, profile):
    """Batches of up to 200 filter values, every page, a scoped dry run before each live batch. Returns (records, failure)."""
    all_records, collection = [], {"DescribeVolumes": "Volumes", "DescribeInstances": "Reservations"}[operation]
    for start in range(0, len(ids), BATCH):
        params = {"Filters": [{"Name": filter_name, "Values": ids[start:start + BATCH]}]}
        # Replay answers the probe only from evidence that recorded one; evidence saved without probes holds none.
        probe, position = evidence.read(file, "ec2", operation, dict(params, DryRun=True), region, profile)
        if probe is not None and (probe.get("error") or {}).get("code") != "DryRunOperation":
            return all_records, {"error": probe.get("error") or {"code": "UnexpectedDryRunSuccess", "message": "dry run returned output"}, "ref": evidence.ref(file, position)}
        records, failure = evidence.pages(file, "ec2", operation, params, region, profile)
        all_records += records
        if failure and "error" in failure:
            return all_records, failure
        for record, position in records:  # an answer without its list is not an empty answer
            if not isinstance(record["output"].get(collection), list):
                return all_records, {"error": {"code": "InvalidResponse", "message": "The %s response has no %s list." % (operation, collection)},
                                     "ref": evidence.ref(file, position)}
        if failure:
            return all_records, failure
    return all_records, None

def check_times(objects, key):
    """A present non-null AWS timestamp must parse; anything else is input the run cannot use."""
    for obj in objects:
        if obj.get(key) is not None:
            try:
                parse_time(obj[key])
            except (ValueError, TypeError, AttributeError):
                raise InputError("Invalid AWS timestamp: %s %r" % (key, obj[key]))

def parse(records, collection, required, nested=None):
    """The source's map: required fields present, last duplicate wins, raw position kept; the rest is dropped."""
    kept, dropped = {}, []
    for record, position in records:
        for index, item in enumerate(record["output"].get(collection) or []):
            for sub_index, obj in (enumerate(item.get(nested) or []) if nested and isinstance(item, dict) else [(None, item)]):
                complete = isinstance(obj, dict) and all(obj.get(key) is not None for key in required)
                kept.update({obj[required[0]]: (obj, position, index) + ((sub_index,) if nested else ())} if complete else {})
                dropped += [] if complete else [obj]
    return kept, dropped

def first_attachment(volume):
    return (volume.get("Attachments") or [None])[0]

def evaluate_checks(volume_id, volumes, instances, instances_missing, as_of, configuration):
    """The finder's rows 2-7 with their exact value shapes; state is stopped, rejected, approved or needs_instance."""
    if volume_id not in volumes:
        return [("ResourceExists", False, {"resourceId": volume_id}, "The completed volume read did not return this volume.")], "stopped"
    volume = volumes[volume_id][0]
    kind = volume["VolumeType"]
    checks = [("ResourceExists", True, {"resourceId": volume_id}, None),
              ("ResourceFixed", kind != "gp3", {"volumeType": kind, "fixedVolumeTypes": ["gp3"]}, "The volume is already gp3."),
              ("VolumeExpectedType", kind == "gp2", {"volumeType": kind, "expectedTypes": ["gp2"]}, "The volume is not gp2.")]
    if not all(check[1] for check in checks):  # ResourceExists, ResourceFixed and VolumeExpectedType fail fast
        return checks[:[check[1] for check in checks].index(False) + 1], "stopped"
    threshold, create_time = configuration["volumeAgeThreshold"], volume.get("CreateTime")
    if create_time is None:
        checks.append(("VolumeAgeXDays", False, {"minAgeDays": threshold, "reason": "create-time-unreported"}, "The volume reports no creation time."))
    else:
        age_days = int((as_of - parse_time(create_time)).total_seconds() * 1000 / 86400000)  # Math.trunc of whole days
        checks.append(("VolumeAgeXDays", age_days >= threshold, {"createTime": iso_js(create_time), "ageDays": age_days, "minAgeDays": threshold},
                       "%d days old; the minimum is %d days." % (age_days, threshold)))
    attachment = first_attachment(volume)
    instance_id = attachment.get("InstanceId") if attachment else None
    instance = instances[instance_id][0] if instance_id in instances else None
    if instance_id is None:
        checks.append(("EMRClusterAssociation", True, {}, None))
    elif instance is not None:
        role = next((tag.get("Value") for tag in instance.get("Tags") or [] if tag.get("Key") == EMR_TAG), None)
        checks.append(("EMRClusterAssociation", role is None,
                       {"attachedInstanceId": instance_id} if role is None else {"attachedInstanceId": instance_id, "emrInstanceGroupRole": role},
                       "The attached instance belongs to an EMR cluster."))
    elif instance_id in instances_missing:
        checks.append(("EMRClusterAssociation", None, None, "The DescribeInstances response for %s was not supplied." % instance_id))
        return checks, "needs_instance"
    else:
        checks.append(("Ec2InstanceDescribable", False, {"attachedInstanceId": instance_id},
                       "The attached instance %s is absent from the completed instance read." % instance_id))
    # Elastic Volumes decision tree; a missing time substitutes the cutoff, which is not "after".
    cutoff = parse_time(ELASTIC_SUPPORT_DATE)
    after = lambda text: (parse_time(text) if text else cutoff) > cutoff
    value, elastic = {"createTime": iso_js(create_time)} if create_time else {}, after(create_time) or attachment is None
    if not elastic:
        attach_time, launch = attachment.get("AttachTime"), instance.get("LaunchTime") if instance else None
        value.update({"attachTime": iso_js(attach_time)} if attach_time is not None else {})
        value.update({"instanceLaunchTime": iso_js(launch)} if not after(attach_time) and launch is not None else {})
        elastic = after(attach_time) or (launch is not None and after(launch))
    value["elasticSupportDate"] = ELASTIC_SUPPORT_DATE
    checks.append(("ElasticVolumeEnabled", elastic, value, "Elastic Volumes are not supported for this volume."))
    return checks, "approved" if all(check[1] for check in checks) else "rejected"

def billed_months(create_time, window):
    """The months of the billing window in which the volume was billed: each day's part at or after CreateTime, over the days in that day's month."""
    day, end, created, months = parse_time(window["start"]), parse_time(window["end"]), parse_time(create_time), 0.0
    while day < end:
        first, following = day.date().replace(day=1), day + datetime.timedelta(days=1)
        billed_part = min(max((following - max(created, day)).total_seconds() / 86400, 0), 1)
        months += billed_part / ((first + datetime.timedelta(days=31)).replace(day=1) - first).days
        day = following
    return months

def collect_billing(evidence, account, region, profile):
    """Cost Explorer effective gp2 rate per references/billing.md. Returns (result, failure)."""
    midnight = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for record in evidence.files.get("billing") or [] if evidence.replay else []:
        if not isinstance(record, dict) or not isinstance(record.get("input"), dict):
            raise InputError("evidence/billing.json must be a list of read records, each with an input object")
        if record["input"].get("TimePeriod"):  # replay keeps the saved window
            try:
                midnight = parse_time(record["input"]["TimePeriod"]["End"] + "T00:00:00Z") + datetime.timedelta(days=1)
            except (KeyError, TypeError, ValueError):
                raise InputError("evidence/billing.json: a saved TimePeriod has no End date")
            break
    start, end = (midnight - datetime.timedelta(days=32)).date().isoformat(), (midnight - datetime.timedelta(days=1)).date().isoformat()
    common = [{"Dimensions": {"Key": key, "Values": [value]}} for key, value in (
        ("LINKED_ACCOUNT", account), ("REGION", region), ("SERVICE", "EC2 - Other"), ("RECORD_TYPE", "Usage"))]
    identity, position = evidence.read("billing", "sts", "GetCallerIdentity", {}, "us-east-1", profile)
    if identity is None:
        return None, {"kind": "missing_evidence", "message": "The billing identity read was not supplied.", "request": position}
    if "error" in identity:
        return None, {"kind": "read_error", "message": "Billing identity could not be verified.", "error": identity["error"], "ref": evidence.ref("billing", position),
                      "request": {"kind": "aws_read", "name": "GetCallerIdentity", "service": "sts", "region": "us-east-1", "parameters": {}}}

    def paged(operation, params):
        """Every page of one Cost Explorer request: (records, failure, the request as an issue names it)."""
        request = {"kind": "aws_read", "name": operation, "service": "ce", "region": "us-east-1", "parameters": params}
        records, failure = evidence.pages("billing", "ce", operation, params, "us-east-1", profile, token="NextPageToken")
        return records, failure and dict(kind="read_error" if "error" in failure else "missing_evidence", message=operation + " did not complete.",
                                         request=request, **failure), request
    params = {"TimePeriod": {"Start": start, "End": end}, "Dimension": "USAGE_TYPE", "Context": "COST_AND_USAGE",
              "SearchString": "VolumeUsage.gp2", "Filter": {"And": common}}
    records, failure, dimension_read = paged("GetDimensionValues", params)
    if failure:
        return None, failure
    usage_types = {}
    for record, _ in records:
        values = record["output"].get("DimensionValues")
        if not isinstance(values, list) or not all(isinstance(value, dict) and isinstance(value.get("Attributes") or {}, dict) for value in values):
            return None, {"kind": "read_error", "message": "GetDimensionValues returned invalid data (no list of DimensionValues objects).", "request": dimension_read}
        for value in values:
            name, unit = value.get("Value"), (value.get("Attributes") or {}).get("unit")
            if isinstance(name, str) and USAGE_TYPE.search(name):
                if not isinstance(unit, str) or unit.lower() not in GB_MONTH_UNITS or usage_types.get(name, unit) != unit:
                    return None, {"kind": "read_error", "message": "Usage type %s has an unsupported unit %r." % (name, unit), "request": dimension_read}
                usage_types[name] = unit
    if not usage_types:
        return None, {"kind": "missing_evidence", "message": "Cost Explorer returned no gp2 volume usage type for this account and region in %s to %s." % (start, end),
                      "request": dimension_read}
    params = {"TimePeriod": {"Start": start, "End": end}, "Granularity": "DAILY", "Metrics": ["NetUnblendedCost", "UnblendedCost", "UsageQuantity"],
              "Filter": {"And": common + [{"Dimensions": {"Key": "USAGE_TYPE", "Values": sorted(usage_types)}}]}}
    records, failure, usage_read = paged("GetCostAndUsage", params)
    if failure:
        return None, failure
    selected = unblended = usage = 0.0
    days, estimated, net_fallbacks = set(), 0, 0
    for record, _ in records:
        if not isinstance(record["output"].get("ResultsByTime"), list):
            return None, {"kind": "read_error", "message": "GetCostAndUsage returned invalid data (no ResultsByTime list).", "request": usage_read}
        for day in record["output"]["ResultsByTime"]:
            window, total = (day.get("TimePeriod") or {}, day.get("Total")) if isinstance(day, dict) else ({}, None)
            try:  # exactly one day; with a unique start inside the window that also rules out an overlap
                one_day = datetime.date.fromisoformat(window["End"]) - datetime.date.fromisoformat(window["Start"]) == datetime.timedelta(days=1)
            except (KeyError, TypeError, ValueError):
                one_day = False
            if not one_day or day.get("Groups") or not isinstance(total, dict) or not isinstance(day.get("Estimated"), bool) \
                    or window["Start"] in days or not (start <= window["Start"] < end):
                return None, {"kind": "read_error", "message": "GetCostAndUsage returned a malformed or overlapping daily period.", "request": usage_read}
            try:
                net, gross, quantity = total.get("NetUnblendedCost"), total["UnblendedCost"], total["UsageQuantity"]
                # unblended only when net is absent/null or its Amount is null; an empty or malformed net field is an error
                net_fallback = net is None or (isinstance(net, dict) and "Amount" in net and net["Amount"] is None)
                if gross.get("Unit") != "USD" or (not net_fallback and net.get("Unit") != "USD") \
                        or quantity.get("Unit", "N/A").lower() not in GB_MONTH_UNITS + ("n/a",):
                    raise ValueError("unit")
                day_gross, day_quantity = float(gross["Amount"]), float(quantity["Amount"])
                day_selected = day_gross if net_fallback else float(net["Amount"])
                if not all(math.isfinite(v) for v in (day_gross, day_quantity, day_selected)) or day_quantity < 0 \
                        or (day_quantity == 0 and (day_selected != 0 or day_gross != 0)):  # zero usage must carry zero cost
                    raise ValueError("amount")
            except (KeyError, TypeError, ValueError, AttributeError) as fault:
                return None, {"kind": "read_error", "message": "GetCostAndUsage returned invalid data (%s)." % fault, "request": usage_read}
            days.add(window["Start"])
            estimated += day["Estimated"]
            net_fallbacks += net_fallback
            selected, unblended, usage = selected + day_selected, unblended + day_gross, usage + day_quantity
    if usage > 0 and not (math.isfinite(selected) and selected >= 0 and math.isfinite(unblended)):
        return None, {"kind": "read_error", "message": "GetCostAndUsage returned invalid data (the aggregate cost is negative or not finite).", "request": usage_read}
    if not usage > 0:
        return None, {"kind": "missing_evidence", "message": "Cost Explorer returned no positive gp2 usage for this account and region in %s to %s." % (start, end),
                      "request": usage_read}
    return {"effectiveMonthlyGp2Rate": selected / usage, "selectedCost": selected, "unblendedCost": unblended, "usageGbMonths": usage,
            "usageTypes": usage_types, "window": {"start": start + "T00:00:00Z", "end": end + "T00:00:00Z"}, "daysReturned": len(days),
            "daysMissing": 31 - len(days), "estimatedDays": estimated, "netCostFallbackDays": net_fallbacks, "billingAccount": identity["output"].get("Account")}, None

def run(args):
    out_dir = args.out
    issues, qualifications = [], []
    if args.replay:
        evidence = Evidence(args.replay, True)
        saved = evidence.files.get("run-metadata")
        metadata, request = (saved.get("run_metadata"), saved.get("request")) if isinstance(saved, dict) else (None, None)
        if not metadata or not request or not isinstance(metadata, dict) or not isinstance(request, dict):
            raise InputError("The replay folder has no run-metadata.json")
        evidence.directory = os.path.join(out_dir, "evidence")
    else:
        evidence = Evidence(os.path.join(out_dir, "evidence"), False)
        configuration = dict(DEFAULT_CONFIGURATION, **{key: value for key, value in (
            ("volumeAgeThreshold", args.volume_age_threshold), ("minimalSavings", args.minimal_savings)) if value is not None})
        if configuration["volumeAgeThreshold"] < 0 or not math.isfinite(configuration["minimalSavings"]):
            sys.stderr.write("volumeAgeThreshold must be an integer >= 0 and minimalSavings finite\n")
            return 1
        metadata = {"accountId": args.account, "region": args.region, "asOf": iso_js(parse_time(args.as_of) if args.as_of else datetime.datetime.now(datetime.timezone.utc)),
                    "configuration": configuration,
                    "configuration_overrides": {key: value for key, value in configuration.items() if value != DEFAULT_CONFIGURATION[key]},
                    "run_classification": "standard", "experiment": None}
        request = {"pricing_mode": args.pricing_mode, "resource_ids": args.volume_id, "allow_public_fallback": args.allow_public_fallback,
                   "profile": args.profile, "billing_profile": args.billing_profile}
        evidence.files["run-metadata"] = {"run_metadata": metadata, "request": request}
        if args.pricing_mode == "supplied":
            if not args.costs:
                raise InputError("--pricing-mode supplied needs --costs FILE")
            try:
                with open(args.costs, encoding="utf-8") as handle:
                    evidence.files["supplied-costs"] = json.load(handle)
            except (OSError, ValueError) as fault:
                raise InputError("--costs could not be read as JSON: %s" % fault)
            if not isinstance(evidence.files["supplied-costs"], dict):
                raise InputError("--costs must hold a JSON object")
    try:
        as_of = parse_time(metadata["asOf"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise InputError("run-metadata.json: asOf must be an ISO 8601 time")
    account, region, configuration = metadata.get("accountId"), metadata.get("region"), metadata.get("configuration")
    pricing_mode, selection, profile = request.get("pricing_mode"), request.get("resource_ids"), request.get("profile")
    billing_profile = request.get("billing_profile") or profile
    if pricing_mode not in PRICING_MODES:
        raise InputError("run-metadata.json: pricing_mode must be one of %s, not %r" % (", ".join(PRICING_MODES), pricing_mode))
    supplied_costs = evidence.files.get("supplied-costs") if pricing_mode == "supplied" else None
    if pricing_mode == "supplied":  # checked for a live run and a replay alike, before any read
        if not isinstance(supplied_costs, dict) or not isinstance(supplied_costs.get("resources", []), list) or not all(isinstance(row, dict) and isinstance(row.get("resourceId"), str) for row in supplied_costs.get("resources", [])):
            raise InputError("supplied-costs.json must hold a JSON object whose resources are objects with a resourceId")
        ids = [row["resourceId"] for row in supplied_costs.get("resources", [])]
        if len(set(ids)) != len(ids):  # a volume named twice has no one cost: never the last entry silently
            raise InputError("supplied-costs.json names a resourceId more than once: %s" % ", ".join(sorted({one for one in ids if ids.count(one) > 1})))
        if supplied_costs.get("currency", "USD") != "USD":  # every amount, price and total is reported in USD
            raise InputError("supplied-costs.json: currency must be USD, not %r" % supplied_costs["currency"])
        supplied_prices = supplied_costs.get("prices") or {}
        try:
            amounts = [row[key] for row in supplied_costs.get("resources", []) for key in ("annualCost", "publicAnnualCost")]
            amounts += [supplied_prices[kind][key] for kind in ("gp2", "gp3") if supplied_prices
                        for key in ("storagePrice", "iopsPrice", "throughputPrice", "freeIops", "freeThroughput")]
        except (KeyError, TypeError):
            amounts = [None]
        if not all(type(amount) in (int, float) and math.isfinite(amount) and amount >= 0 for amount in amounts):
            raise InputError("supplied-costs: every annualCost, publicAnnualCost and price must be a finite nonnegative number")
    age, minimum = (configuration.get(key) for key in DEFAULT_CONFIGURATION) if isinstance(configuration, dict) and set(configuration) == set(DEFAULT_CONFIGURATION) else (None, None)
    if type(age) is not int or age < 0 or type(minimum) not in (int, float) or not math.isfinite(minimum):
        raise InputError("run-metadata.json: configuration must be exactly volumeAgeThreshold (integer >= 0) and minimalSavings (finite number)")
    looks_up = pricing_mode != "supplied" or not (supplied_costs or {}).get("prices")  # supplied prices need no Pricing lookup
    if looks_up and region.startswith(("cn-", "us-gov-")):
        raise InputError("%s is outside the commercial partition: the Pricing and Cost Explorer reads of this skill are fixed to us-east-1 and cannot answer for it. "
                         "Use --pricing-mode supplied with a costs file that holds prices." % region)
    if looks_up and region not in PRICING_LOCATIONS:
        raise InputError("Unknown region for the Pricing location filter: %s" % region)
    assessment = {
        "schema_version": "1.0", "skill": {"id": SKILL_ID, "revision": skill_revision(), "source_ref": None},
        "run": {"observed_at": metadata["asOf"], "mode": "local_code", "status": "complete"},
        "scope": {"account_id": None, "regions": [region], "resource_ids": selection and list(dict.fromkeys(selection)), "pricing_mode": pricing_mode},  # an id named twice is one id
        "coverage": {"inventory_complete": False, "discovered_count": None, "assessed_count": None, "unassessed_ids": []},
        "findings": [], "totals": {"decision_counts": {"recommended": 0, "not_recommended": 0, "needs_evidence": 0, "error": 0}, "annual_savings": None},
        "issues": issues, "qualifications": qualifications, "verification": {"method": "self_check", "summary":  # an early end; finish() still self-checks the saved files
            "Re-read the saved counts and opened every evidence reference after writing the files. The run ended before any volume was assessed."},
        "evidence_refs": [], "details": {"run_metadata": metadata}, "execution_status": "not_executed"}

    def failed(stage, message, failure=None):
        issue = {"kind": "read_error", "stage": stage, "message": message}
        if failure and failure.get("error"):
            issue["message"] += " %s: %s" % (failure["error"]["code"], failure["error"]["message"])
            issue["evidence_refs"] = [failure["ref"]]
        if failure and failure.get("missing"):
            issue["kind"], issue["request"] = "missing_evidence", failure["missing"]
        issues.append(issue)
        assessment["run"]["status"] = "failed"
        return finish(out_dir, evidence, assessment)
    def missing(message, request):
        issues.append({"kind": "missing_evidence", "stage": "collection", "message": message, "request": request})
    # 1. identity
    identity, position = evidence.read("identity", "sts", "GetCallerIdentity", {}, region, profile)
    assessment["evidence_refs"] += [evidence.ref("identity", position)] if identity else []  # an absent record is reported once, as the missing read
    if identity is None or "error" in identity:
        return failed("scope", "Identity could not be verified.", {"error": identity["error"], "ref": evidence.ref("identity", position)} if identity else {"missing": position})
    actual = identity["output"].get("Account")
    if actual != account or not re.fullmatch(r"\d{12}", str(account)):
        return failed("scope", "The identity read returned no account ID." if actual is None else
                      "The credentials belong to account %s, not the requested %s." % (actual, account) if actual != account else
                      "The requested account %s is not a 12-digit AWS account ID." % account)
    assessment["scope"]["account_id"] = account
    # prices: supplied with the costs, or the four Pricing lookups
    if supplied_costs and supplied_costs.get("prices"):
        prices, price_provenance = supplied_costs["prices"], {"supplied": True, "source": supplied_costs.get("source")}
        qualifications.append("Prices were supplied with the costs (%s); no AWS Pricing lookup was made." % supplied_costs.get("source", "no source stated"))
    else:
        prices, price_provenance, failure = resolve_prices(evidence, region, profile)
        if failure:
            return failed("collection", "The AWS Pricing lookup failed.", failure)
        assessment["evidence_refs"].append("evidence/pricing.json")
        qualifications += ["Price fallback: %s was not returned by AWS Pricing; the finder's default %s USD was used." % (name, record["fallback"])
                           for name, record in price_provenance.items() if record["fallback"] is not None]
    # inventory
    omitted = 0
    if selection is None:
        discovery, failure = describe(evidence, "describe-volumes", "DescribeVolumes", "volume-type", ["gp2"], region, profile)
        if failure and "error" in failure:
            return failed("collection", "The gp2 volume inventory was denied or failed.", failure)
        ids = [volume["VolumeId"] for record, _ in discovery for volume in record["output"]["Volumes"] if isinstance(volume, dict) and volume.get("VolumeId")]
        omitted = sum(len(record["output"]["Volumes"]) for record, _ in discovery) - len(ids)
        if omitted:  # collection.md: a dropped entry is a parsing omission, said so
            qualifications.append("%d returned gp2 inventory entries had no VolumeId and were not assessed; a parsing omission, not a deletion." % omitted)
        if failure:
            missing("The gp2 inventory is incomplete: a page is missing.", failure["missing"])
            assessment["run"]["status"] = "partial"
            assessment["coverage"]["unassessed_ids"] = ids
            return finish(out_dir, evidence, assessment)
    ids = list(dict.fromkeys(selection if selection is not None else ids))
    assessment["evidence_refs"] += ["evidence/describe-volumes.json"] if "describe-volumes" in evidence.files else []
    if not ids:
        assessment["coverage"] = {"inventory_complete": True, "discovered_count": 0, "assessed_count": 0, "unassessed_ids": []}
        if not omitted:
            qualifications.append("A completed gp2 volume inventory in %s returned no volumes." % region)
        return finish(out_dir, evidence, assessment)
    records, failure = describe(evidence, "describe-volumes", "DescribeVolumes", "volume-id", ids, region, profile)
    if failure and "error" in failure:
        return failed("collection", "The volume read was denied or failed.", failure)
    volumes, dropped = parse(records, "Volumes", ("VolumeId", "VolumeType", "State"))
    inventory_complete = failure is None
    check_times([volume for volume, _, _ in volumes.values()], "CreateTime")
    check_times([a for volume, _, _ in volumes.values() for a in volume.get("Attachments") or [] if isinstance(a, dict)], "AttachTime")
    if failure:
        missing("A page of the volume read is missing.", failure["missing"])
    if dropped:
        qualifications.append("%d returned volume objects lacked VolumeId, VolumeType or State and were not parsed; a parsing omission, not a deletion." % len(dropped))
    # instances, from the first attachment of every parsed volume
    instance_ids = list(dict.fromkeys(a["InstanceId"] for v, _, _ in volumes.values() for a in [first_attachment(v)] if a and a.get("InstanceId")))
    instances, instances_missing = {}, set()
    if instance_ids:
        records, failure = describe(evidence, "describe-instances", "DescribeInstances", "instance-id", instance_ids, region, profile)
        if failure and "error" in failure:
            return failed("collection", "The instance read was denied or failed.", failure)
        assessment["evidence_refs"] += ["evidence/describe-instances.json"] if "describe-instances" in evidence.files else []
        instances, dropped = parse(records, "Reservations", ("InstanceId", "InstanceType"), nested="Instances")
        check_times([entry[0] for entry in instances.values()], "LaunchTime")
        if failure:
            # describe() stops at the first batch without a record, so that batch and every later one is unread
            instances_missing = set(instance_ids[instance_ids.index(failure["missing"]["parameters"]["Filters"][0]["Values"][0]):])
            missing("The DescribeInstances response for the attached instances was not supplied.", failure["missing"])
        if dropped:
            qualifications.append("%d returned instance objects lacked InstanceId or InstanceType and were not parsed." % len(dropped))
    # costs
    calculations = {}
    sizes = {vid: entry[0].get("Size") for vid, entry in volumes.items()}
    public_baseline = {}
    for vid, size in sizes.items():
        try:  # a Size whose annual cost overflows, or has more digits than the report can round to cents, is not usable
            if type(size) in (int, float) and size > 0 and money(size * prices["gp2"]["storagePrice"] * 12):
                public_baseline[vid] = size * prices["gp2"]["storagePrice"] * 12
        except ArithmeticError:
            pass
    billing = billing_failure = None
    if pricing_mode == "billing":
        recorded = evidence.files.get("pre-billing-snapshot")  # a replay keeps the time the snapshot was saved
        evidence.files["pre-billing-snapshot"] = {"scope": {"accountId": account, "region": region, "resource_ids": sorted(volumes)}, "asOf": metadata["asOf"],
                                                  "configuration": configuration, "sizes": sizes, "prices": prices, "publicAnnualCost": public_baseline, "saved_at": recorded["saved_at"] if evidence.replay and isinstance(recorded, dict) and recorded.get("saved_at") else iso_js(datetime.datetime.now(datetime.timezone.utc))}
        if volumes:
            billing, billing_failure = collect_billing(evidence, account, region, billing_profile)
        if billing:
            calculations["cost_explorer_rate"] = billing
            qualifications += [
                "Billed costs are a gp2 account-and-region effective rate from Cost Explorer (%s to %s, end exclusive), applied to current capacity for the days of that window on which the volume existed and annualized as the finder does (window cost / 31 * 365); not individual volume invoices or CUR equivalence." % (billing["window"]["start"][:10], billing["window"]["end"][:10]),
                "The gp3 target cost applies the public gp3-to-gp2 price ratio to the billed current cost; negotiated gp3 rates are not known.",
                "Cost Explorer: %d of %d days returned, %d marked Estimated, %d days priced on UnblendedCost because NetUnblendedCost was absent; visible net amounts do not prove complete payer-discount visibility." % (
                    billing["daysReturned"], billing["daysReturned"] + billing["daysMissing"], billing["estimatedDays"], billing["netCostFallbackDays"])]
        elif billing_failure and request.get("allow_public_fallback"):
            pricing_mode = assessment["scope"]["pricing_mode"] = "public_prices"
            detail = billing_failure["message"] + (" %s: %s" % (billing_failure["error"]["code"], billing_failure["error"]["message"]) if billing_failure.get("error") else "")
            qualifications.insert(0, "Billed costs were tried first and were not available: %s The whole run is priced at public prices, as the request allowed." % detail)
            issues.append({"kind": "qualification", "stage": "costing", "message": "Cost Explorer produced no rate: " + detail,
                           "evidence_refs": [billing_failure["ref"]] if billing_failure.get("ref") else ["evidence/billing.json"] if "billing" in evidence.files else []})
            billing_failure = None
    supplied_rows = {row["resourceId"]: row for row in supplied_costs.get("resources", [])} if pricing_mode == "supplied" else {}
    if pricing_mode != "billing":
        qualifications.append({"supplied": "Current costs were supplied by the caller (%s) and used unchanged; the gp3 target applies the public gp3-to-gp2 price ratio." % (supplied_costs or {}).get("source", "no source stated"),
                               "public_prices": "Public prices; the current cost is a public-price illustration of the volume's size, not a billed cost."}[pricing_mode])
    basis = BASIS_BY_MODE[pricing_mode]
    shared_costs = dict({"basis": basis, "estimation_method": "configuration_projection", "currency": "USD"}, **({"source_period": billing["window"]} if billing else {}))
    # one row per volume
    findings, waiting_on_billing = [], 0
    for volume_id in ids:
        checks, state = evaluate_checks(volume_id, volumes, instances, instances_missing, as_of, configuration)
        if volume_id not in volumes and not inventory_complete:  # the missing page may hold it: absence is not decided
            checks, state = [("ResourceExists", None, None, "The volume read is incomplete; this volume may be on the missing page.")], "needs_instance"
        size, public_annual = sizes.get(volume_id), public_baseline.get(volume_id)
        size = size if volume_id in public_baseline else None  # only a Size with a public baseline is usable
        supplied_row, cost_issue, months = supplied_rows.get(volume_id), None, None
        if supplied_row:
            annual_cost, public_annual = supplied_row["annualCost"], supplied_row["publicAnnualCost"]
        elif pricing_mode == "supplied" and volume_id in volumes:
            annual_cost, cost_issue = None, {"kind": "missing_evidence", "stage": "costing", "resource_id": volume_id,
                                             "message": "No supplied cost record for this volume.",
                                             "request": {"kind": "input", "name": "annualCost and publicAnnualCost for " + volume_id}}
        elif pricing_mode == "billing":
            annual_cost = None
            if billing and public_annual is not None and volumes[volume_id][0].get("CreateTime") is not None:  # no CreateTime: no billed days, and the age check rejects it
                # The finder's own annualization of the volume's billed window: ROUND(window cost / 31 * 365, 2), for both costs.
                months = billed_months(volumes[volume_id][0]["CreateTime"], billing["window"])
                if months:
                    annual_cost = float(to_fixed(size * billing["effectiveMonthlyGp2Rate"] * months / 31 * 365, 2))
                    public_annual = float(to_fixed(public_annual / 12 * months / 31 * 365, 2))
                else:  # created at or after the window end: no billed day, so no billed cost, never 0.00 (the finder has no row)
                    public_annual, cost_issue = None, {
                        "kind": "missing_evidence", "stage": "costing", "resource_id": volume_id,
                        "message": "The volume has no billed day in the Cost Explorer window %s to %s (end exclusive), so no billed cost can be stated." % (billing["window"]["start"][:10], billing["window"]["end"][:10]),
                        "request": {"kind": "input", "name": "a billed cost for %s: rerun after its first billed day, or use public prices" % volume_id}}
        else:
            annual_cost = public_annual if pricing_mode == "public_prices" else None
        report_data, expressions, settings, savings_calculated, not_finite = {}, {}, None, False, False
        if state == "approved" and size:  # the source populates parameters only for an approved volume with a truthy Size
            settings = sizing(size)
            report_data = dict({"resourceFixedConfiguration": {"retypeToClass": "gp3"}, "volumeId": volume_id, "volumeSize": size}, **settings)
            if annual_cost is not None:
                try:  # a zero gp2 price divides by zero: no saving is established, and no NaN is saved
                    fields, expressions = savings_fields(size, annual_cost, public_annual, prices, settings)
                    not_finite = not all(math.isfinite(value) for value in [annual_cost] + list(fields.values()) if isinstance(value, (int, float)))
                except ArithmeticError:  # Decimal refuses to round an infinity
                    not_finite = True
                if not_finite:
                    expressions = {}
                else:
                    report_data.update(fields)
                    savings_calculated = True
        annual_savings = report_data["annualSavings"] if savings_calculated else 0
        if state not in ("stopped", "needs_instance"):  # the two savings gates run even after a non-stopping rejection
            gate_cost = annual_cost if annual_cost is not None else 0
            checks += [("MinimalSavings", annual_savings > configuration["minimalSavings"],
                        {"annualSavings": annual_savings, "annualCost": gate_cost, "minimalSavings": configuration["minimalSavings"]},
                        "Annual savings %s do not exceed the minimum %s." % (js_number(annual_savings), js_number(configuration["minimalSavings"]))),
                       ("MaximumSavings", annual_savings <= gate_cost, {"annualSavings": annual_savings, "annualCost": gate_cost}, "Annual savings exceed the annual cost.")]
        rows, stop = [], None
        for name, approved, value, reason in checks:
            if stop:
                rows.append({"name": name, "status": "not_evaluated", "reason": stop})
            elif approved is None:
                rows.append({"name": name, "status": "not_evaluated", "reason": reason})
                stop = "Not run: the assessment stopped at the missing %s." % ("volume page" if name == "ResourceExists" else "instance response")
            elif name in ("MinimalSavings", "MaximumSavings") and not savings_calculated:
                rows.append({"name": name, "status": "not_evaluated", "value": value, "reason":
                             "The finder ran this on its initialized zero savings%s; no saving was calculated." % (" after the earlier rejection" if state == "rejected" else "")})
            else:
                rows.append({"name": name, "status": "pass" if approved else "fail", "value": value} if approved else
                            {"name": name, "status": "fail", "value": value, "reason": reason})
                if not approved and name in ("ResourceExists", "ResourceFixed", "VolumeExpectedType"):
                    stop = "Not run after the earlier rejection."
        rows += [{"name": name, "status": "not_evaluated", "reason": stop or "Not run after the earlier rejection."} for name in CHECK_NAMES[len(rows):]]
        if any(row["status"] == "fail" for row in rows):
            decision = "not_recommended"
        elif not_finite:
            decision = "error"
            issues.append({"kind": "calculation_error", "stage": "costing", "resource_id": volume_id, "message": "annual-savings-not-finite"})
        elif billing_failure and state == "approved":
            decision = "error" if billing_failure["kind"] == "read_error" else "needs_evidence"
            waiting_on_billing += 1
        elif state == "needs_instance" or not inventory_complete or cost_issue or (state == "approved" and not size):
            decision = "needs_evidence"
            if cost_issue:
                issues.append(cost_issue)
            elif state == "approved" and not size:
                issues.append({"kind": "missing_evidence", "stage": "collection", "resource_id": volume_id,
                               "message": "The volume reports no usable Size, so no cost or proposal can be calculated.",
                               "request": {"kind": "input", "name": "a finite positive Size for " + volume_id}})
        else:
            decision = "recommended"
        if savings_calculated:
            costs = dict({"status": "calculated"}, **shared_costs, current_annual=annual_cost, target_annual=report_data["gp3Cost"], annual_savings=annual_savings)
        elif not not_finite and (annual_cost is not None or (public_annual is not None and pricing_mode == "public_prices")):
            costs = {"status": "not_calculated", "basis": basis, "currency": "USD", "current_annual": annual_cost if annual_cost is not None else public_annual}
        else:
            costs = {"status": "not_calculated"}
        finding = {"id": volume_id, "resource": {"id": volume_id, "type": RESOURCE_TYPE, "region": region}, "decision": decision, "checks": rows}
        if decision == "recommended":
            finding["recommendation"] = {"action": "change_volume_type", "parameters": {
                "volume_type": "gp3", "size_gib": size, "iops": settings["gp3Iops"], "throughput_mibps": settings["gp3Throughput"]}}
        if decision == "not_recommended" and savings_calculated and state == "approved":
            finding["technical_eligibility"] = "eligible"
        finding["costs"], refs = costs, []
        if volume_id in volumes:
            refs.append(evidence.ref("describe-volumes", volumes[volume_id][1], "/output/Volumes/%d" % volumes[volume_id][2]))
            attachment = first_attachment(volumes[volume_id][0])
            if attachment and attachment.get("InstanceId") in instances:
                refs.append(evidence.ref("describe-instances", instances[attachment["InstanceId"]][1],
                                         "/output/Reservations/%d/Instances/%d" % instances[attachment["InstanceId"]][2:]))
        calculations[volume_id] = {"size": size, "publicAnnualCost": {"expression": "ROUND(Size * gp2.storagePrice * billedMonths / 31 * 365, 2)" if months is not None else "Size * gp2.storagePrice * 12", "result": public_annual},
                                   "annualCost": annual_cost, **({"billedMonths": months, "annualCostExpression": "ROUND(Size * effectiveMonthlyGp2Rate * billedMonths / 31 * 365, 2)"} if months is not None else {}), "prices": prices, "price_provenance": price_provenance, "sizing": settings, **expressions}
        finding["evidence_refs"], finding["details"] = refs + ["evidence/calculations.json#/" + volume_id], {"savings_calculated": savings_calculated, "finderReportData": report_data}
        findings.append(finding)
    evidence.files["calculations"] = calculations
    if billing_failure:
        issue = {"kind": billing_failure["kind"], "stage": "costing", "message": "Billing unavailable: " + billing_failure["message"]}
        if billing_failure.get("error"):
            issue["message"] += " %s: %s" % (billing_failure["error"]["code"], billing_failure["error"]["message"])
        issue.update(({"evidence_refs": [billing_failure["ref"]]} if billing_failure.get("ref") else {}), request=billing_failure["request"])  # every collect_billing failure names its read
        issues.append(issue)
        qualifications.append("No billed savings were calculated; %d volumes wait on Cost Explorer. Public baselines stay in the evidence." % waiting_on_billing)

    recommended = [f for f in findings if f["decision"] == "recommended"]
    unassessed = [f["id"] for f in findings if f["decision"] in ("needs_evidence", "error")]
    totals = {"decision_counts": {d: sum(f["decision"] == d for f in findings) for d in ("recommended", "not_recommended", "needs_evidence", "error")},
              "annual_savings": None}
    if recommended:
        totals.update({key: shared_costs[key] for key in ("currency", "basis", "estimation_method")})
        totals.update({field: sum(f["costs"][field] for f in recommended) for field in ("current_annual", "target_annual", "annual_savings")})
    assessment.update(findings=findings, totals=totals, coverage={"inventory_complete": inventory_complete, "discovered_count": len(ids),
                                                                 "assessed_count": len(findings) - len(unassessed), "unassessed_ids": unassessed})
    assessment["run"]["status"] = "complete" if not unassessed and inventory_complete else "partial"
    qualifications += [
        "CloudFix recommendation history was not queried; the finder's RecommendationFinalized row rests on an empty local-adapter assumption.",
        "Unchecked pipeline context: finalized recommendation history, tenant settings/discovery threshold, CUR discovery parity, pipeline persistence/monitoring and change-request revalidation.",
        "Volumes were discovered with a gp2 volume inventory, not CloudFix's billing-data discovery; its cost threshold, region set and tag exclusions were not checked."
        if selection is None else "Only the selected volumes were assessed; this is not an account-wide inventory and CloudFix's discovery conditions were not checked.",
        "The finder does not test workload performance or utilization; workload safety has not been demonstrated.",
        "One-time change costs and first-year savings were not established.",
    ]
    assessment["verification"]["summary"] = ("Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence "
                                             "reference after writing the files. The sizing and cost expressions are recorded per volume in "
                                             "evidence/calculations.json. AWS facts were not re-read.")
    return finish(out_dir, evidence, assessment)

if __name__ == "__main__":
    sys.exit(main())
