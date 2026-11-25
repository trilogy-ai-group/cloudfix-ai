#!/usr/bin/env python3
"""Assess S3 buckets for a Standard and Standard-IA to Intelligent-Tiering lifecycle rule with the customer's own AWS read access.
Standard library only; AWS reads go through the AWS CLI v2. The rules are the ones in ../references/.
Exit 0: complete. Exit 2: the run is partial (evidence missing, a read failed or the self-check found a problem). Exit 1: the run failed."""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import traceback
from decimal import Decimal, ROUND_HALF_UP

# Skill constants the shared skeleton reads.
MODE_FLAG, PRICING_MODES = "--pricing-mode", ("billing", "public_prices", "supplied", "technical_only")
RESOURCE_NOUN, EMPTY_INVENTORY_NOUN = "bucket", "buckets"
REPORT_FOOTER = "Nothing was executed: no lifecycle rule was created, no tag was written and no bucket was changed. These are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`."
ALLOWED_READS = frozenset({("sts", "GetCallerIdentity"), ("s3api", "ListBuckets"), ("s3api", "GetBucketTagging"), ("s3api", "GetBucketLifecycleConfiguration"),
                           ("s3api", "ListBucketIntelligentTieringConfigurations"), ("cloudwatch", "GetMetricData"), ("ce", "GetDimensionValues"), ("ce", "GetCostAndUsage")})  # the finder's assessment reads; aws() refuses anything else

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

SKILL_ID, RESOURCE_TYPE = "co-s3-intelligent-tiering", "AWS::S3::Bucket"
CHECK_NAMES = ("RecommendationFinalized", "ResourceExists", "LifecycleConfigurationReadable", "ResourceFixed", "ControlTower",
               "NoEarlyExpirationRule", "NoEarlyAsyncGlacierTransition", "NoProblematicIntelligentTieringConfiguration",
               "HasObjectMetrics", "NetSavingsAfterObjectFees", "MinimalSavings", "MaximumSavings")
ORDER_8_ALTERNATIVE = "IntelligentTieringConfigurationNotChecked"
FAIL_FAST = ("ResourceExists", "LifecycleConfigurationReadable", "ResourceFixed", "ControlTower", "HasObjectMetrics", "NetSavingsAfterObjectFees")
MINIMAL_SAVINGS, EARLY_DAYS, MIN_OBJECT_SIZE_KB, RULE_NAME = 50, 31, 128, "CloudFix-Standard-To-Sit"
MONITORING_FEE_PER_OBJECT_PER_YEAR, TRANSITION_FEE_PER_OBJECT = (0.0025 / 1000) * 12, 0.01 / 1000
IT_PRICE_FRACTION, SIA_WEIGHT, GIB, QUERY_LIMIT = 0.4185, 0.65, 1024 ** 3, 500
LOOKBACK_DAYS = {"objects": 90, "sizes": 3}  # the billed-size window is [midnight-8d, midnight-1d)
TIERS = (("standardGb", ("StandardStorage",)), ("standardIaGb", ("StandardIAStorage",)), ("onezoneIaGb", ("OneZoneIAStorage",)),
         ("intelligentTieringGb", ("IntelligentTieringFAStorage", "IntelligentTieringIAStorage", "IntelligentTieringAIAStorage",
                                   "IntelligentTieringAAStorage", "IntelligentTieringDAAStorage")),
         ("glacierIrGb", ("GlacierInstantRetrievalStorage",)), ("glacierGb", ("GlacierStorage",)), ("deepArchiveGb", ("DeepArchiveStorage",)))
OVERHEAD_TYPES, ARCHIVE_CLASSES = ("GlacierObjectOverhead", "DeepArchiveObjectOverhead"), ("GLACIER", "DEEP_ARCHIVE", "GLACIER_IR")
BASIS_BY_MODE = {"public_prices": "public_price", "billing": "account_effective_rate", "supplied": "caller_supplied"}
CE_PATTERNS = {"standard": r"^(?:[A-Z0-9]+-)?TimedStorage-ByteHrs$", "standardIa": r"^(?:[A-Z0-9]+-)?TimedStorage-SIA-ByteHrs$"}
GB_MONTH_UNITS = ("gb-mo", "gb-month", "gb-months")
CATALOG_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/%s/index.json"
CATALOG_CLASSES = {"standard": ("Standard", "TimedStorage-ByteHrs"), "standardIa": ("Standard - Infrequent Access", "TimedStorage-SIA-ByteHrs")}
NO_HISTORY = "CloudFix recommendation history was not queried."
METRICS_READ = {"kind": "aws_read", "name": "GetMetricData", "service": "cloudwatch"}
ECONOMICS_INPUT = {"kind": "input", "name": "economics"}

def add_skill_arguments(parser):
    parser.add_argument("--billing-profile", help="AWS CLI profile for Cost Explorer (default: --profile)")
    parser.add_argument("--allow-public-fallback", action="store_true", help="the customer asked for billed costs if possible, otherwise public prices")
    parser.add_argument("--bucket", action="append", help="assess this bucket (repeatable); default: every bucket in the regional listing")
    parser.add_argument("--workers", type=int, default=16, help="concurrent AWS CLI calls for the per-bucket reads (default 16)")

def describe_change(row):
    return "Lifecycle rule `%s`: to Intelligent-Tiering at day 0" % RULE_NAME

def skill_revision():
    with open(os.path.join(SCRIPT_DIR, "..", "SKILL.md"), encoding="utf-8") as handle:
        return re.search(r'^  version: "([^"]+)"', handle.read(), re.M).group(1)

def report_headline(assessment, recommended):
    return "**Savings not established.**" if not recommended else "**%d opportunities found; savings not totaled.**" % len(recommended)

def report_context(assessment):
    scope, coverage, run, totals = assessment["scope"], assessment["coverage"], assessment["run"], assessment["totals"]
    unknown = lambda value: "unknown" if value is None else value
    lines = ["Account %s · %s · %s · %s discovered, %s assessed, %d unassessed · %s · observed %s · %s." % (
        unknown(scope["account_id"]), ", ".join(scope["regions"]), "selected buckets" if scope["resource_ids"] is not None else "every bucket in the regional listing",
        unknown(coverage["discovered_count"]), unknown(coverage["assessed_count"]), len(coverage["unassessed_ids"]), run["status"], run["observed_at"],
        {"self_check": "self-check, no independent review", "independent_review": "independent review", "none": "not verified"}[assessment["verification"]["method"]])]
    included = [row for row in assessment["findings"] if row["decision"] == "recommended" and row["resource"]["id"] not in totals.get("excluded_ids", [])]
    if totals["annual_savings"] is not None and all(row["costs"].get("first_year_savings") is not None for row in included):
        lines.append("First-year savings after one-time transition charges%s: %s." % (
            " for the %d included %s" % (len(included), "bucket" if len(included) == 1 else "buckets") if totals.get("excluded_ids") else "",
            money(sum(row["costs"]["first_year_savings"] for row in included))))
    return lines

def report_notes(assessment):
    return []

def report_bullets(assessment):
    """Each bucket's own notes; the separate billing rows of an excluded bucket are printed by the skeleton after the table."""
    return ["- %s: %s" % (row["id"], text) for row in assessment["findings"] for text in row.get("qualifications", [])]

# --- collection -------------------------------------------------------------------------------------------------------

def fan_out(evidence, file, service, region, profile, jobs, workers):
    """jobs: {key: work}; work(fetch) returns [(record or None, params)] for one read, page by page. Live calls run in
    threads; the records are saved in job order, so live evidence and replay evidence match."""
    saved, out = evidence.files.setdefault(file, []), {}
    def keep(key, pages):
        out[key] = []
        for record, params in pages:
            if record is not None and not evidence.replay and "operation" in record:  # the pager's InvalidPageToken entry is not a read
                saved.append(record)
            out[key].append((record, next((i for i, r in enumerate(saved) if r is record), None), params))
        evidence.replay or evidence.write(file)  # as Evidence.read: a stopped run keeps the jobs collected so far. Only this thread writes; the workers only call aws()
    if evidence.replay:
        fetch = lambda operation, params: evidence.read(file, service, operation, params, region, profile)[0]
        for key, work in jobs.items():
            keep(key, work(fetch))
        return out
    fetch = lambda operation, params: aws(service, operation, params, region, profile)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for key, pages in zip(jobs, pool.map(lambda work: work(fetch), jobs.values())):
            keep(key, pages)
    return out

def paged(operation, params, token_in=None, token_out=None):
    def work(fetch):
        pages, current, seen = [], params, set()
        while True:
            record = fetch(operation, current)
            pages.append((record, current))
            following = None if record is None or "error" in record else record["output"].get(token_out)
            if following is None:
                return pages
            if not isinstance(following, str) or not following or following in seen:  # as Evidence.pages: an empty token is not the end; a repeated one would page without end
                return pages + [({"error": {"code": "InvalidPageToken", "message": "%s returned a repeated or malformed %s; the read is incomplete." % (operation, token_out)}}, current)]
            seen.add(following)
            current = dict(current, **{token_in: following})
    return work

def outcome(pages, file, service, operation, region):
    """One read's pages as (outputs, error, missing_request, refs)."""
    refs = ["evidence/%s.json#/%d" % (file, index) for _, index, _ in pages if index is not None]
    missing = next(({"kind": "aws_read", "name": operation, "service": service, "region": region, "parameters": params} for record, _, params in pages if record is None), None)
    error = next((record["error"] for record, _, _ in pages if record and "error" in record), None)
    return [record["output"] for record, _, _ in pages if record and "output" in record], error, missing, refs

def bucket_reads(evidence, buckets, present, region, profile, workers):
    """Tagging for every selected bucket; lifecycle and Intelligent-Tiering configurations for the ones the listing holds."""
    reads = {}
    for file, operation, scope, tokens in (("tagging", "GetBucketTagging", buckets, ()), ("lifecycle", "GetBucketLifecycleConfiguration", present, ()),
                                            ("intelligent-tiering", "ListBucketIntelligentTieringConfigurations", present, ("ContinuationToken", "NextContinuationToken"))):
        jobs = {bucket: paged(operation, {"Bucket": bucket}, *tokens) for bucket in scope}
        for bucket, pages in fan_out(evidence, file, "s3api", region, profile, jobs, workers).items():
            outputs, error, missing, refs = outcome(pages, file, "s3", operation, region)
            reads.setdefault(bucket, {})[file] = {"outputs": outputs, "error": error, "missing": missing, "refs": refs}
    return reads

def metric_requests(buckets, as_of, billing):
    """The GetMetricData requests: object counts and sizes, plus billed sizes in billing mode; at most 500 queries each, with the id-to-series map of every request."""
    midnight = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    day = datetime.timedelta(days=1)
    sizes = [("BucketSizeBytes", storage_type) for _, types in TIERS for storage_type in types] + [("BucketSizeBytes", t) for t in OVERHEAD_TYPES]
    groups = [("objects", [("NumberOfObjects", "AllStorageTypes")], midnight - LOOKBACK_DAYS["objects"] * day, as_of), ("sizes", sizes, midnight - LOOKBACK_DAYS["sizes"] * day, as_of)]
    if billing:
        groups.append(("billed", [("BucketSizeBytes", "StandardStorage"), ("BucketSizeBytes", "StandardIAStorage")], midnight - 8 * day, midnight - day))
    requests = []
    for group, series, start, end in groups:
        queries = [(bucket, metric, storage_type, "bucket_%d_metric_%d" % (b, s)) for b, bucket in enumerate(buckets) for s, (metric, storage_type) in enumerate(series)]
        for chunk in (queries[i:i + QUERY_LIMIT] for i in range(0, len(queries), QUERY_LIMIT)):
            params = {"StartTime": iso_js(start), "EndTime": iso_js(end), "MetricDataQueries": [
                {"Id": query_id, "MetricStat": {"Metric": {"Namespace": "AWS/S3", "MetricName": metric, "Dimensions": [{"Name": "BucketName", "Value": bucket}, {"Name": "StorageType", "Value": storage_type}]},
                                                "Stat": "Average", "Period": 86400}} for bucket, metric, storage_type, query_id in chunk]}
            requests.append((group, params, {query_id: (bucket, metric, storage_type) for bucket, metric, storage_type, query_id in chunk}, (start, end)))
    return requests

def collect_metrics(evidence, buckets, region, as_of, profile, billing, workers):
    """Every page of every request, merged by id. Returns per bucket {"series": {(group, metric, type): {timestamps, values}}, "windows", "fault", "missing", "refs"}."""
    requests = metric_requests(buckets, as_of, billing)
    jobs = {index: paged("GetMetricData", params, "NextToken", "NextToken") for index, (_, params, _, _) in enumerate(requests)}
    per_bucket = {bucket: {"series": {}, "fault": None, "missing": None, "refs": [], "windows": {}} for bucket in buckets}
    id_map = {}
    for index, pages in fan_out(evidence, "metrics", "cloudwatch", region, profile, jobs, workers).items():
        group, params, ids, window = requests[index]
        outputs, error, missing, refs = outcome(pages, "metrics", "cloudwatch", "GetMetricData", region)
        id_map.update({ref: ids for ref in refs})
        merged, pack_fault = {}, None
        for output in outputs:
            for result in output.get("MetricDataResults", []):
                if result.get("Id") not in ids:
                    pack_fault = "GetMetricData returned a result id that no query of this request asked for."
                    continue
                entry = merged.setdefault(result["Id"], {"timestamps": [], "values": [], "status": None})
                entry["timestamps"] += result.get("Timestamps", [])
                entry["values"] += result.get("Values", [])
                entry["status"] = result.get("StatusCode")
                if result.get("StatusCode") in ("InternalError", "Forbidden"):
                    entry["fault"] = "GetMetricData returned %s for this bucket." % result["StatusCode"]
                elif len(result.get("Values", [])) != len(result.get("Timestamps", [])) or not all(type(value) in (int, float) for value in result.get("Values", [])):
                    entry["fault"] = "GetMetricData returned Values that are not one number per timestamp for this bucket."
        for query_id, (bucket, metric, storage_type) in ids.items():
            holder = per_bucket[bucket]
            holder["refs"] = holder["refs"] + [ref for ref in refs if ref not in holder["refs"]]
            holder["windows"][group] = window
            if missing:
                holder["missing"] = holder["missing"] or missing
            elif error:
                holder["fault"] = holder["fault"] or "GetMetricData failed: %s: %s" % (error["code"], error["message"])
            elif pack_fault:
                holder["fault"] = holder["fault"] or pack_fault
            elif query_id not in merged:
                holder["fault"] = holder["fault"] or "GetMetricData returned no result for %s %s." % (metric, storage_type)
            elif merged[query_id].get("fault") or merged[query_id]["status"] == "PartialData":
                holder["fault"] = holder["fault"] or merged[query_id].get("fault") or "GetMetricData left %s %s PartialData on its final page." % (metric, storage_type)
            else:
                holder["series"][(group, metric, storage_type)] = merged[query_id]
    return per_bucket, id_map

def first_value(series):
    value = (series or {}).get("values", [None])[:1]
    return value[0] if value and isinstance(value[0], (int, float)) and math.isfinite(value[0]) else 0

def reduce_metrics(holder, billing):
    """collection.md's reductions: latest count, first-value tier sizes, archived count, and the 7-day billed means."""
    series = holder["series"]
    count = series.get(("objects", "NumberOfObjects", "AllStorageTypes"))
    points = sorted(zip(count["timestamps"], [v if isinstance(v, (int, float)) else 0 for v in count["values"]])) if count else []
    tiers = {tier: float(to_fixed(sum(first_value(series.get(("sizes", "BucketSizeBytes", t))) for t in types) / GIB, 2)) for tier, types in TIERS}
    archived = math.floor(sum(first_value(series.get(("sizes", "BucketSizeBytes", t))) for t in OVERHEAD_TYPES) / (32 * 1024) + 0.5)
    reduced = {"objectCount": points[-1][1] if points else None, "archivedObjectCount": archived, "tiers": tiers, "fault": None}
    if billing:
        start, end = holder["windows"].get("billed", (None, None))
        for key, storage_type in (("standardGbBilled", "StandardStorage"), ("standardIaGbBilled", "StandardIAStorage")):
            found, seen, days = series.get(("billed", "BucketSizeBytes", storage_type)) or {"timestamps": [], "values": []}, set(), []
            for stamp, value in zip(found["timestamps"], found["values"]):
                if stamp in seen or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    reduced["fault"] = reduced["fault"] or "The %s billed-size series holds a repeated day or an invalid value." % storage_type
                seen.add(stamp)
                if start is not None and start <= parse_time(stamp) < end:
                    days.append(value)
            reduced[key] = float(to_fixed(sum(days) / len(days) / GIB, 2)) if days else 0
            reduced.setdefault("sizeDays", {})["standard" if key == "standardGbBilled" else "standardIa"] = len(days)
    return reduced

# --- lifecycle rules ---------------------------------------------------------------------------------------------------

def is_global(rule):
    filters = rule.get("Filter")
    if filters is None:
        return not rule.get("Prefix")
    if filters.get("Prefix") or filters.get("Tag") is not None:
        return False
    combined = filters.get("And")
    return combined is None or (not combined.get("Prefix") and not combined.get("Tags"))

def early(rule, boundary):
    """(early current-version expiration, early async Glacier transition) of one rule, per checks.md."""
    expiration = rule.get("Expiration") or {}
    before = lambda stamp: stamp is not None and parse_time(stamp) < boundary
    expires = (isinstance(expiration.get("Days"), (int, float)) and 0 < expiration["Days"] < EARLY_DAYS) or before(expiration.get("Date"))
    transitions = any(t.get("StorageClass") in ARCHIVE_CLASSES and ((t.get("Days") is not None and t["Days"] < EARLY_DAYS) or before(t.get("Date")))
                      for t in rule.get("Transitions") or [])
    return bool(expires), transitions

def describe_rule(rule):
    scope = rule.get("Filter") or {}
    where = ("prefix %s" % (scope.get("Prefix") or (scope.get("And") or {}).get("Prefix")) if scope.get("Prefix") or (scope.get("And") or {}).get("Prefix")
             else "tag-scoped" if scope.get("Tag") is not None or (scope.get("And") or {}).get("Tags") else "prefix %s" % rule.get("Prefix") if rule.get("Prefix") else "size-scoped")
    actions = ["expires after %s days" % rule["Expiration"]["Days"] if (rule.get("Expiration") or {}).get("Days") is not None else None,
               ", ".join("to %s at day %s" % (t.get("StorageClass"), t.get("Days", t.get("Date"))) for t in rule.get("Transitions") or []) or None,
               "noncurrent-version actions" if rule.get("NoncurrentVersionExpiration") or rule.get("NoncurrentVersionTransitions") else None,
               "aborts incomplete multipart uploads" if rule.get("AbortIncompleteMultipartUpload") else None]
    return "%s: %s, %s" % (rule.get("ID", "unnamed"), where, "; ".join(a for a in actions if a) or "no current-version action")

# --- economics ---------------------------------------------------------------------------------------------------------

def monthly_storage_cost(size_gb, dimensions):
    """s3MonthlyStorageCost of public-price-model.md: progressive bands from zero to Inf."""
    if not (isinstance(size_gb, (int, float)) and math.isfinite(size_gb) and size_gb >= 0) or not dimensions:
        raise ValueError("Missing or invalid storage quantity/dimensions")
    boundary, cost = 0, 0
    for band in sorted(dimensions, key=lambda d: float(d["begin"])):
        begin, end, rate = float(band["begin"]), math.inf if band["end"] == "Inf" else float(band["end"]), float(band["rate"])
        if begin != boundary or not end > begin or not math.isfinite(rate) or rate < 0:
            raise ValueError("Missing, overlapping or invalid pricing range")
        cost += max(0, min(size_gb, end) - begin) * rate
        boundary = end
    if boundary != math.inf:
        raise ValueError("Incomplete pricing ranges")
    return cost

def storage_model(standard_gb, standard_ia_gb, standard_dimensions, sia_dimensions):
    """s3PublicStorageModel of public-price-model.md, with the finder's fixed 0.4185 and 0.65 coefficients."""
    total = standard_gb + standard_ia_gb
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 for x in (standard_gb, standard_ia_gb)) or not total > 0:
        raise ValueError("No supported transitionable payload")
    annual = 12 * (monthly_storage_cost(standard_gb, standard_dimensions) + monthly_storage_cost(standard_ia_gb, sia_dimensions))
    fraction = 1 - IT_PRICE_FRACTION / (standard_gb / total + SIA_WEIGHT * standard_ia_gb / total)
    return {"annualIncludedStorageCost": annual, "modelSavingFraction": fraction, "grossSavings": annual * fraction}

def estimate_fees(gross, objects, archived, transitionable_gb, intelligent_tiering_gb, existing_fee):
    """estimateNetSavingsAfterObjectFees of costing.md, byte for byte in Python."""
    monitored = min(existing_fee / MONITORING_FEE_PER_OBJECT_PER_YEAR, (intelligent_tiering_gb * 1024 * 1024) / MIN_OBJECT_SIZE_KB)
    candidates = objects - archived - monitored
    average_kb = (transitionable_gb * 1024 * 1024) / candidates if candidates > 0 else 0
    monitoring = max(0, candidates) * MONITORING_FEE_PER_OBJECT_PER_YEAR
    share = min(1, MIN_OBJECT_SIZE_KB / average_kb) if average_kb > 0 else 1
    net = min(gross - monitoring, gross * (1 - share))
    transition = max(0, candidates) * TRANSITION_FEE_PER_OBJECT
    return {"objectCount": objects, "transitionableSizeGb": transitionable_gb, "monitoredObjectCount": monitored, "candidateObjectCount": candidates,
            "averageObjectSizeKb": average_kb, "existingMonitoringFee": existing_fee, "monitoringFee": monitoring, "smallObjectShare": share,
            "netSavings": net, "transitionCharge": transition, "firstYearNetSavings": net - transition}

def modeled_row(standard_gb, standard_ia_gb, dimensions, basis, allocation):
    model = storage_model(standard_gb, standard_ia_gb, dimensions["standard"], dimensions["standardIa"])
    cost, gross = model["annualIncludedStorageCost"], model["grossSavings"]
    return {"basis": basis, "costScope": "Standard+Standard-IA payload storage", "nativeCurEquivalent": False, "tierAllocation": allocation,
            "annualCost": cost, "publicAnnualCost": cost if basis == "public-price-model" else None, "dataCosts": cost, "totalSavings": gross, "dataSavings": gross,
            "totalSavingsPercentage": model["modelSavingFraction"], "dataSavingsPercentage": model["modelSavingFraction"],
            "stdGbMonths": standard_gb, "iaGbMonths": standard_ia_gb, "gbMonths": standard_gb + standard_ia_gb, "girGbMonths": 0, "extraSavings": 0, "intMonitoringCosts": 0}

def read_catalog(evidence, region):
    """The regional public S3 catalog: one SKU and one OnDemand term per class, with every price dimension. Saved whole."""
    catalog = (evidence.files.get("economics") or {}).get("catalog")
    if catalog is None and evidence.replay:
        return None, "The public catalog read was not supplied."
    if catalog is None:
        url = CATALOG_URL % region  # a fixed https host and path; only the validated region code varies
        catalog = {"url": url, "retrieved_at": iso_js(datetime.datetime.now(datetime.timezone.utc))}
        try:  # curl ships with macOS, Windows 10+ and the Linux images that carry the AWS CLI; no IAM action is involved
            if not re.fullmatch(r"[a-z]{2}(?:-gov|-iso[a-z]?)?-[a-z]+-\d", region):
                raise ValueError("it needs an AWS region code; got %r." % region)
            done = subprocess.run(["curl", "-fsS", "--max-time", "120", url], capture_output=True, timeout=130)
            if done.returncode != 0:
                raise OSError(done.stderr.decode(errors="replace").strip() or "curl exit %d" % done.returncode)
            index = json.loads(done.stdout)
            if not isinstance(index, dict):
                raise ValueError("the answer is not a JSON object")
            catalog.update(sha256=hashlib.sha256(done.stdout).hexdigest(), version=index.get("version"), publicationDate=index.get("publicationDate"), selection={}, raw=index)
        except (OSError, ValueError, subprocess.TimeoutExpired) as fault:
            catalog["error"] = {"code": "CatalogReadFailed", "message": str(fault)}  # a failed read is evidence too: a replay answers it as the live run did
        evidence.files.setdefault("economics", {})["catalog"] = catalog
    if isinstance(catalog, dict) and isinstance(catalog.get("error"), dict):
        return None, "The public catalog read failed: %s" % catalog["error"].get("message")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("raw"), dict):
        raise InputError("evidence/economics.json#/catalog needs the saved catalog document in raw")
    index, problems, catalog["selection"] = catalog["raw"], [], {}
    for name, (volume_type, usage_suffix) in CATALOG_CLASSES.items():
        skus = [sku for sku, product in index.get("products", {}).items()
                if product.get("productFamily") == "Storage" and product.get("attributes", {}).get("servicecode") == "AmazonS3"
                and product["attributes"].get("regionCode") == region and product["attributes"].get("operation", "") == ""
                and product["attributes"].get("volumeType") == volume_type and product["attributes"].get("usagetype", "").endswith(usage_suffix)]
        terms = list((index.get("terms", {}).get("OnDemand", {}).get(skus[0]) or {}).items()) if len(skus) == 1 else []
        dimensions = [{"begin": d["beginRange"], "end": d["endRange"], "rate": d["pricePerUnit"].get("USD"), "rateCode": code}
                      for code, d in sorted(terms[0][1].get("priceDimensions", {}).items())] if len(terms) == 1 else []
        if len(skus) != 1 or len(terms) != 1 or not dimensions or any(d["rate"] is None or terms[0][1]["priceDimensions"][d["rateCode"]].get("unit") != "GB-Mo" for d in dimensions):
            problems.append("%s: %d matching SKUs, %d OnDemand terms" % (volume_type, len(skus), len(terms)))
        catalog["selection"][name] = {"sku": skus[0] if len(skus) == 1 else None, "term": terms[0][0] if len(terms) == 1 else None,
                                      "effectiveDate": terms[0][1].get("effectiveDate") if len(terms) == 1 else None, "dimensions": dimensions}
    return ({name: entry["dimensions"] for name, entry in catalog["selection"].items()}, None) if not problems else (None, "Public catalog selection is missing or ambiguous: " + "; ".join(problems))

def collect_cost_explorer(evidence, account, region, as_of, profile):
    """billing-cost-explorer.md steps 1 to 6. Returns (rates, failure): rates has per class {rate or None, ...} and the window."""
    midnight = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    window = {"Start": (midnight - datetime.timedelta(days=32)).strftime("%Y-%m-%d"), "End": (midnight - datetime.timedelta(days=1)).strftime("%Y-%m-%d")}
    common = [{"Dimensions": {"Key": key, "Values": [value]}} for key, value in (("LINKED_ACCOUNT", account), ("REGION", region), ("SERVICE", "Amazon Simple Storage Service"), ("RECORD_TYPE", "Usage"))]
    identity, position = evidence.read("cost-explorer", "sts", "GetCallerIdentity", {}, "us-east-1", profile)
    if identity is None or "error" in identity:
        return None, {"kind": "missing_evidence" if identity is None else "read_error", "message": "The billing identity could not be verified.", "error": (identity or {}).get("error"),
                      "ref": evidence.ref("cost-explorer", 0) if identity else None, "request": position if identity is None else {"kind": "aws_read", "name": "GetCallerIdentity", "service": "sts", "region": "us-east-1"}}
    def fail(operation, message, failure, pages):
        request = failure["missing"] if failure and failure.get("missing") else {"kind": "aws_read", "name": operation, "service": "ce", "region": "us-east-1"}
        return None, {"kind": "missing_evidence" if failure and failure.get("missing") else "read_error", "message": message, "error": (failure or {}).get("error"),
                      "ref": failure["ref"] if failure and failure.get("ref") else (evidence.ref("cost-explorer", pages[-1][1]) if pages else None), "request": request}
    values = {"TimePeriod": window, "Dimension": "USAGE_TYPE", "Context": "COST_AND_USAGE", "SearchString": "TimedStorage", "Filter": {"And": common}}
    pages, failure = evidence.pages("cost-explorer", "ce", "GetDimensionValues", values, "us-east-1", profile, token="NextPageToken")
    if failure:
        return fail("GetDimensionValues", "GetDimensionValues %s." % ("is incomplete" if failure.get("missing") else "failed"), failure, pages)
    listed = [record["output"].get("DimensionValues", []) for record, _ in pages]
    returned = [item for page in listed if isinstance(page, list) for item in page]
    if not all(isinstance(page, list) for page in listed) or not all(isinstance(item, dict) and isinstance(item.get("Value"), str) for item in returned):
        return fail("GetDimensionValues", "GetDimensionValues returned invalid data: DimensionValues is not a list of values that each hold a Value.", None, pages)
    retained = {name: [item["Value"] for item in returned if re.match(pattern, item["Value"])] for name, pattern in CE_PATTERNS.items()}
    excluded = {item["Value"]: "not Standard or Standard-IA payload storage" for item in returned if not any(item["Value"] in kept for kept in retained.values())}
    units = {item["Value"]: (item.get("Attributes") or {}).get("unit") for item in returned}
    rates = {"window": window, "identity": identity["output"].get("Arn"), "returned": [item["Value"] for item in returned], "retained": retained, "excluded": excluded,
             "refs": [evidence.ref("cost-explorer", 0)] + [evidence.ref("cost-explorer", index) for _, index in pages], "classes": {}}
    wanted = sorted(set(retained["standard"] + retained["standardIa"]))
    if not wanted:
        rates["classes"] = {name: {"usageTypes": [], "rate": None, "reason": "no usage type matched the class pattern; returned: %s" % (", ".join(rates["returned"]) or "nothing")} for name in CE_PATTERNS}
        return rates, None
    usage = {"TimePeriod": window, "Granularity": "DAILY", "Metrics": ["NetUnblendedCost", "UnblendedCost", "UsageQuantity"], "GroupBy": [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
             "Filter": {"And": common + [{"Dimensions": {"Key": "USAGE_TYPE", "Values": wanted}}]}}
    pages, failure = evidence.pages("cost-explorer", "ce", "GetCostAndUsage", usage, "us-east-1", profile, token="NextPageToken")
    rates["refs"] += [evidence.ref("cost-explorer", index) for _, index in pages]
    if failure:
        return fail("GetCostAndUsage", "GetCostAndUsage %s." % ("is incomplete" if failure.get("missing") else "failed"), failure, pages)
    totals, days, estimated, invalid = {name: {"selectedCost": Decimal(0), "unblendedCost": Decimal(0), "usageGbMonths": Decimal(0), "netFallbackGroups": 0, "daysWithUsage": 0} for name in CE_PATTERNS}, set(), 0, []
    def objects(holder, key, where):
        found = holder.get(key, [])
        if isinstance(found, list) and all(isinstance(item, dict) for item in found):
            return found
        invalid.append("%s%s is not a list of objects" % (where, key))
        return []
    for record, _ in pages:
        for result in objects(record["output"], "ResultsByTime", ""):
            start, end = (result.get("TimePeriod") or {}).get("Start"), (result.get("TimePeriod") or {}).get("End")
            try:
                one_day = window["Start"] <= start and end <= window["End"] and datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start) == datetime.timedelta(days=1)
            except (TypeError, ValueError):
                one_day = False
            if not one_day or start in days or not isinstance(result.get("Estimated"), bool):
                invalid.append("day %s is outside the window, repeated or not a one-day result" % start)
            days.add(start)
            estimated += 1 if result.get("Estimated") else 0
            seen = set()
            for group in objects(result, "Groups", "day %s: " % start):
                key = (group.get("Keys") or [None])[0]
                name = next((n for n, kept in retained.items() if key in kept), None)
                if len(group.get("Keys") or []) != 1 or name is None or key in seen:
                    invalid.append("day %s: group %s is not one retained usage type" % (start, group.get("Keys")))
                    continue
                seen.add(key)
                metrics = group.get("Metrics") or {}
                try:
                    net, gross, quantity = metrics.get("NetUnblendedCost"), metrics["UnblendedCost"], metrics["UsageQuantity"]
                    fallback = net is None or ("Amount" in net and net["Amount"] is None)  # billing-cost-explorer.md step 5: only an absent or null net metric, or a null Amount, falls back
                    unit = (quantity.get("Unit") or "N/A")
                    if unit.lower() not in GB_MONTH_UNITS and (unit != "N/A" or (units.get(key) or "").lower() not in GB_MONTH_UNITS):
                        raise ValueError("usage unit %s is not GB-months" % unit)
                    if unit != "N/A" and units.get(key) and units[key].lower() != unit.lower():
                        raise ValueError("usage unit %s disagrees with the dimension unit %s" % (unit, units[key]))
                    amount = Decimal((gross if fallback else net)["Amount"])
                    gross_amount, quantity_amount = Decimal(gross["Amount"]), Decimal(quantity["Amount"])
                    if not all(x.is_finite() for x in (amount, gross_amount, quantity_amount)) or quantity_amount < 0 or (quantity_amount == 0 and (amount != 0 or gross_amount != 0)):
                        raise ValueError("non-finite, negative or zero-usage-with-cost amounts")
                    if (gross if fallback else net).get("Unit") != "USD" or gross.get("Unit") != "USD":
                        raise ValueError("cost unit is not USD")
                except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as fault:
                    invalid.append("day %s %s: %s" % (start, key, fault))
                    continue
                totals[name]["selectedCost"] += amount
                totals[name]["unblendedCost"] += gross_amount
                totals[name]["usageGbMonths"] += quantity_amount
                totals[name]["netFallbackGroups"] += 1 if fallback else 0
                totals[name]["daysWithUsage"] += 1
    invalid += ["%s: the summed cost %s is negative" % (name, total["selectedCost"]) for name, total in totals.items() if total["selectedCost"] < 0]
    if invalid:
        return fail("GetCostAndUsage", "GetCostAndUsage returned invalid data: " + "; ".join(invalid[:5]), None, pages)
    for name, total in totals.items():
        usable = total["usageGbMonths"] > 0
        rates["classes"][name] = dict({key: float(value) if isinstance(value, Decimal) else value for key, value in total.items()}, usageTypes=retained[name],
                                      rate=float(total["selectedCost"] / total["usageGbMonths"]) if usable else None,
                                      reason=None if usable else "no positive usage in the window for %s" % (", ".join(retained[name]) or "any matching usage type"))
    rates.update(daysRequested=31, daysReturned=len(days), daysMissing=31 - len(days), estimatedDays=estimated)
    return rates, None

# --- analysis ------------------------------------------------------------------------------------------------------------

def coverage_reason(reduced, estimate, resource):
    """costing.md: the metric gaps, then the first matching reason; None when coverage passes. Raises KeyError when a
    step needs an economic input the row does not have."""
    n, a, tiers = reduced["objectCount"], reduced["archivedObjectCount"], reduced["tiers"]
    t, total = tiers["standardGb"] + tiers["standardIaGb"], sum(tiers.values())
    gaps = [gap for gap, hit in (("number-of-objects-missing", n is None), ("number-of-objects-zero", n is not None and n <= 0), ("bucket-size-zero", not total > 0)) if hit]
    if gaps:
        return "cloudwatch-metric-gap", gaps
    if tiers["glacierGb"] + tiers["deepArchiveGb"] > 0 and a == 0:
        return "archived-object-overhead-missing", gaps
    if t <= 0:
        if resource is None:
            raise KeyError("economics")
        return ("transitionable-bytes-missing" if resource["dataSavings"] > 0 and (resource["stdGbMonths"] > 0 or (resource["iaGbMonths"] > 0 and tiers["onezoneIaGb"] <= 0)) else None), gaps
    if (n or 0) - a < 1:
        return "no-objects-outside-archive", gaps
    if estimate is None:
        raise KeyError("economics")
    if estimate["candidateObjectCount"] < 1:
        return "monitored-objects-reach-object-count", gaps
    return None, gaps

def assess_row(bucket, present, reads, reduced, resource, boundary):
    """Orders 1 to 12 for one economic row. Returns (checks, stopped, estimate, qualifications, exception).
    checks: [(name, status, value, reason)] ending where the row stopped."""
    checks, quals = [("RecommendationFinalized", "not_evaluated", None, NO_HISTORY)], []
    def stop(name, status, value, reason):
        checks.append((name, status, value, reason))
        return checks, True, None, quals, None
    if present is None:
        return stop("ResourceExists", "not_evaluated", None, "Not run: a page of the bucket listing is missing.")
    if not present:
        return stop("ResourceExists", "fail", {"resourceId": bucket}, "The regional bucket listing does not hold %s." % bucket)
    checks.append(("ResourceExists", "pass", {"resourceId": bucket}, None))
    lifecycle, config = reads.get("lifecycle", {}), reads.get("intelligent-tiering", {})
    error = lifecycle.get("error")
    if error and (error["code"] == "NoSuchBucket" or (error["code"] == "AccessDenied" and "resource-based policy" in error["message"])):
        return stop("LifecycleConfigurationReadable", "fail", {"resourceId": bucket, "error": "%s: %s" % (bucket, error["message"])}, "The lifecycle configuration of %s is not readable: %s." % (bucket, error["code"]))
    if (error and error["code"] != "NoSuchLifecycleConfiguration") or lifecycle.get("missing"):
        return stop("LifecycleConfigurationReadable", "not_evaluated", None, "Not run: the lifecycle read %s." % ("failed: %s" % error["code"] if error else "was not supplied"))
    checks.append(("LifecycleConfigurationReadable", "pass", {"resourceId": bucket}, None))
    rules = [rule for output in lifecycle.get("outputs", []) for rule in output.get("Rules") or [] if rule.get("Status") is not None]
    enabled = [rule for rule in rules if rule.get("Status") != "Disabled"]
    global_rules = [rule for rule in enabled if is_global(rule)]
    fixed = next((rule for rule in global_rules if rule.get("Transitions") and (rule.get("ID") == RULE_NAME or any(t.get("StorageClass") == "INTELLIGENT_TIERING" for t in rule["Transitions"]))), None)
    if fixed:
        return stop("ResourceFixed", "fail", {"globalRuleCount": len(global_rules), "intelligentTieringRuleId": fixed.get("ID")}, ("An enabled bucket-wide lifecycle rule (%s) already transitions to Intelligent-Tiering." if any(t.get("StorageClass") == "INTELLIGENT_TIERING" for t in fixed["Transitions"])
                     else "An enabled bucket-wide lifecycle rule with transitions carries the CloudFix rule ID %s, which the finder reads as already fixed.") % fixed.get("ID"))
    checks.append(("ResourceFixed", "pass", {"globalRuleCount": len(global_rules)}, None))
    if "aws-controltower" in bucket:
        return stop("ControlTower", "fail", {"bucketName": bucket}, "The bucket name contains aws-controltower.")
    checks.append(("ControlTower", "pass", {"bucketName": bucket}, None))
    expiring = [rule for rule in global_rules if early(rule, boundary)[0]]
    checks.append(("NoEarlyExpirationRule", "fail" if expiring else "pass", {"earlyExpirationRuleCount": len(expiring), "earlyExpirationRuleIds": [r["ID"] for r in expiring if r.get("ID") is not None], "minExpirationDays": EARLY_DAYS},
                   "An enabled bucket-wide rule expires current objects before day %d." % EARLY_DAYS if expiring else None))
    archiving = [rule for rule in global_rules if early(rule, boundary)[1]]
    checks.append(("NoEarlyAsyncGlacierTransition", "fail" if archiving else "pass", {"earlyTransitionRuleCount": len(archiving), "earlyTransitionRuleIds": [r["ID"] for r in archiving if r.get("ID") is not None], "minTransitionDays": EARLY_DAYS},
                   "An enabled bucket-wide rule moves current objects to an archive class before day %d." % EARLY_DAYS if archiving else None))
    outside = [describe_rule(rule) for rule in enabled if not is_global(rule)]
    if outside:
        quals.append("%d enabled lifecycle rule%s outside the bucket-wide checks (%s). Not tested as safe; review before any change." % (len(outside), "" if len(outside) == 1 else "s", "; ".join(outside)))
    if config.get("error") and "AccessDenied" in config["error"]["code"]:
        checks.append((ORDER_8_ALTERNATIVE, "not_evaluated", {"error": config["error"]["code"]}, "The Intelligent-Tiering configuration read was denied; archive tiers were not checked."))
        quals.append("Intelligent-Tiering configurations were not checked: %s." % config["error"]["code"])
    elif (config.get("error") and config["error"]["code"] not in ("NoSuchConfiguration", "NoSuchBucket")) or config.get("missing"):
        return stop("NoProblematicIntelligentTieringConfiguration", "not_evaluated", None, "Not run: the Intelligent-Tiering configuration read %s." % ("failed: %s" % config["error"]["code"] if config.get("error") else "was not supplied"))
    else:
        configurations = [c for output in config.get("outputs", []) for c in output.get("IntelligentTieringConfigurationList") or [] if all(c.get(k) is not None for k in ("Id", "Status", "Tierings"))]
        tiers = list(dict.fromkeys(t["AccessTier"] for c in configurations if c["Status"] == "Enabled" for t in c["Tierings"] if t.get("AccessTier") in ("ARCHIVE_ACCESS", "DEEP_ARCHIVE_ACCESS")))
        checks.append(("NoProblematicIntelligentTieringConfiguration", "pass", {"intelligentTieringConfigurationCount": len(configurations), "enabledArchiveAccessTiers": tiers}, None))
        if tiers:
            quals.append("Enabled Intelligent-Tiering archive tiers %s on %s: retrieval takes 3 to 5 hours (ARCHIVE_ACCESS) or 12 hours or more (DEEP_ARCHIVE_ACCESS)." % (", ".join(tiers), bucket))
    tags = reads.get("tagging", {})
    if tags.get("error") and tags["error"]["code"] != "NoSuchTagSet":
        quals.append("Tags were not read: %s." % tags["error"]["code"])
    elif any(tag.get("Key") == "dont_fix_it" for output in tags.get("outputs", []) for tag in output.get("TagSet") or []):
        quals.append("The bucket carries a dont_fix_it tag: a user constraint to surface before any proposal.")
    if reduced is None:
        return stop("HasObjectMetrics", "not_evaluated", None, "The GetMetricData responses for this bucket were not supplied.")
    if reduced["fault"]:
        exception = {"context": "Validation error: ", "error": reduced["fault"]}
        checks.append(("HasObjectMetrics", "not_evaluated", None, "Validation error: " + reduced["fault"]))
        return checks, True, None, quals, exception
    tiers, n, a = reduced["tiers"], reduced["objectCount"], reduced["archivedObjectCount"]
    t = tiers["standardGb"] + tiers["standardIaGb"]
    estimate = estimate_fees(resource["totalSavings"], n or 0, a, t, tiers["intelligentTieringGb"], resource["intMonitoringCosts"] if isinstance(resource["intMonitoringCosts"], (int, float)) and math.isfinite(resource["intMonitoringCosts"]) else 0) if resource else None
    value = dict({"metricGaps": []}, **({"objectCount": n} if n is not None else {}), archivedObjectCount=a, totalSizeGb=sum(tiers.values()), transitionableSizeGb=t)
    if estimate:
        value.update(monitoredObjectCount=estimate["monitoredObjectCount"], candidateObjectCount=estimate["candidateObjectCount"],
                     curDataSavingsUsd=resource["dataSavings"], curStdGbMonths=resource["stdGbMonths"], curIaGbMonths=resource["iaGbMonths"])
    try:
        reason, gaps = coverage_reason(reduced, estimate, resource)
    except KeyError:
        return stop("HasObjectMetrics", "not_evaluated", value, "Not run: the economic row this check needs is missing.")
    value["metricGaps"] = gaps
    if reason:
        return stop("HasObjectMetrics", "fail", dict(value, reason=reason), "Object metrics do not support the estimate: %s." % reason)
    checks.append(("HasObjectMetrics", "pass", value, None))
    fee_value = {"transitionableSizeGb": t, "candidateObjectCount": estimate["candidateObjectCount"], "averageObjectSizeKb": estimate["averageObjectSizeKb"], "minObjectSizeKb": MIN_OBJECT_SIZE_KB,
                 "smallObjectFraction": estimate["smallObjectShare"], "annualMonitoringFeeUsd": estimate["monitoringFee"], "annualNetSavingsUsd": estimate["netSavings"],
                 "transitionChargeUsd": estimate["transitionCharge"], "firstYearNetSavingsUsd": estimate["firstYearNetSavings"]}
    if not estimate["firstYearNetSavings"] > 0:
        return checks + [("NetSavingsAfterObjectFees", "fail", fee_value, "First-year net savings after object fees are not positive (%s)." % js_number(estimate["firstYearNetSavings"]))], True, estimate, quals, None
    checks.append(("NetSavingsAfterObjectFees", "pass", fee_value, None))
    savings, cost = estimate["netSavings"], resource["annualCost"]
    not_finite = {"reason": "annual-savings-not-finite"} if not math.isfinite(savings) else {}
    checks.append(("MinimalSavings", "pass" if savings > MINIMAL_SAVINGS else "fail", dict({"annualSavings": savings, "annualCost": cost, "minimalSavings": MINIMAL_SAVINGS}, **not_finite),
                   None if savings > MINIMAL_SAVINGS else "Annual savings after object fees (%s) are not above the %d USD minimum." % (money(savings) if math.isfinite(savings) else savings, MINIMAL_SAVINGS)))
    cost_reason = {"reason": "annual-cost-not-finite"} if math.isfinite(savings) and not math.isfinite(cost) else not_finite
    checks.append(("MaximumSavings", "pass" if savings <= cost else "fail", dict({"annualSavings": savings, "annualCost": cost}, **cost_reason),
                   None if savings <= cost else "Annual savings (%s) exceed the annual cost (%s)." % (js_number(savings), js_number(cost))))
    return checks, False, estimate, quals, None

def check_rows(checks):
    return [dict({"name": name, "status": status}, **({"value": value} if value is not None else {}), **({"reason": reason} if reason else {})) for name, status, value, reason in checks]

NATIVE_FIELDS = ("publicAnnualCost", "annualCost", "totalSavings", "extraSavings", "dataSavings", "totalSavingsPercentage", "dataSavingsPercentage",
                 "girGbMonths", "iaGbMonths", "stdGbMonths", "gbMonths", "dataCosts", "intMonitoringCosts")

# The amounts orders 9 to 12 read. intMonitoringCosts is absent on purpose: costing.md gives it a zero credit when it is missing.
REQUIRED_AMOUNTS = ("annualCost", "totalSavings", "dataSavings", "stdGbMonths", "iaGbMonths")

def parse_float(value):
    """JavaScript's Number.parseFloat for a supplied field: the leading decimal number of a string, NaN when it has none.
    A JSON number is already parsed."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except OverflowError:  # an integer beyond the float range is Infinity in JavaScript
            return math.inf if value > 0 else -math.inf
    # ASCII digits and JavaScript's white space only: Python's \d and \s also match other scripts' digits and separators.
    match = re.match("[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*([+-]?(?:Infinity|(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?))", value) if isinstance(value, str) else None
    return float(match.group(1).replace("Infinity", "inf")) if match else math.nan

def native_row(row):
    """A supplied native economic row, parsed as the finder does: Number.parseFloat with NaN to 0; the missing and the
    malformed fields stay named beside their zeros."""
    parsed = dict(row, missingFields=[field for field in NATIVE_FIELDS if row.get(field) is None], malformedFields=[])
    for field in NATIVE_FIELDS:
        value = parse_float(row.get(field))
        if math.isinf(value):
            raise InputError("The supplied economic row for %s has a non-finite %s; supply a finite amount." % (row.get("resourceId"), field))
        if math.isnan(value) and row.get(field) is not None:
            parsed["malformedFields"].append(field)
        parsed[field] = 0 if math.isnan(value) else value
    return parsed

# --- the run -------------------------------------------------------------------------------------------------------------

def run(args):
    issues, qualifications, calculations = [], [], {}
    if args.replay:
        evidence = Evidence(args.replay, True)
        saved = evidence.files.get("run-metadata") if isinstance(evidence.files.get("run-metadata"), dict) else {}
        metadata, request = saved.get("run_metadata"), saved.get("request")
        if not metadata or not request:
            sys.stderr.write("The replay folder has no run-metadata.json\n")
            return 1
        evidence.directory = os.path.join(args.out, "evidence")
    else:
        if args.pricing_mode == "public_prices" or (args.pricing_mode == "billing" and args.allow_public_fallback):
            try:  # the public catalog is read with curl; its absence must show before the AWS reads, not after them
                subprocess.run(["curl", "--version"], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise InputError("curl is required to read the public AWS price list in this mode (no curl on PATH). Install it, choose another --pricing-mode, or follow references/guided.md.")
        evidence = Evidence(os.path.join(args.out, "evidence"), False)
        metadata = {"skill_revision": None, "accountId": args.account, "region": args.region, "asOf": iso_js(parse_time(args.as_of) if args.as_of else datetime.datetime.now(datetime.timezone.utc)), "evaluation_timezone": "UTC",
                    "configuration": {"minimalSavings": MINIMAL_SAVINGS, "minTransitionDays": EARLY_DAYS}, "run_classification": "standard", "public_price_fallback_allowed": bool(args.allow_public_fallback)}
        request = {"pricing_mode": args.pricing_mode, "resource_ids": args.bucket, "allow_public_fallback": bool(args.allow_public_fallback), "profile": args.profile, "billing_profile": args.billing_profile, "workers": args.workers}
        evidence.files["run-metadata"] = {"run_metadata": metadata, "request": request}
        if args.pricing_mode == "supplied":
            if not args.costs:
                sys.stderr.write("--pricing-mode supplied needs --costs FILE\n")
                return 1
            try:
                with open(args.costs, encoding="utf-8") as handle:
                    evidence.files["economics"] = {"supplied": json.load(handle)}
            except (OSError, ValueError) as fault:
                raise InputError("--costs could not be read as JSON: %s" % fault)
    try:
        as_of, account, region = parse_time(metadata["asOf"]), metadata["accountId"], metadata["region"]
        pricing_mode, selection, profile, workers = request["pricing_mode"], request.get("resource_ids"), request.get("profile"), request.get("workers") or 16
        if pricing_mode not in PRICING_MODES or not all(isinstance(text, str) for text in [account, region] + (selection or [])) or not (selection is None or isinstance(selection, list)):
            raise ValueError(pricing_mode)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise InputError("evidence/run-metadata.json needs run_metadata with an ISO 8601 asOf, accountId and region, and a request with a pricing_mode of %s and resource_ids null or a list of bucket names" % ", ".join(PRICING_MODES))
    assessment = {
        "schema_version": "1.0", "skill": {"id": SKILL_ID, "revision": skill_revision(), "source_ref": None},
        "run": {"observed_at": metadata["asOf"], "mode": "local_code", "status": "complete"},
        "scope": {"account_id": None, "regions": [region], "resource_ids": selection and list(dict.fromkeys(selection)), "pricing_mode": pricing_mode},  # an id named twice is one id
        "coverage": {"inventory_complete": False, "discovered_count": None, "assessed_count": None, "unassessed_ids": []},
        "run_metadata": metadata, "findings": [], "totals": {"decision_counts": {"recommended": 0, "not_recommended": 0, "needs_evidence": 0, "error": 0}, "annual_savings": None},
        "issues": issues, "qualifications": qualifications, "verification": {"method": "self_check", "summary": "The run stopped before analysis; the saved counts and evidence references were self-checked."},
        "evidence_refs": ["evidence/run-metadata.json", "evidence/identity.json#/0"], "execution_status": "not_executed"}

    def close():
        assessment["evidence_refs"] += ["evidence/list-buckets.json"] if evidence.files.get("list-buckets") else []
        assessment["evidence_refs"] += ["evidence/economics.json#/" + key for key in ("catalog", "cost_explorer_effective_rate", "supplied") if key in (evidence.files.get("economics") or {})]
        return finish(args.out, evidence, assessment)
    def failed(stage, message, failure=None):
        issue = {"kind": "read_error", "stage": stage, "message": message}
        if failure and failure.get("error"):
            issue["message"] += " %s: %s" % (failure["error"]["code"], failure["error"]["message"])
            issue["evidence_refs"] = [failure["ref"]]
        if failure and failure.get("missing"):
            issue["kind"], issue["request"] = "missing_evidence", failure["missing"]
        issues.append(issue)
        assessment["run"]["status"] = "failed"
        return close()
    supplied = (evidence.files.get("economics") or {}).get("supplied") if pricing_mode == "supplied" else None
    if pricing_mode == "supplied" and not (isinstance(supplied, dict) and isinstance(supplied.get("resources"), list) and all(isinstance(row, dict) for row in supplied["resources"])):
        return failed("scope", "The supplied economics file needs a resources list of native economic rows.", {"missing": ECONOMICS_INPUT})
    if pricing_mode == "supplied" and supplied.get("currency") != "USD":
        raise InputError('The supplied economics file needs "currency": "USD"; this script converts no other currency.')
    # 1. identity
    identity, position = evidence.read("identity", "sts", "GetCallerIdentity", {}, region, profile)
    if identity is None:  # a replay without the identity read: no record to point at
        assessment["evidence_refs"].remove("evidence/identity.json#/0")
    if identity is not None and "error" not in identity and not isinstance(identity.get("output"), dict):
        raise InputError("evidence/identity.json#/0 holds neither an output object nor an error")
    if identity is None or "error" in identity:
        return failed("scope", "Identity could not be verified.", {"error": identity["error"], "ref": evidence.ref("identity", 0)} if identity else {"missing": position})
    if identity["output"].get("Account") != account:
        return failed("scope", "The credentials belong to account %s, not the requested %s." % (identity["output"].get("Account"), account))
    assessment["scope"]["account_id"] = account
    # 2. the regional listing, then the per-bucket reads and the metrics
    pages, failure = evidence.pages("list-buckets", "s3api", "ListBuckets", {"BucketRegion": region}, region, profile, token="ContinuationToken")
    if not failure and not all(isinstance(record["output"].get("Buckets", []), list) for record, _ in pages):
        failure = {"error": {"code": "InvalidData", "message": "Buckets is not a list."}, "ref": evidence.ref("list-buckets", pages[-1][1])}
    if failure and "error" in failure:
        for bucket in selection or []:
            assessment["findings"].append({"id": bucket, "resource": {"id": bucket, "type": RESOURCE_TYPE, "region": region}, "decision": "error",
                                           "checks": check_rows([(name, "not_evaluated", None, "Not run: the bucket listing was denied or failed.") for name in CHECK_NAMES[:1]]), "costs": {"status": "not_calculated"}})
        assessment["totals"]["decision_counts"]["error"] = len(assessment["findings"])
        assessment["coverage"].update(discovered_count=len(selection or []), assessed_count=0, unassessed_ids=list(selection or []))
        return failed("collection", "The bucket listing was denied or failed.", failure)
    listed = {bucket["Name"]: bucket for record, _ in pages for bucket in record["output"].get("Buckets") or [] if bucket.get("Name") is not None}
    inventory_complete = failure is None
    if failure:
        issues.append({"kind": "missing_evidence", "stage": "collection", "message": "A page of the bucket listing is missing.", "request": failure["missing"]})
    # guided.md section 1: a supplied row selects and prices its bucket; a row that names another account or region is not used, and is named once
    supplied_rows = [row for row in (supplied or {"resources": []})["resources"] if row.get("awsAccount") in (None, account) and row.get("awsRegion") in (None, region)]
    unused = [str(row.get("resourceId")) for row in (supplied or {"resources": []})["resources"] if row not in supplied_rows]
    if unused:  # the rows, not their buckets: a bucket named here is still priced when another of its rows names this account and region
        qualifications.append("%d supplied economic row%s not used because %s another account or region than %s in %s; resourceId: %s." % (
            len(unused), " was" if len(unused) == 1 else "s were", "it names" if len(unused) == 1 else "they name", account, region, ", ".join(sorted(set(unused)))))
    named = {row["resourceId"] for row in supplied_rows if isinstance(row.get("resourceId"), str) and row["resourceId"].strip()}
    buckets = sorted(dict.fromkeys(selection)) if selection is not None else sorted(named.union(listed))
    present = [bucket for bucket in buckets if bucket in listed]
    if not buckets:
        assessment["coverage"] = {"inventory_complete": inventory_complete, "discovered_count": 0, "assessed_count": 0, "unassessed_ids": []}
        assessment["run"]["status"] = "complete" if inventory_complete else "partial"
        qualifications.append("A completed listing of buckets in %s returned no buckets." % region)
        return close()
    reads = bucket_reads(evidence, buckets, present, region, profile, workers)
    billing = pricing_mode == "billing"
    metrics, id_map = collect_metrics(evidence, present, region, as_of, profile, billing, workers)
    reduced = {bucket: None if metrics[bucket]["missing"] else reduce_metrics(metrics[bucket], billing) for bucket in present}
    for bucket in present:
        if reduced[bucket] is not None and metrics[bucket]["fault"]:
            reduced[bucket]["fault"] = metrics[bucket]["fault"]
    calculations["metric_queries"] = id_map
    # 3. economics by mode: public catalog, Cost Explorer rates (with the conditional public fallback), or supplied rows
    economics, economics_problem, rates, catalog_refs = {}, None, None, []
    def holds(bucket, name):
        r = reduced.get(bucket)
        return bool(r) and (r["tiers"]["standardGb" if name == "standard" else "standardIaGb"] > 0 or r.get("standardGbBilled" if name == "standard" else "standardIaGbBilled", 0) > 0)
    if billing and any(holds(bucket, name) for bucket in present for name in CE_PATTERNS):
        rates, ce_failure = collect_cost_explorer(evidence, account, region, as_of, request.get("billing_profile") or profile)
        needed = {name for bucket in present for name in CE_PATTERNS if holds(bucket, name) and (rates or {}).get("classes", {}).get(name, {}).get("rate") is None}
        if ce_failure:
            economics_problem = dict(ce_failure, decision="needs_evidence" if ce_failure["kind"] == "missing_evidence" else "error")
        elif needed:
            economics_problem = {"kind": "missing_evidence", "decision": "needs_evidence", "request": ECONOMICS_INPUT, "ref": rates["refs"][-1], "classes": sorted(needed),
                                 "message": "Cost Explorer returned no usable usage for %s: %s." % (", ".join(sorted(needed)), "; ".join(rates["classes"][n]["reason"] for n in sorted(needed)))}
        if rates:
            evidence.files.setdefault("economics", {})["cost_explorer_effective_rate"] = rates
        if economics_problem and request.get("allow_public_fallback"):
            reason = "Billed costs were requested first. Cost Explorer could not produce every rate because %s As the request allowed, every amount is an AWS public price, not a billed cost." % (
                economics_problem["message"] + (" %s: %s" % (economics_problem["error"]["code"], economics_problem["error"]["message"]) if economics_problem.get("error") else ""))
            qualifications.append(reason)
            issues.append(dict({"kind": "qualification", "stage": "costing", "message": reason}, **({"evidence_refs": [economics_problem["ref"]]} if economics_problem.get("ref") else {}),
                               **({"request": economics_problem["request"]} if economics_problem.get("request") else {})))
            pricing_mode, billing, economics_problem = "public_prices", False, None
            assessment["scope"]["pricing_mode"] = "public_prices"
    elif billing:
        qualifications.append("No bucket in scope holds Standard or Standard-IA bytes, so Cost Explorer was not read.")
    if pricing_mode == "public_prices":
        dimensions, catalog_problem = read_catalog(evidence, region)
        catalog_refs = ["evidence/economics.json#/catalog"] if "catalog" in evidence.files.get("economics", {}) else []
        if catalog_problem:
            missing = "not supplied" in catalog_problem or "ambiguous" in catalog_problem
            economics_problem = dict({"kind": "missing_evidence" if missing else "read_error", "request": ECONOMICS_INPUT, "message": catalog_problem, "decision": "needs_evidence" if missing else "error"},
                                     **({"ref": catalog_refs[0]} if catalog_refs else {}))
    if economics_problem:
        issues.append(dict({"kind": economics_problem["kind"], "stage": "costing", "message": economics_problem["message"] + (  # the stage by which row_reason finds what a priced row waits on
            " %s: %s" % (economics_problem["error"]["code"], economics_problem["error"]["message"]) if economics_problem.get("error") else "")},
            **({"evidence_refs": [economics_problem["ref"]]} if economics_problem.get("ref") else {}), **({"request": economics_problem["request"]} if economics_problem.get("request") else {})))
    rows = []  # (row id, bucket, resource or None, extra details, problem for a missing row)
    for bucket in buckets:
        r, problem = reduced.get(bucket), None
        if pricing_mode == "supplied":
            matches = [row for row in supplied_rows if row.get("resourceId") == bucket]
            if not matches:
                rows.append((bucket, bucket, None, {}, ("needs_evidence", "No economic row was supplied for this bucket.", ECONOMICS_INPUT)))
            for position, row in enumerate(matches, start=1):
                parsed = native_row(row)
                unusable = [field for field in REQUIRED_AMOUNTS if field in parsed["missingFields"] + parsed["malformedFields"]]
                row_id = "%s#%d" % (bucket, position) if len(matches) > 1 else bucket
                rows.append((row_id, bucket, parsed, {"billing_row": {"position": position, "resourceName": row.get("resourceName"), "tag_original_resource_id": row.get("tag_original_resource_id")}} if len(matches) > 1 else {},
                             ("needs_evidence", "The economic row supplied for %s has no usable %s; a zero in its place is not an amount." % (row_id, ", ".join(unusable)), ECONOMICS_INPUT) if unusable else None))
            continue
        resource, extra = None, {}
        if pricing_mode == "technical_only":
            problem = ("needs_evidence", "No economics were requested (technical_only); the savings checks cannot run.", ECONOMICS_INPUT)
        elif economics_problem and (pricing_mode == "public_prices" or any(holds(bucket, name) for name in economics_problem.get("classes", CE_PATTERNS))):
            problem = (economics_problem["decision"], economics_problem["message"], economics_problem.get("request"))
        elif r is not None and not r["fault"] and bucket in present:
            s, a = (r["tiers"]["standardGb"], r["tiers"]["standardIaGb"]) if pricing_mode == "public_prices" else (r.get("standardGbBilled", 0), r.get("standardIaGbBilled", 0))
            if billing and (missing_series := [t for tier, key, t in (("standardGb", "standardGbBilled", "StandardStorage"), ("standardIaGb", "standardIaGbBilled", "StandardIAStorage")) if r["tiers"][tier] > 0 and r.get(key, 0) == 0]):
                problem = ("needs_evidence", "%s holds %s bytes today but its 7-day billed-size series returned no usable day." % (bucket, " and ".join(missing_series)), dict(METRICS_READ, region=region))
            elif s + a > 0:
                try:
                    if billing:
                        flat = {name: [{"begin": 0, "end": "Inf", "rate": rates["classes"][name]["rate"] if holds(bucket, name) else 0}] for name in CE_PATTERNS}
                        resource = dict(modeled_row(s, a, flat, "cost-explorer-effective-rate", "account-and-region effective rate, one flat band per class"),
                                        sizeBasis="7-day mean of daily CloudWatch BucketSizeBytes", sizeDays=r["sizeDays"], **({"rateNotNeeded": [n for n in CE_PATTERNS if not holds(bucket, n)]} if not all(holds(bucket, n) for n in CE_PATTERNS) else {}))
                    else:
                        resource = modeled_row(s, a, dimensions, "public-price-model", "bucket-alone progressive public quote")
                except ValueError as fault:
                    problem = ("needs_evidence", "The storage model is undefined for this bucket: %s." % fault, ECONOMICS_INPUT)
            else:
                problem = ("needs_evidence", "The bucket holds no Standard or Standard-IA payload today, so the storage model is undefined.", ECONOMICS_INPUT)
        rows.append((bucket, bucket, resource, extra, problem))
    # 4. the checks and the costing, row by row
    boundary, basis = as_of.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=EARLY_DAYS), BASIS_BY_MODE.get(pricing_mode)
    window = {"start": rates["window"]["Start"] + "T00:00:00Z", "end": rates["window"]["End"] + "T00:00:00Z"} if billing and rates and rates.get("classes") else None
    findings, few_days = [], []
    for row_id, bucket, resource, extra, problem in rows:
        in_listing, r = True if bucket in listed else False if inventory_complete else None, reduced.get(bucket)
        economic_row, resource = resource, None if problem else resource  # a row with an unusable required amount is kept, and no check or cost reads it
        checks, stopped, estimate, row_quals, exception = assess_row(bucket, in_listing, reads.get(bucket, {}), r, resource, boundary)
        rejected = any(status == "fail" for _, status, _, _ in checks)
        read_problem = ("needs_evidence", "A page of the bucket listing is missing, so whether it holds %s is not known." % bucket, failure["missing"]) if in_listing is None else None
        for file, operation in (("lifecycle", "GetBucketLifecycleConfiguration"), ("intelligent-tiering", "ListBucketIntelligentTieringConfigurations")):
            read = reads.get(bucket, {}).get(file, {})
            swallowed = ("NoSuchLifecycleConfiguration", "NoSuchBucket") if file == "lifecycle" else ("NoSuchConfiguration", "NoSuchBucket")
            request = {"kind": "aws_read", "name": operation, "service": "s3", "region": region, "parameters": {"Bucket": bucket}}
            if stopped and checks[-1][1] == "not_evaluated" and read.get("missing"):
                read_problem = read_problem or ("needs_evidence", "The %s response for this bucket was not supplied." % operation, read["missing"])
            elif stopped and checks[-1][1] == "not_evaluated" and read.get("error") and read["error"]["code"] not in swallowed and "AccessDenied" not in (read["error"]["code"] if file != "lifecycle" else ""):
                read_problem = read_problem or ("error", "%s failed: %s: %s" % (operation, read["error"]["code"], read["error"]["message"]), request, read["refs"])
            elif file == "lifecycle" and checks[-1][0] == "LifecycleConfigurationReadable" and checks[-1][1] == "fail":  # a rejection: name the read that caused it
                issues.append({"kind": "read_error", "stage": "collection", "resource_id": bucket, "message": "%s failed: %s: %s" % (operation, read["error"]["code"], read["error"]["message"]), "request": request})
        if not rejected and read_problem is None and reads.get(bucket, {}).get("tagging", {}).get("missing"):
            read_problem = ("needs_evidence", "The GetBucketTagging response for this bucket was not supplied.", reads[bucket]["tagging"]["missing"])
        if not rejected and read_problem is None and r is not None and r["fault"]:
            read_problem = ("error", r["fault"], dict(METRICS_READ, region=region), metrics[bucket]["refs"])
        if not rejected and read_problem is None and in_listing and r is None:
            read_problem = ("needs_evidence", "The GetMetricData responses for this bucket were not supplied.", metrics[bucket]["missing"])
        problem = read_problem or problem  # a failed or missing read names the next read before any economics input
        decision = "not_recommended" if rejected else problem[0] if problem else "recommended"
        finding = {"id": row_id, "resource": {"id": bucket, "type": RESOURCE_TYPE, "region": region}, "decision": decision, "checks": check_rows(checks)}
        if decision == "recommended":
            finding["recommendation"] = {"action": "transition_to_intelligent_tiering", "parameters": {"resourceId": bucket, "bucketName": bucket, "sitRuleName": RULE_NAME}}
        elif problem and not rejected and not (economics_problem and problem[1] == economics_problem["message"]):  # the run-level issue covers those rows
            issues.append(dict({"kind": "read_error" if problem[0] == "error" else "missing_evidence", "stage": "costing" if economic_row and not resource else "collection", "resource_id": bucket, "message": problem[1]},
                               **({"request": problem[2]} if problem[2] else {}), **({"evidence_refs": problem[3]} if problem[3:] else {})))
        if rejected and not any(status == "fail" for name, status, _, _ in checks if name not in ("NetSavingsAfterObjectFees", "MinimalSavings", "MaximumSavings")):
            finding["technical_eligibility"] = "eligible"
        costs = {"status": "not_calculated"}
        if resource and estimate:
            costs = dict({"status": "calculated", "basis": basis, "estimation_method": "model_estimate", "currency": "USD"},
                         **({"source_period": window or {"start": resource.get("start_date"), "end": resource.get("end_date")}} if billing or (supplied and resource.get("start_date")) else {}),
                         current_annual=resource["annualCost"], target_annual=resource["annualCost"] - estimate["netSavings"], annual_savings=estimate["netSavings"],
                         one_time_cost=estimate["transitionCharge"], first_year_savings=estimate["firstYearNetSavings"])
        elif resource:
            costs = dict({"status": "not_calculated", "basis": basis, "currency": "USD", "current_annual": resource["annualCost"]}, **({"source_period": window} if window else {}))
            extra["nativeCalculation" if pricing_mode == "supplied" else "modeledCalculation"] = {"annualSavings": resource["totalSavings"], "origin": "resource.totalSavings gross fallback; not a net saving"}
        if row_quals:
            finding["qualifications"] = row_quals
        details = dict({"tier_sizes_gb": dict(r["tiers"], **({k: r[k] for k in ("standardGbBilled", "standardIaGbBilled")} if billing and "standardGbBilled" in r else {}))} if r else {},
                       **extra, **({"exception": exception} if exception else {}))
        if billing and r and r.get("sizeDays") and resource and any(0 < r["sizeDays"][k] < 7 for k in r["sizeDays"]):
            few_days.append("%s (%s)" % (bucket, ", ".join("%s %d" % (k, v) for k, v in r["sizeDays"].items() if v < 7)))
        refs = [ref for file in ("tagging", "lifecycle", "intelligent-tiering") for ref in reads.get(bucket, {}).get(file, {}).get("refs", [])] + (metrics[bucket]["refs"] if bucket in metrics else []) + catalog_refs + (rates["refs"] if resource and billing else [])
        if r or economic_row:
            calculations[row_id] = dict({"inputs": {"objectCount": r["objectCount"], "archivedObjectCount": r["archivedObjectCount"], "tiers": r["tiers"]}} if r else {}, **({"billed": {k: r[k] for k in ("standardGbBilled", "standardIaGbBilled", "sizeDays")}} if billing and r and "sizeDays" in r else {}),
                                        **({"economic_row": economic_row} if economic_row else {}), **({"estimate": estimate} if estimate else {}))
            refs.append("evidence/calculations.json#/" + row_id)
        finding.update(costs=costs, evidence_refs=refs, **({"details": details} if details else {}))
        findings.append(finding)
    evidence.files["calculations"] = calculations
    # 5. totals and the run-wide statements
    unassessed = sorted({f["resource"]["id"] for f in findings if f["decision"] in ("needs_evidence", "error")})
    for finding in findings:  # the guide's Decisions: a bucket with a row still open has no recommended row, so none of its amounts is totaled
        if finding["decision"] == "recommended" and finding["resource"]["id"] in unassessed:
            finding["decision"] = "needs_evidence"
            del finding["recommendation"]
    issues[:] = [issue for n, issue in enumerate(issues) if issue not in issues[:n]]  # two billing rows of one bucket share its read issues
    recommended = [f for f in findings if f["decision"] == "recommended"]
    duplicated = sorted({f["resource"]["id"] for f in recommended if sum(g["resource"]["id"] == f["resource"]["id"] for g in recommended) > 1})
    included = [f for f in recommended if f["resource"]["id"] not in duplicated]
    totals = {"decision_counts": {d: sum(f["decision"] == d for f in findings) for d in ("recommended", "not_recommended", "needs_evidence", "error")}, "annual_savings": None}
    if duplicated:
        totals.update(excluded_ids=duplicated, reason_not_totaled="%s %s several billing rows; they are shown row by row and not added." % (", ".join(duplicated), "has" if len(duplicated) == 1 else "have"))
    if included:
        totals.update({key: included[0]["costs"][key] for key in ("currency", "basis", "estimation_method")})
        totals.update({field: sum(f["costs"][field] for f in included) for field in ("current_annual", "target_annual", "annual_savings")})
    elif recommended:
        totals["reason_not_totaled"] = totals.get("reason_not_totaled") or "No row can be totaled."
    assessment.update(findings=findings, totals=totals, coverage={"inventory_complete": inventory_complete, "discovered_count": len(buckets), "assessed_count": len(buckets) - len(unassessed), "unassessed_ids": unassessed})
    assessment["run"]["status"] = "complete" if not unassessed and inventory_complete else "partial"
    source = {"public_prices": "AWS public prices for Standard and Standard-IA payload storage only, quoted for each bucket alone at its current size. Current cost is that modeled storage cost, not the bucket's bill. Discounts, requests, retrievals, early deletes, minimum-size and minimum-duration charges and other storage classes are excluded; no existing-monitoring credit and no retrieval or early-delete saving is assumed.",
              "billing": ("Current cost is an account-and-region effective rate for Standard and for Standard-IA storage (net billed cost divided by billed GB-months over %s to %s, end exclusive), applied to each bucket's 7-day mean CloudWatch size and projected for 12 months at that rate; today's size can differ. It is not that bucket's bill and not CUR-equivalent. The rate averages the pricing bands and discounts across all of the account's storage of that class in the region; a bucket's own band, lifetime, size changes and adjustments are not reconstructed. Payload storage only: requests, retrievals, early deletes, Standard-IA minimum-size and minimum-duration charges and every other storage class are excluded; no retrieval or early-delete saving and no existing-monitoring credit is assumed, so buckets that already hold Intelligent-Tiering objects can have their fees overstated. Visible net amounts do not prove that every payer discount is visible, and the billing view may be pro forma." % (
                  rates["window"]["Start"], rates["window"]["End"])) if rates and rates.get("classes") else None,
              "supplied": "Current cost and the gross saving were supplied by the caller (%s) and used unchanged; the gross saving is the caller's estimate." % ((supplied or {}).get("source") or "no source stated"),
              "technical_only": "No economics were requested: no saving is quantified and orders 10 to 12 did not run."}[pricing_mode]
    qualifications += [text for text in (
        "Model-based estimate. The future Intelligent-Tiering tier mix is modeled, not measured: storage is assumed to cost %s of the Standard price after the transition, with Standard-IA weighted at %s of Standard. No object access pattern was measured." % (IT_PRICE_FRACTION, SIA_WEIGHT) if pricing_mode != "technical_only" else None,
        source,
        "Cost Explorer: %d of 31 days returned, %d marked Estimated; %s UnblendedCost fallback groups; per-class usage types, costs and rates are in evidence/economics.json." % (
            rates["daysReturned"], rates["estimatedDays"], sum(c.get("netFallbackGroups", 0) for c in rates["classes"].values())) if billing and rates and rates.get("classes") and rates.get("daysReturned") is not None else None,
        "Priced on fewer than 7 billed-size days: %s." % "; ".join(few_days) if few_days else None,
        "Savings are after the lower of two bounds: the monitoring fee (0.0025 USD per 1,000 objects per month) or the loss on objects below 128 KiB, estimated from the average object size. Objects below 128 KiB do not produce the modeled saving." if pricing_mode != "technical_only" else None,
        "First-year savings also subtract a one-time transition charge (0.01 USD per 1,000 objects) for existing objects only; objects that arrive later incur further charges." if pricing_mode != "technical_only" else None,
        "The object count is the latest daily value within 90 days and sizes are the first value returned within 3 days; they need not describe one instant.",
        "CloudFix recommendation history was not queried, so RecommendationFinalized is not evaluated. Buckets were discovered with ListBuckets, not by CloudFix billing discovery." if selection is None
        else "CloudFix recommendation history was not queried, so RecommendationFinalized is not evaluated. Only the selected buckets were assessed; this is not an account-wide inventory.",
        "Optional archive tiers, application retrieval needs and prefix-, tag- or noncurrent-version lifecycle rules were not assessed as safe. Nothing was changed.",
        "A row that stopped early lists the finder's checks up to the one that stopped it; the later checks were not run.") if text]
    assessment["verification"]["summary"] = ("Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. "
                                             "Every metric reduction, economic row and fee estimate is recorded in evidence/calculations.json. AWS facts were not re-read. No independent review took place.")
    return close()

if __name__ == "__main__":
    sys.exit(main())
