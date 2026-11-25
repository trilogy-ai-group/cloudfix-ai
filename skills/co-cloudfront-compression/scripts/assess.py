#!/usr/bin/env python3
"""Assess CloudFront distributions for cache behaviors that leave compression off, with the customer's own AWS read access.
Standard library only; AWS reads go through the AWS CLI v2. The rules are the ones in ../references/.
This finder estimates no savings: the result is always technical_only.
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
MODE_FLAG, PRICING_MODES = "--economics-mode", ("technical_only", "supplied")
RESOURCE_NOUN, EMPTY_INVENTORY_NOUN = "distribution", "distributions"
REPORT_FOOTER = "Nothing was executed, and no saving is established or realized. Full result: `assessment.json`; evidence: `evidence/`."
ALLOWED_READS = frozenset({("sts", "GetCallerIdentity"), ("cloudfront", "ListDistributions"), ("cloudfront", "GetDistributionConfig"),
                           ("cloudfront", "GetCachePolicy"), ("cloudfront", "ListTagsForResource"), ("cloudwatch", "GetMetricData")})  # the finder's assessment reads; aws() refuses anything else

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

SKILL_ID, RESOURCE_TYPE = "co-cloudfront-compression", "AWS::CloudFront::Distribution"
FINDER_ID, CHECK_NAMES = "CloudFrontCompression", ("RecommendationFinalized", "ResourceExists", "ExcludeCacheAndCompression", "MaximumSavings")
CACHING_DISABLED_NAME, HISTORY_DAYS, METRIC_BATCH = "Managed-CachingDisabled", 14, 500
DEFAULT_CONFIGURATION = {"resourceCostThreshold": 0}
NO_HISTORY = "CloudFix recommendation history is not available to a standalone run."
CONFIG_REQUIRED, POLICY_REQUIRED = ("CallerReference", "Origins", "DefaultCacheBehavior", "Comment", "Enabled"), ("Id", "LastModifiedTime", "CachePolicyConfig")

def add_skill_arguments(parser):
    # CloudFront is global: the endpoint region defaults, and the mode defaults because this finder estimates no savings.
    parser.set_defaults(region="us-east-1", economics_mode="technical_only")
    parser.epilog = "A replay takes the assessment time from the saved run-metadata.json; --as-of with --replay is refused."
    parser.add_argument("--distribution-id", action="append", help="assess this distribution (repeatable); default: every distribution in the account")
    parser.add_argument("--resource-cost-threshold", type=float, help="tenant setting, only when the user supplies it; default 0")
    parser.add_argument("--no-download-traffic", action="store_true", help="skip the optional CloudWatch BytesDownloaded observation")

def describe_change(row):
    counts, behaviors = row["checks"][2]["value"], row["recommendation"]["parameters"]["behaviors"]
    return "Enable compression on %d of %d cache behaviors: %s" % (len(behaviors), counts["behaviorCount"], ", ".join(behaviors))

def skill_revision():
    with open(os.path.join(SCRIPT_DIR, "..", "SKILL.md"), encoding="utf-8") as handle:
        return re.search(r'^  version: "([^"]+)"', handle.read(), re.M).group(1)

def billing_week(as_of):
    """date_trunc('week', asOf - 8 days) to date_trunc('week', asOf - 1 day): Monday 00:00 UTC, end exclusive."""
    day = as_of.date() - datetime.timedelta(days=8)
    monday = day - datetime.timedelta(days=day.weekday())
    return monday.isoformat() + "T00:00:00Z", (monday + datetime.timedelta(days=7)).isoformat() + "T00:00:00Z"

def disables_caching(config):
    """Rule 3: the exact managed name, or DefaultTTL, MaxTTL and MinTTL each strictly numeric 0 (a boolean is not a number)."""
    ttls = [config.get(key) for key in ("DefaultTTL", "MaxTTL", "MinTTL")]
    return config.get("Name") == CACHING_DISABLED_NAME or all(type(ttl) in (int, float) and ttl == 0 for ttl in ttls)

def behaviors_of(config):
    """The default behavior, then the additional ones. A DefaultCacheBehavior or CacheBehaviors that is not an object has no settings, as the finder's
    property read of it has none; an entry of Items that is not an object raises AttributeError, where the finder's read of it throws."""
    default, extra = (value if isinstance(value, dict) else {} for value in (config["DefaultCacheBehavior"], config.get("CacheBehaviors")))
    return [("Default (*)", default)] + [(item.get("PathPattern"), item) for item in extra.get("Items") or []]

def policy_ids_of(config):
    return list(dict.fromkeys(item.get("CachePolicyId") for _, item in behaviors_of(config) if item.get("CachePolicyId") is not None))

def usable_config(record):
    """The DistributionConfig of a successful read, or None when it or a required part is nullish (collection-and-normalization.md)."""
    config = ((record or {}).get("output") or {}).get("DistributionConfig")
    return config if isinstance(config, dict) and all(config.get(key) is not None for key in CONFIG_REQUIRED) else None

def check_costs(supplied):
    """The costs file of SKILL.md, refused before any read when a later step could not use it."""
    rows = supplied.get("resources", []) if isinstance(supplied, dict) else None
    if not (isinstance(rows, list) and all(isinstance(row, dict) and isinstance(row.get("resourceId"), str) and row["resourceId"] for row in rows)):
        raise InputError('The supplied costs must be an object whose "resources" is a list of objects, each with a resourceId')
    ids = [row["resourceId"] for row in rows]
    if len(set(ids)) != len(ids):  # no reference says which row would win
        raise InputError("The supplied costs name a resourceId more than once: %s" % ", ".join(sorted({one for one in ids if ids.count(one) > 1})))
    if not (isinstance(supplied.get("currency"), str) and re.fullmatch(r"[A-Z]{3}", supplied["currency"])):  # an amount without its currency is not a usable fact
        raise InputError('The supplied costs need "currency", three capital letters such as USD')
    history, period = supplied.get("finalized_recommendations"), supplied.get("period")
    try:
        usable = (isinstance(supplied.get("lookup_complete", False), bool) and (history is None or isinstance(history, list) and "lookup_complete" in supplied) and (period is None or isinstance(period, dict))
                  and all(isinstance(record["resourceId"], str) and isinstance(record["finderId"], str) and (record.get("savingsFinalizedAt") is None or parse_time(record["savingsFinalizedAt"])) for record in history or [])
                  and all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)", period[key]) and parse_time(period[key]) for key in (("start", "end") if period else ())))
    except (AttributeError, KeyError, TypeError, ValueError):
        usable = False
    if not usable:
        raise InputError('The supplied costs need "period" as {start, end} date-times with an offset (2026-10-19T00:00:00Z), "lookup_complete" as true or false (required with history) and "finalized_recommendations" as a list of objects with a string resourceId and finderId and a savingsFinalizedAt that is null, absent or ISO 8601')

def rule3(config, policies):
    """The ExcludeCacheAndCompression value, the compression-disabled behavior names and the caching-disabled policy names."""
    retained = [(name, item["Compress"]) for name, item in behaviors_of(config) if item.get("Compress") is not None]
    disabled = [name for name, compress in retained if not compress]  # JS-falsy; for AWS booleans, explicit false
    caching_off = [policies[pid].get("Name") or pid for pid in policy_ids_of(config) if disables_caching(policies[pid])]
    value = {"policyCount": len(policies), "cachingDisabledPolicyCount": len(caching_off), "behaviorCount": len(retained),
             "compressionDisabledBehaviorCount": len(disabled)}
    return value, disabled, caching_off, [type(compress) for _, compress in retained if type(compress) is not bool]

def rule3_reason(value, caching_off):
    if caching_off:
        return "A fetched cache policy disables caching: %s." % ", ".join(caching_off)
    if value["behaviorCount"] == 0:
        return "No cache behavior reports a Compress value."
    return "Compression is already enabled on %s cache behavior%s." % ({1: "the only", 2: "both"}.get(value["behaviorCount"], "all %d" % value["behaviorCount"]),
                                                                        "" if value["behaviorCount"] == 1 else "s")

def finalized_recently(history, distribution_id, as_of):
    """Rule 1 on complete supplied history: a record for this resource and finder with savingsFinalizedAt > asOf - 14 days."""
    floor = as_of - datetime.timedelta(days=HISTORY_DAYS)
    return any(record.get("resourceId") == distribution_id and record.get("finderId") == FINDER_ID
               and record.get("savingsFinalizedAt") and parse_time(record["savingsFinalizedAt"]) > floor for record in history)

def read_outcome(record, position, file, pointer):
    """(ok, ref, failure) for one evidence record; failure is {"missing": request} or {"error": ..., "ref": ...}."""
    if record is None:
        return False, None, {"missing": position}
    ref = "evidence/%s.json#/%d" % (file, position)
    if "error" in record:
        return False, ref + "/error", {"error": record["error"], "ref": ref + "/error"}
    return True, ref + (pointer if pointer.rsplit("/", 1)[1] in record["output"] else "/output"), None  # never a pointer to a key the response lacks

def observe_traffic(ids, results, week, reason_if_missing):
    """details.download_traffic per distribution from the merged GetMetricData results; (observations, affected ids)."""
    observations, affected = {}, []
    for index, distribution_id in enumerate(ids):
        merged = results.get("d%d" % index)
        entry = {"bytesDownloaded": None, "weekStart": week[0], "weekEnd": week[1], "datapoints": None}
        if merged is None:
            entry["reason"] = reason_if_missing or "The GetMetricData result for this distribution is missing from every page."
        elif merged["status"] != "Complete":
            entry.update(datapoints=len(merged["values"]), reason=("A page of the series returned status %s; download volume is unknown." if merged["status"] in ("InternalError", "Forbidden") else
                                                                    "The series ended with status %s; download volume is unknown.") % merged["status"])
        elif not merged["values"]:
            entry.update(datapoints=0, reason="The complete series returned no datapoints; download volume is unknown, not zero.")
        elif not all(type(value) in (int, float) and math.isfinite(value) for value in merged["values"]):
            entry.update(datapoints=len(merged["values"]), reason="A datapoint of the series is not a finite number; download volume is unknown.")
        else:
            total = sum(merged["values"])
            entry.update(bytesDownloaded=int(total) if float(total).is_integer() else total, datapoints=len(merged["values"]))
        affected += [distribution_id] if entry["bytesDownloaded"] is None else []
        observations[distribution_id] = (entry, merged["ref"] if merged else None)
    return observations, affected

def collect_traffic(evidence, ids, week, profile):
    """One BytesDownloaded query per distribution, 500 per request, every page. Returns (merged results by query id, failure message)."""
    results, failure = {}, None
    for start in range(0, len(ids), METRIC_BATCH):
        queries = [{"Id": "d%d" % (start + offset), "MetricStat": {"Metric": {"Namespace": "AWS/CloudFront", "MetricName": "BytesDownloaded", "Dimensions": [
            {"Name": "DistributionId", "Value": distribution_id}, {"Name": "Region", "Value": "Global"}]}, "Period": 86400, "Stat": "Sum"}}
            for offset, distribution_id in enumerate(ids[start:start + METRIC_BATCH])]
        params = {"MetricDataQueries": queries, "StartTime": week[0], "EndTime": week[1]}
        records, fault = evidence.pages("get-metric-data-bytes-downloaded", "cloudwatch", "GetMetricData", params, "us-east-1", profile)
        problem = None
        for record, position in records:
            listed = record.get("output", {}).get("MetricDataResults") or []
            if not isinstance(listed, list):
                problem = "A GetMetricData page returned MetricDataResults that is not a list."
                break
            for index, result in enumerate(listed):
                if not (isinstance(result, dict) and isinstance(result.get("Id"), str)):
                    continue  # it answers no query: its distribution's result reads as missing
                merged = results.setdefault(result["Id"], {"values": [], "status": None, "ref": evidence.ref("get-metric-data-bytes-downloaded", position, "/output/MetricDataResults/%d" % index)})
                values = result.get("Values")
                merged["values"] += values if isinstance(values, list) else [] if values is None else [None]  # not a list: one datapoint that observe_traffic refuses
                if merged["status"] not in ("InternalError", "Forbidden"):  # a fault on any page stands for the whole series
                    merged["status"] = result.get("StatusCode")
        if fault:
            problem = "GetMetricData failed: %s: %s" % (fault["error"]["code"], fault["error"]["message"]) if "error" in fault else "A page of the requested GetMetricData read is missing."
        if problem:  # the optional observation costs its own note: every volume of this request is unknown, and no decision changes
            failure = problem
            for query in queries:
                results.pop(query["Id"], None)
    return results, failure

def report_headline(assessment, recommended):
    discovered = assessment["coverage"]["discovered_count"]
    return "**Savings not established. Compression is recommended for %d of %s distributions.**" % (len(recommended), "unknown" if discovered is None else discovered)

def report_context(assessment):
    coverage, scope, run, metadata = (assessment[key] for key in ("coverage", "scope", "run", "run_metadata"))
    unknown = lambda value: "unknown" if value is None else value
    return ["Account %s · Region: global · Scope: %s · Discovered: %s · Assessed: %s · Unassessed: %d · Status: %s · Observed: %s · Verification: %s." % (
        unknown(scope["account_id"]), metadata["scope"], unknown(coverage["discovered_count"]), unknown(coverage["assessed_count"]),
        len(coverage["unassessed_ids"]), run["status"], run["observed_at"],
        {"self_check": "self-check", "independent_review": "independent review", "none": "not verified"}[assessment["verification"]["method"]]), "",
        "Technical assessment only: no prices were requested. CloudFix does not track savings for this best practice, so no saving is estimated."
        if metadata["economics_mode"] == "technical-only" else
        "Costs were supplied, and this finder estimates no savings: CloudFix does not track savings for this best practice, so the supplied amounts are context, not a saving."]

def report_notes(assessment):
    """The historical cost illustration, when a row carries one; never a savings estimate."""
    illustrated = [row for row in assessment["findings"] if row.get("details", {}).get("historical_cur_illustration")]
    lines = ["", "### Historical cost illustration (not a savings estimate)", "",
             "CloudFix's own tracked savings for this best practice are zero. The 65% figure is an unverified model over one billing week of Data Transfer cost, not a saving."] if illustrated else []
    shown = lambda amount: money(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else "unknown"
    for row in illustrated:
        model = row["details"]["historical_cur_illustration"]
        window = model.get("window") or {}
        lines.append("- %s: billing week %s to %s; annualized cost %s; 65%% illustration %s." % (
            row["id"], (window.get("start") or "?")[:10], (window.get("end") or "?")[:10],
            shown(model.get("annual_cost")), shown(model.get("source_cur_estimated_annual_savings"))))
    return lines

def report_bullets(assessment):
    return []

def run(args):
    out_dir = args.out
    issues, qualifications = [], []
    if args.replay:
        evidence = Evidence(args.replay, True)
        saved = evidence.files.get("run-metadata")
        metadata, request = (saved.get("run_metadata"), saved.get("request")) if isinstance(saved, dict) else (None, None)
        if not isinstance(metadata, dict) or not isinstance(request, dict):
            raise InputError("The replay folder has no usable run-metadata.json")
        if args.as_of:
            raise InputError("--as-of is for a live run; a replay takes the assessment time from the saved run-metadata.json")
        evidence.directory = os.path.join(out_dir, "evidence")
    else:
        evidence = Evidence(os.path.join(out_dir, "evidence"), False)
        configuration = dict(DEFAULT_CONFIGURATION, **({"resourceCostThreshold": args.resource_cost_threshold} if args.resource_cost_threshold is not None else {}))
        selection = list(dict.fromkeys(args.distribution_id)) if args.distribution_id else None
        metadata = {"finderId": FINDER_ID, "skill_version": None, "accountId": args.account, "region": args.region,
                    "asOf": iso_js(parse_time(args.as_of) if args.as_of else datetime.datetime.now(datetime.timezone.utc)), "evaluation_timezone": "UTC",
                    "scope": "all distributions in the account" if selection is None else "selected distributions: " + ", ".join(selection),
                    "economics_mode": args.economics_mode.replace("_", "-"), "configuration": configuration,
                    "configuration_overrides": {key: value for key, value in configuration.items() if value != DEFAULT_CONFIGURATION[key]},
                    "run_classification": "standard", "experiment": None}
        request = {"economics_mode": args.economics_mode, "resource_ids": selection, "profile": args.profile, "download_traffic": not args.no_download_traffic}
        evidence.files["run-metadata"] = {"run_metadata": metadata, "request": request}
        if (args.economics_mode == "supplied") != bool(args.costs):  # a costs file is never dropped without a word
            raise InputError("--economics-mode supplied and --costs FILE go together: give both or neither")
        if args.costs:
            try:
                with open(args.costs, encoding="utf-8") as handle:
                    evidence.files["supplied-costs"] = json.load(handle)
            except (OSError, ValueError) as fault:
                raise InputError("--costs could not be read as JSON: %s" % fault)
    configuration = metadata.get("configuration")
    threshold = (configuration if isinstance(configuration, dict) else {}).get("resourceCostThreshold", DEFAULT_CONFIGURATION["resourceCostThreshold"])
    try:
        as_of = parse_time(metadata.get("asOf"))
        usable = (all(isinstance(metadata.get(key), str) for key in ("accountId", "region", "scope", "economics_mode")) and request.get("economics_mode") in PRICING_MODES
                  and type(threshold) in (int, float) and math.isfinite(threshold))
    except (AttributeError, ValueError):
        usable = False
    if not usable:
        raise InputError("The run metadata needs asOf (ISO 8601), accountId, region, scope, economics_mode, a finite resourceCostThreshold and request.economics_mode (%s)" % " or ".join(PRICING_MODES))
    account, region, profile = metadata["accountId"], metadata["region"], request.get("profile")
    selection, want_traffic = request.get("resource_ids"), request.get("download_traffic", True)
    if selection is not None and not (isinstance(selection, list) and all(isinstance(item, str) and re.fullmatch(r"[A-Z0-9]+", item) for item in selection)):
        raise InputError("A distribution id is uppercase letters and digits only: %s" % json.dumps(selection))
    selection = list(dict.fromkeys(selection)) if selection else selection  # a replayed request may repeat an id
    supplied = evidence.files.get("supplied-costs") if request["economics_mode"] == "supplied" else None
    if request["economics_mode"] == "supplied":
        if supplied is None:
            raise InputError("economics_mode supplied needs supplied-costs.json in the evidence folder")
        check_costs(supplied)
    supplied_rows = {row["resourceId"]: (row, index) for index, row in enumerate((supplied or {}).get("resources", []))}
    history = (supplied or {}).get("finalized_recommendations") if (supplied or {}).get("lookup_complete") else None
    week = billing_week(as_of)
    assessment = {
        "schema_version": "1.0", "skill": {"id": SKILL_ID, "revision": skill_revision(), "source_ref": None},
        "run": {"observed_at": metadata["asOf"], "mode": "local_code", "status": "complete"},
        "scope": {"account_id": None, "regions": ["global"], "resource_ids": selection, "pricing_mode": "technical_only"},
        "coverage": {"inventory_complete": False, "discovered_count": None, "assessed_count": None, "unassessed_ids": []},
        "findings": [], "totals": {"decision_counts": {"recommended": 0, "not_recommended": 0, "needs_evidence": 0, "error": 0}, "annual_savings": None,
                                   "reason_not_totaled": "Technical assessment only; savings were not estimated."},
        "issues": issues, "qualifications": qualifications, "verification": {"method": "self_check", "summary": "Recomputed the decision counts from the saved rows and opened every evidence reference after writing the files."},
        "evidence_refs": [], "run_metadata": metadata, "execution_status": "not_executed"}

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
    # 1. identity
    identity, position = evidence.read("get-caller-identity", "sts", "GetCallerIdentity", {}, region, profile)
    if identity is None or "error" in identity:
        return failed("scope", "Identity could not be verified.", {"error": identity["error"], "ref": evidence.ref("get-caller-identity", position)} if identity else {"missing": position})
    assessment["evidence_refs"].append(evidence.ref("get-caller-identity", position, "/output"))
    if identity["output"].get("Account") != account:
        return failed("scope", "The credentials belong to account %s, not the requested %s." % (identity["output"].get("Account"), account))
    assessment["scope"]["account_id"] = account
    # 2. inventory: every ListDistributions page, or the selection
    if selection is None:
        ids, params, dropped, seen = [], {}, 0, set()
        while True:
            record, position = evidence.read("list-distributions", "cloudfront", "ListDistributions", params, region, profile)
            if record is None:
                issues.append({"kind": "missing_evidence", "stage": "collection", "message": "The distribution inventory is incomplete: a page is missing.", "request": position})
                assessment["run"]["status"], assessment["coverage"]["unassessed_ids"] = "partial", ids
                assessment["evidence_refs"] += ["evidence/list-distributions.json"] if "list-distributions" in evidence.files else []
                return finish(out_dir, evidence, assessment)
            if "error" in record:
                return failed("collection", "The distribution inventory was denied or failed.", {"error": record["error"], "ref": evidence.ref("list-distributions", position)})
            listing = record["output"].get("DistributionList")
            marker, items = (listing.get("NextMarker"), listing.get("Items") or []) if isinstance(listing, dict) else (None, [])
            problem = ("no DistributionList" if not isinstance(listing, dict) else "Items that is not a list" if not isinstance(items, list) else
                       "no Items with Quantity %s" % listing.get("Quantity") if listing.get("Items") is None and listing.get("Quantity") != 0 else
                       None if not listing.get("IsTruncated") else "IsTruncated without a NextMarker" if not marker else
                       "a repeated or malformed NextMarker" if not isinstance(marker, str) or marker in seen else None)  # a repeated marker would page without end
            if problem:
                return failed("collection", "The distribution inventory is incomplete.", {"ref": evidence.ref("list-distributions", position, "/output"), "error": {
                    "code": "IncompleteResponse", "message": "ListDistributions returned " + problem}})
            for item in items:
                kept = isinstance(item, dict) and item.get("Id") is not None and item.get("Enabled") is not None
                ids += [item["Id"]] if kept else []
                dropped += not kept
            if not listing.get("IsTruncated"):
                break
            seen.add(marker)
            params = {"Marker": marker}
        assessment["evidence_refs"].append("evidence/list-distributions.json")
        if dropped:
            qualifications.append("%d returned distribution records lacked Id or Enabled and were not assessed; a parsing omission, not a deletion." % dropped)
        ids = list(dict.fromkeys(ids))
    else:
        ids = selection
    unmatched = sorted(set(supplied_rows) - set(ids))
    if unmatched:
        qualifications.append("Supplied cost rows for distributions outside the assessed inventory were not used: " + ", ".join(unmatched))
    if not ids:
        assessment["coverage"] = {"inventory_complete": True, "discovered_count": 0, "assessed_count": 0, "unassessed_ids": []}
        qualifications.append("A completed inventory returned no distributions in account %s." % account)
        return finish(out_dir, evidence, assessment)
    # configuration and tags per distribution, then every unique cache policy, then the optional traffic observation
    configs, tags, policies, wanted = {}, {}, {}, {}
    for distribution_id in ids:
        configs[distribution_id] = evidence.read("get-distribution-config-" + distribution_id, "cloudfront", "GetDistributionConfig", {"Id": distribution_id}, region, profile)
        tags[distribution_id] = evidence.read("list-tags-for-resource-" + distribution_id, "cloudfront", "ListTagsForResource",
                                              {"Resource": "arn:aws:cloudfront::%s:distribution/%s" % (account, distribution_id)}, region, profile)
        config = usable_config(configs[distribution_id][0])
        try:
            wanted[distribution_id] = policy_ids_of(config) if config else []
        except AttributeError:  # a cache behavior that is not an object: the finder's rule 3 throws for this distribution alone
            wanted[distribution_id] = None
        for pid in wanted[distribution_id] or []:
            if pid not in policies:
                policies[pid] = evidence.read("get-cache-policy-" + pid, "cloudfront", "GetCachePolicy", {"Id": pid}, region, profile)
    traffic, affected = {}, []
    if want_traffic:
        results, failure = collect_traffic(evidence, ids, week, profile)
        traffic, affected = observe_traffic(ids, results, week, failure)
        for distribution_id in affected:
            issues.append({"kind": "qualification", "stage": "collection", "resource_id": distribution_id,
                           "evidence_refs": ["evidence/get-metric-data-bytes-downloaded.json"] if "get-metric-data-bytes-downloaded" in evidence.files else [],
                           "message": "Download volume for %s is unknown: %s" % (distribution_id, traffic[distribution_id][0]["reason"]),
                           "request": {"kind": "aws_read", "name": "GetMetricData", "service": "cloudwatch", "region": region,
                                       "parameters": {"MetricDataQueries": "AWS/CloudFront BytesDownloaded for " + distribution_id, "StartTime": week[0], "EndTime": week[1]}}})
    # 3. one finding per distribution
    findings, batch = [], []
    for distribution_id in ids:
        checks, refs, details, read_errors, missing = [], [], {"raw_cur_region": None, "savings_tracking": "disabled", "native_final_annual_savings": 0}, [], []
        supplied_row, supplied_index = supplied_rows.get(distribution_id, (None, None))
        cost = (supplied_row or {}).get("annualCost")
        known_cost = isinstance(cost, (int, float)) and not isinstance(cost, bool)  # absent, null or a string is unknown, not a nonfinite cost
        if history is None:
            checks.append(("RecommendationFinalized", None, None, NO_HISTORY))
        else:
            recent = finalized_recently(history, distribution_id, as_of)
            checks.append(("RecommendationFinalized", not recent, {"resourceId": distribution_id}, "A CloudFix recommendation for this distribution was finalized within the last 14 days."))
        record, position = configs[distribution_id]
        ok, ref, failure = read_outcome(record, position, "get-distribution-config-" + distribution_id, "/output/DistributionConfig")
        config = usable_config(record) if ok else None
        stop, disabled = None, []
        if ok and config is None:
            failure = {"error": {"code": "IncompleteResponse", "message": "GetDistributionConfig for %s returned an incomplete distribution config" % distribution_id}, "ref": ref, "derived": True}
        refs += [ref] if ref else []
        if failure and failure.get("missing"):
            missing.append(("GetDistributionConfig", failure["missing"]))
            checks.append(("ResourceExists", None, None, "The GetDistributionConfig response was not supplied."))
            stop = "Not run: the GetDistributionConfig response was not supplied."
        elif failure and failure["error"]["code"] == "NoSuchDistribution":
            checks.append(("ResourceExists", False, {"resourceId": distribution_id}, "GetDistributionConfig returned NoSuchDistribution."))
            stop = "Not run after the earlier rejection."
        elif failure:
            read_errors.append(("GetDistributionConfig", {"Id": distribution_id}, failure))
            batch.append(distribution_id)
            reason = "GetDistributionConfig returned %s." % failure["error"]["code"]
            checks.append(("ResourceExists", None, None, reason))
            stop = "Not run: " + reason
        if stop:
            checks.append(("ExcludeCacheAndCompression", None, None, stop))
        else:
            checks.append(("ResourceExists", True, {"resourceId": distribution_id}, None))
            fetched, policy_failure = {}, None
            if wanted[distribution_id] is None:
                policy_failure = "GetDistributionConfig for %s returned a cache behavior that is not an object." % distribution_id
                read_errors.append(("GetDistributionConfig", {"Id": distribution_id}, {"error": {"code": "IncompleteResponse", "message": policy_failure}, "ref": ref, "derived": True}))
            for pid in wanted[distribution_id] or []:
                precord, pposition = policies[pid]
                pok, pref, pfailure = read_outcome(precord, pposition, "get-cache-policy-" + pid, "/output/CachePolicy")
                policy = precord["output"].get("CachePolicy") if pok else None
                if pok and (policy is None or any(policy.get(key) is None for key in POLICY_REQUIRED) or any(policy["CachePolicyConfig"].get(key) is None for key in ("Name", "MinTTL"))):
                    pok, pfailure = False, {"error": {"code": "IncompleteResponse", "message": 'No %sCachePolicy found with params "%s"' % ("" if policy is None else "complete ", pid)}, "ref": pref, "derived": True}
                refs += [pref] if pref else []
                if pok:
                    fetched[pid] = policy["CachePolicyConfig"]
                elif pfailure.get("missing"):
                    missing.append(("GetCachePolicy", pfailure["missing"]))
                    policy_failure = policy_failure or "The GetCachePolicy response for %s was not supplied." % pid
                else:
                    read_errors.append(("GetCachePolicy", {"Id": pid}, pfailure))
                    policy_failure = policy_failure or "GetCachePolicy for %s returned %s." % (pid, pfailure["error"]["code"] if not pfailure.get("derived") else pfailure["error"]["message"])
            if policy_failure:
                checks.append(("ExcludeCacheAndCompression", None, None, policy_failure))
                stop = "Not run: " + policy_failure
            else:
                counts, disabled, caching_off, odd_types = rule3(config, fetched)
                odd_shape = [key for key in ("DefaultCacheBehavior", "CacheBehaviors") if config.get(key) is not None and not isinstance(config[key], dict)]
                qualifications += ["%s: %s is not an object; it was read as having no settings, as the finder reads it." % (distribution_id, " and ".join(odd_shape))] if odd_shape else []
                checks.append(("ExcludeCacheAndCompression", counts["cachingDisabledPolicyCount"] == 0 and counts["compressionDisabledBehaviorCount"] > 0, counts, rule3_reason(counts, caching_off)))
                if odd_types:
                    qualifications.append("%s: a Compress value is not a boolean (%s); it was counted by JavaScript truthiness." % (distribution_id, ", ".join(sorted({t.__name__ for t in odd_types}))))
        # rule 4 needs a known annual cost; the result stays technical_only whatever was asked for
        if stop:
            checks.append(("MaximumSavings", None, None, stop))
        elif supplied_row is None:
            checks.append(("MaximumSavings", None, None, "Annual cost was not requested in technical_only mode." if supplied is None else "No supplied cost for this distribution."))
        elif not known_cost:
            checks.append(("MaximumSavings", None, None, "The supplied row has no annual cost." if cost is None else "The supplied annual cost is not a number."))
        else:
            finite = math.isfinite(cost)  # the finder compares 0 <= annualCost as it is: an infinite cost passes, NaN and a negative cost fail
            checks.append(("MaximumSavings", 0 <= cost, dict({"annualSavings": 0, "annualCost": cost if finite else None}, **({} if finite else {"reason": "annual-cost-not-finite"})),
                           "annual-cost-not-finite" if not finite else "Annual cost %s is negative." % js_number(cost)))
            qualifications += [] if finite else ["%s: the supplied annual cost is not a finite number. MaximumSavings compared it as the finder does; it is not a usable cost." % distribution_id]
        rows = [{"name": name, "status": "not_evaluated", "reason": reason} if approved is None else {"name": name, "status": "pass", "value": value} if approved else
                {"name": name, "status": "fail", "value": value, "reason": reason} for name, approved, value, reason in checks]
        # tags and traffic never decide; a failed tag read is a qualification, a missing one is missing evidence
        trecord, tposition = tags[distribution_id]
        tok, tref, tfailure = read_outcome(trecord, tposition, "list-tags-for-resource-" + distribution_id, "/output/Tags")
        refs += [tref] if tref else []
        details["resourceTags"] = {tag["Key"]: tag["Value"] for tag in (trecord["output"].get("Tags") or {}).get("Items") or [] if tag.get("Key") is not None and tag.get("Value") is not None} if tok else None
        if tfailure and tfailure.get("missing"):
            missing.append(("ListTagsForResource", tfailure["missing"]))
        elif tfailure:
            issues.append({"kind": "qualification", "stage": "collection", "resource_id": distribution_id, "evidence_refs": [tref],
                           "message": "ListTagsForResource failed for %s: %s: %s. Tags never decide a check." % (distribution_id, tfailure["error"]["code"], tfailure["error"]["message"])})
        if distribution_id in traffic:
            details["download_traffic"], tref = traffic[distribution_id]
            refs += [tref] if tref else []
        if supplied_row is not None:
            saved = lambda amount: amount if type(amount) in (int, float) and math.isfinite(amount) else None  # an amount is a finite number or null; the raw row stays in the evidence
            details["raw_cur_region"] = supplied_row.get("awsRegion")
            details["historical_cur_illustration"] = {
                "label": "illustration", "is_opportunity_savings": False, "source": "supplied", "currency": supplied.get("currency"),
                "window": supplied.get("period"), "annual_cost": saved(supplied_row.get("annualCost")), "public_annual_cost": saved(supplied_row.get("publicAnnualCost")),
                "source_cur_estimated_annual_savings": saved(supplied_row.get("annualSavings")), "native_final_annual_savings": 0,
                "data_transferred": saved(supplied_row.get("dataTransferred")), "unrounded": None, "evidence_ref": "evidence/supplied-costs.json#/resources/%d" % supplied_index}
            refs.append(details["historical_cur_illustration"]["evidence_ref"])
        for name, params, failure in read_errors:  # recorded whatever the decision: a failed check does not hide a failed read
            issues.append({"kind": "read_error", "stage": "collection", "resource_id": distribution_id, "evidence_refs": [failure["ref"]],
                           "message": "%s returned an incomplete response for %s: %s" % (name, distribution_id, failure["error"]["message"]) if failure.get("derived") else
                           "%s returned %s for %s: %s" % (name, failure["error"]["code"], distribution_id, failure["error"]["message"]),
                           "request": {"kind": "aws_read", "name": name, "service": "cloudfront", "region": region, "parameters": params}})
        issues += [{"kind": "missing_evidence", "stage": "collection", "resource_id": distribution_id, "message": "The %s response for %s was not supplied." % (name, distribution_id), "request": request_} for name, request_ in missing]
        decision = "not_recommended" if any(row["status"] == "fail" for row in rows) else "error" if read_errors else "needs_evidence" if missing else "recommended"
        finding = {"id": distribution_id, "resource": {"id": distribution_id, "type": RESOURCE_TYPE, "region": "global"}, "decision": decision, "checks": rows}
        if decision == "recommended":
            finding["recommendation"] = {"action": "enable_compression", "parameters": {"distribution_id": distribution_id, "compress": True, "behaviors": disabled}}
        elif rows[2]["status"] == "pass":  # not recommended for another reason: the setting is provisional (guided.md, stage 3)
            details["provisional_parameters"] = {"distribution_id": distribution_id, "compress": True, "behaviors": disabled}
            finding.update({"technical_eligibility": "eligible"} if decision == "not_recommended" else {})
        costs = {"status": "not_calculated"}
        if known_cost and math.isfinite(cost):
            costs.update(basis="caller_supplied", currency=supplied.get("currency"), current_annual=cost)
            costs.update({"source_period": supplied["period"]} if supplied.get("period") else {})
        finding.update(costs=costs, evidence_refs=refs, details=details)
        findings.append(finding)
    issues += [{"kind": "qualification", "stage": "collection", "message": "GetDistributionConfig failed for %s, which fails the native finder's whole batch; "
                "the other distributions were decided on their own evidence." % ", ".join(batch)}] if batch else []
    unassessed = [row["id"] for row in findings if row["decision"] in ("needs_evidence", "error")]
    assessment["totals"]["decision_counts"] = {d: sum(row["decision"] == d for row in findings) for d in ("recommended", "not_recommended", "needs_evidence", "error")}
    assessment.update(findings=findings, coverage={"inventory_complete": selection is None or all(configs[i][0] is not None and ("output" in configs[i][0] or configs[i][0]["error"]["code"] == "NoSuchDistribution") for i in ids), "discovered_count": len(ids), "assessed_count": len(findings) - len(unassessed), "unassessed_ids": unassessed})
    assessment["run"]["status"] = "complete" if not unassessed else "partial"
    if supplied is not None:
        included = [row for row in findings if row["decision"] == "recommended" and row["details"].get("historical_cur_illustration")]
        def total(field):  # null when no row is included or an included amount is unknown: neither is a total of 0
            amounts = [row["details"]["historical_cur_illustration"][field] for row in included]
            return sum(amounts) if amounts and None not in amounts else None
        assessment["illustration_totals"] = {"label": "illustration", "is_opportunity_savings": False, "currency": supplied.get("currency"), "included_count": len(included),
                                             "excluded_ids": [row["id"] for row in findings if row not in included],
                                             "annual_cost": total("annual_cost"),
                                             "source_cur_estimated_annual_savings": total("source_cur_estimated_annual_savings"),
                                             "native_final_annual_savings": 0}
    below = [row["id"] for row in findings if math.isfinite(row["costs"].get("current_annual", math.nan)) and row["costs"]["current_annual"] <= threshold]
    qualifications += [
        "Technical assessment only. CloudFix does not track savings for this best practice; its savings value of 0 means tracking is disabled, not that the saving is zero.",
        "Actual cost reduction depends on traffic compressibility, content already compressed, cache behavior and the pricing plan. None were measured."]
    qualifications += ["Supplied costs are one billing week of Data Transfer cost, annualized; the 65% figure is an unverified model, never a saving."] if supplied is not None else []
    qualifications += ["CloudFix recommendation history was not available, so RecommendationFinalized was not evaluated."] if history is None else []
    qualifications.append(("Distributions were discovered with a live inventory, not CloudFix's billing-data discovery." if selection is None else
                           "Only the selected distributions were assessed; this is not an account-wide inventory.") +
                          (" BytesDownloaded observations for the billing week %s to %s count GET and HEAD downloads only, not all billed Data Transfer." % (week[0][:10], week[1][:10]) if want_traffic else "") +
                          " CloudFix discovery requires that week's annualized data-transfer cost to exceed the tenant's resourceCostThreshold, " +
                          ("which this run could not apply without billing data, so a distribution listed here may still be absent from CloudFix." if supplied is None else
                           "which is a discovery gate, not a check. Against the recorded threshold %s, the supplied annual cost of %s is at or below it, which CloudFix discovery does not list; "
                           "a distribution without a supplied annual cost may still be absent from CloudFix." % (js_number(threshold), ", ".join(below) or "no distribution")))
    assessment["verification"]["summary"] = ("Recomputed the decision counts from the saved rows, confirmed the null total and opened every evidence reference after writing the files. "
                                             "Rule 3 counts come from the saved configurations and policies in evidence/. AWS was not re-read; workload safety was not assessed.")
    return finish(out_dir, evidence, assessment)

if __name__ == "__main__":
    sys.exit(main())
