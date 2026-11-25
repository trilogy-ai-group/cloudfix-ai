#!/usr/bin/env python3
"""Assess idle QuickSight users with the customer's own AWS read access, as CloudFix's QsRemoveIdleUser finder does.
Standard library only; AWS reads go through the AWS CLI v2. The rules are the ones in ../references/.
Exit 0: complete. Exit 2: the run is partial (evidence missing, a read failed or the self-check found a problem). Exit 1: the run failed."""
import argparse
import concurrent.futures
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from decimal import Decimal, ROUND_HALF_UP

# Skill constants the shared skeleton reads.
MODE_FLAG, PRICING_MODES = "--pricing-mode", ("technical_only", "supplied", "billing")
RESOURCE_NOUN, EMPTY_INVENTORY_NOUN = "user", "QuickSight users"
REPORT_FOOTER = "Nothing was executed; these are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`."
ALLOWED_READS = frozenset({("sts", "GetCallerIdentity"), ("ec2", "DescribeRegions"), ("quicksight", "ListUsers"), ("quicksight", "ListTagsForResource"),
                           ("quicksight", "SearchAnalyses"), ("quicksight", "SearchDashboards"), ("quicksight", "SearchDataSets"), ("quicksight", "SearchDataSources"),
                           ("cloudtrail", "LookupEvents"), ("ce", "GetDimensionValues"), ("ce", "GetCostAndUsage")})  # the finder's assessment reads; aws() refuses anything else

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

SKILL_ID, FINDER_ID, RESOURCE_TYPE = "co-quicksight-idle-users", "QsRemoveIdleUser", "AWS::QuickSight::User"
CHECK_NAMES = ("RecommendationFinalized", "ResourceExists", "ValidateUserRole", "UserNotProtected", "UserNotActive", "HasReceiver",
               "MinimalSavings", "MaximumSavings", "DontFixItOptOut")
ALLOWED_ROLES = ("AUTHOR", "ADMIN", "AUTHOR_PRO", "ADMIN_PRO", "READER_PRO")
KIND_BY_ROLE = {"AUTHOR": "author", "ADMIN": "author", "AUTHOR_PRO": "author_pro", "ADMIN_PRO": "author_pro", "READER_PRO": "reader_pro"}
DEFAULT_CONFIGURATION, IDLE_DAYS_RANGE = {"daysForIdleUser": 30, "protectedUsersPattern": None, "minimalSavings": 0}, (15, 90)
BASIS_BY_MODE = {"supplied": "caller_supplied", "billing": "account_effective_rate"}
OPT_OUT_TAG, SOURCES = "cloudfix:dontFixIt", ("quicksight.amazonaws.com", "signin.amazonaws.com")
SIGN_IN_URL, PACING_SECONDS = "https://%s.quicksight.aws.amazon.com/sn/start", 0.5
THROTTLED, RETRY_DELAYS = ("ThrottlingException", "Throttling", "TooManyRequestsException", "RequestLimitExceeded"), (1, 2, 4)
# The patterns Python's re (ASCII, IGNORECASE) and JavaScript's new RegExp(pattern, 'i') read alike: ASCII literals, punctuation escapes,
# \d \w \s and their negations, \b, . ^ $ |, plain and (?: (?= (?! groups, classes of literals and letter or digit ranges, and one quantifier per item.
JS_SAME = re.compile(r"""(?:[A-Za-z0-9 _,:;!@#%&'"~=<>/-]|\\[^A-Za-z0-9\s]|\\[dDwWsSbtnrfv]|[.^$|)]|\((?:\?[:=!]|(?!\?))|(?:[*+?]|\{\d{1,3}(?:,\d{0,3})?\})\??(?![*+?{])"""
                     r"""|\[\^?-?(?:[A-Za-z0-9]-[A-Za-z0-9]|[A-Za-z0-9 _.,:;@#%'"=<>/!+*?(){}$]|\\[^A-Za-z0-9\s]|\\[dDwWsStnrfv])+-?\])*""")
# In a UserName, the characters the two engines read differently even then: line ends, the spaces only JavaScript's \s holds, and non-BMP characters.
JS_DIFFERENT = re.compile("[\n\r\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\U00010000-\U0010ffff]")
USER_MONTH_UNIT, MONTH_SUFFIX = re.compile(r"^users?[ _-]?(mo|mos|month|months)$"), "-Month"
NO_HISTORY, NOT_RUN = "No framework history was supplied; a standalone run cannot see it.", "Not run after the earlier rejection."
HALTED = "Not run: an earlier check could not be evaluated."
COST_INPUT = {"kind": "input", "name": "A CUR table for the CUR method, a supplied per-user annual cost, or a separate run that requests public prices"}
# (parameter key, operation, filter name, summary list, id field); analyses drop DELETED entries.
ASSET_SEARCHES = (("analyses", "SearchAnalyses", "QUICKSIGHT_USER", "AnalysisSummaryList", "AnalysisId"),
                  ("dashboards", "SearchDashboards", "QUICKSIGHT_USER", "DashboardSummaryList", "DashboardId"),
                  ("dataSets", "SearchDataSets", "DIRECT_QUICKSIGHT_SOLE_OWNER", "DataSetSummaries", "DataSetId"),
                  ("dataSources", "SearchDataSources", "DIRECT_QUICKSIGHT_SOLE_OWNER", "DataSourceSummaries", "DataSourceId"))

def add_skill_arguments(parser):
    parser.add_argument("--billing-profile", help="AWS CLI profile for Cost Explorer (default: --profile)")
    parser.add_argument("--user-arn", action="append", help="assess this QuickSight user ARN (repeatable); default: every user in the default namespace")
    parser.add_argument("--days-for-idle-user", type=int, help="idle window in days, 15 to 90; default 30")
    parser.add_argument("--protected-users-pattern", help="case-insensitive regular expression; a matching UserName is never recommended")
    parser.add_argument("--minimal-savings", type=float, help="annual currency units; default 0")
    parser.set_defaults(pricing_mode="billing")

def describe_change(row):
    receiver, parameters = row["details"]["receiver"]["userName"] or row["details"]["receiver"]["arn"], row["recommendation"]["parameters"]
    listed = sum(len(parameters[key]) for key, _, _, _, _ in ASSET_SEARCHES)
    return "Grant %s owner permissions on %d listed assets, then delete the user" % (receiver, listed) if listed else "Delete the user (no listed assets; receiver %s)" % receiver

def report_headline(assessment, recommended):
    if not recommended:
        return "**Savings not established.**"
    return "**%d idle %s found; %s.**" % (len(recommended), RESOURCE_NOUN if len(recommended) == 1 else RESOURCE_NOUN + "s",
                                          "no cost was requested" if assessment["scope"]["pricing_mode"] == "technical_only" else "savings not totaled")

def report_context(assessment):
    scope, coverage, run_, configuration = assessment["scope"], assessment["coverage"], assessment["run"], assessment["details"].get("configuration") or {}
    unknown = lambda value: "unknown" if value is None else value
    lines = ["Account %s · %s, default namespace · %s · Discovered %s · Assessed %s · Unassessed %d · %s · Observed %s · %s." % (
        unknown(scope["account_id"]), ", ".join(scope["regions"]), "%d selected users" % len(scope["resource_ids"]) if scope["resource_ids"] is not None else "All users requested",
        unknown(coverage["discovered_count"]), unknown(coverage["assessed_count"]), len(coverage["unassessed_ids"]), run_["status"].capitalize(), run_["observed_at"],
        {"self_check": "Self-check", "independent_review": "Independent review", "none": "Not verified"}[assessment["verification"]["method"]])]
    if configuration:
        lines.append("Idle window: %d days. %s" % (configuration["daysForIdleUser"], "Protection pattern: `%s`." % configuration.get("protectedUsersPattern")
                                                  if configuration.get("protectedUsersPattern") else "No protection pattern."))
    return lines

def report_notes(assessment): return []
def report_bullets(assessment): return []

def skill_revision():
    with open(os.path.join(SCRIPT_DIR, "..", "SKILL.md"), encoding="utf-8") as handle:
        return re.search(r'^  version: "([^"]+)"', handle.read(), re.M).group(1)

def check(name, status, value=None, reason=None, native=None):
    row = {"name": name, "status": status, "native_approved": native, "value": value, "reason": reason or None}
    return {key: item for key, item in row.items() if item is not None}

class Pacer:
    """Nonterminal CloudTrail pages in one region start at least PACING_SECONDS after the previous page started."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep, self.last = clock, sleep, None

    def wait(self):
        now = self.clock()
        if self.last is not None and now - self.last < PACING_SECONDS:
            self.sleep(PACING_SECONDS - (now - self.last))
            now = self.clock()
        self.last = now

def lookup(evidence, pacer, file, params, region, profile):
    """One LookupEvents read, paced on a live run. A throttled answer is asked again; every attempt stays in the evidence."""
    for delay in RETRY_DELAYS + (None,):
        evidence.replay or pacer.wait()
        record, position = evidence.read(file, "cloudtrail", "LookupEvents", params, region, profile)
        if evidence.replay or delay is None or (record.get("error") or {}).get("code") not in THROTTLED:  # a replay's read already returns the last attempt
            return record, position
        pacer.sleep(delay)

def matched_event_time(event, user, source, region):
    """(matched, EventTime) under the native predicates; raises ValueError on a malformed CloudTrailEvent payload."""
    payload = json.loads(event.get("CloudTrailEvent") or "{}")
    principal = (payload.get("userIdentity") or {}).get("principalId")
    if principal is not None and user["IdentityType"] != "QUICKSIGHT":
        parts = user["PrincipalId"].split("/")
        if len(parts) < 3:  # collection.md: the source throws here; no substitute identity, and never "no activity"
            raise ValueError("PrincipalId %r has no third component to compare with the event principal" % user["PrincipalId"])
        if not (parts[2] in principal or principal in parts[2]):
            return False, None
    if not isinstance(event.get("EventSource"), str):  # collection.md: a fabricated empty event is never a non-match
        raise ValueError("an event's EventSource is %r, not a string, so whether it matches this user is unknown" % (event.get("EventSource"),))
    if event.get("EventSource") != source:
        return False, None
    if user["IdentityType"] == "QUICKSIGHT":
        if not isinstance(event.get("EventName"), str) or (event["EventName"] == "UserAuthentication" and not event.get("CloudTrailEvent")):
            raise ValueError("a %s event lacks its EventName or its CloudTrailEvent payload, so whether it is this user's sign-in is unknown" % source)
        login = (payload.get("additionalEventData") or {}).get("LoginTo")
        if event.get("EventName") != "UserAuthentication" or login not in (SIGN_IN_URL % region, SIGN_IN_URL % region + "/"):
            return False, None
    return True, event.get("EventTime")

def source_of(user):
    return SOURCES[0] if user["IdentityType"] == "IAM" else SOURCES[1]

def walk_issue(walk, ending="."):
    """The message, evidence and exact next read of one walk that failed or did not reach its last page."""
    detail = ("failed: %s: %s" % (walk["error"]["code"], walk["error"]["message"])) if walk.get("error") else "is incomplete"
    return {"message": "LookupEvents in %s %s%s" % (walk["region"], detail, ending), "evidence_refs": walk["refs"][-1:],
            "request": walk.get("missing") or {"kind": "aws_read", "name": "LookupEvents", "service": "cloudtrail", "region": walk["region"], "parameters": walk["params"]}}

def walk_user(evidence, file, region, user, window, profile, pacer, pages=None):
    """All LookupEvents pages for one user in one region until a match. Returns {finished, eventTime, error|missing, refs}.
    pages holds this region's reads by request: users whose UserName ends in the same name (RoleA/alice, RoleB/alice) share them."""
    name = (user.get("UserName") or "").split("/")[-1]
    params = {"LookupAttributes": [{"AttributeKey": "Username", "AttributeValue": name}], "StartTime": window[0], "EndTime": window[1]}
    result, seen, untimed, pages = {"finished": False, "eventTime": None, "refs": []}, set(), False, {} if pages is None else pages
    if not name:
        return dict(result, missing={"kind": "input", "name": "UserName for " + user["Arn"]})
    while True:
        key = json.dumps(params, sort_keys=True)
        if key not in pages:
            pages[key] = lookup(evidence, pacer, file, params, region, profile)
        record, position = pages[key]
        if record is None:
            return dict(result, missing=position)
        result["refs"].append(evidence.ref(file, position))
        if "error" in record:
            return dict(result, error=record["error"], params=params)
        if not isinstance(record["output"].get("Events"), list):  # collection.md: CloudTrail writes a zero-match page as Events: [], so anything else is not an empty page
            return dict(result, error={"code": "MalformedCloudTrailEvent", "message": "the page has no Events list, so the user's activity is unknown"}, params=params)
        for event in record["output"]["Events"]:
            try:
                matched, event_time = matched_event_time(event, user, source_of(user), region)
                if matched and not isinstance(event_time, (str, type(None))):
                    raise ValueError("a matching event's EventTime is %r, not an ISO 8601 string" % (event_time,))
                event_time = iso_js(event_time) if matched and event_time is not None else None
            except (ValueError, TypeError, AttributeError) as fault:
                return dict(result, error={"code": "MalformedCloudTrailEvent", "message": str(fault)}, params=params)
            if event_time is not None:
                return dict(result, finished=True, eventTime=event_time)
            if matched:
                untimed = True
                break  # a match without EventTime ends this page's loop; later events on it are not examined
        following = record["output"].get("NextToken")
        if following in (None, ""):  # collection.md: absent, null or empty is the last page (any other value that is not a string is an error); an untimed match is never inactivity
            return dict(result, finished=True, **({"error": {"code": "MalformedCloudTrailEvent", "message": "an event that matches this user has no EventTime, so the user's activity is unknown"}, "params": params} if untimed else {}))
        if not isinstance(following, str) or following in seen:  # never a walk without end
            return dict(result, error={"code": "InvalidPageToken", "message": "LookupEvents returned a repeated or non-string NextToken, so the walk is incomplete"}, params=params)
        seen.add(following)
        params = dict(params, NextToken=following)

def scan_region(evidence, region, users, window, profile):
    """One worker per region: the two probes, then the walks of every user whose source is due here, in inventory order."""
    probes, walks, pacer, pages = {}, {}, Pacer(), {}
    for source in SOURCES:
        params = {"LookupAttributes": [{"AttributeKey": "EventSource", "AttributeValue": source}], "StartTime": window[0], "EndTime": window[1], "MaxResults": 1}
        record, position = lookup(evidence, pacer, "activity-probes-" + region, params, region, profile)  # the probes count against the same CloudTrail rate as the walk pages
        listed = (record or {}).get("output", {}).get("Events")
        events = listed or []
        if record is None:
            probes[source] = {"missing": position}
        elif "error" in record or not (isinstance(events, list) and all(isinstance(event, dict) for event in events[:1])):  # an unusable probe is an error, not an empty source
            probes[source] = {"error": record.get("error") or {"code": "MalformedCloudTrailEvent", "message": "The probe's Events is not a list whose first entry is an object."},
                              "ref": evidence.ref("activity-probes-" + region, position), "params": params}
        else:
            probes[source] = {"due": bool(events and events[0].get("EventTime") is not None)}
            following = record["output"].get("NextToken") not in (None, "")
            if not probes[source]["due"] and (events or not isinstance(listed, list) or following):  # collection.md: an event without EventTime, an output without an Events list or an empty page with a NextToken is not "no activity in this region"
                probes[source]["untimed"] = {"error": {"code": "MalformedCloudTrailEvent", "message": "the %s probe returned %s, so activity in this region is unknown" % (
                                                 source, "an event without EventTime" if events else "no Events list" if not isinstance(listed, list) else "an empty page with a NextToken")},
                                             "refs": [evidence.ref("activity-probes-" + region, position)], "params": params}
    for user in users:
        if probes[source_of(user)].get("due"):
            started = time.monotonic()
            walks[user["Arn"]] = walk_user(evidence, "activity-walks-" + region, region, user, window, profile, pacer, pages)
            walks[user["Arn"]]["seconds"] = round(time.monotonic() - started, 3)
    return region, probes, walks

def activity_summary(user, regions, probes, walks):
    """coverage-and-cache.md, fresh mode: the UserNotActive value and reason, the due regions and the read failures behind a decline."""
    due = [region for region in regions if probes[region][source_of(user)].get("due")]
    results = [dict(walks[region].get(user["Arn"], {}), region=region) for region in due]
    failures = [walk for walk in results if walk.get("error") or walk.get("missing")]
    failures += [dict(probes[region][source_of(user)]["untimed"], region=region) for region in regions if "untimed" in probes[region][source_of(user)]]
    if not due:
        return {"reason": "no-region-with-event-source"}, due, failures
    times = [walk["eventTime"] for walk in results if walk.get("eventTime")]
    summary = {"finishedProcessing": all(walk.get("finished") for walk in results)}
    if times:
        summary.update(eventTime=max(times, key=parse_time), reason="activity-identified")
    else:
        summary["reason"] = "no-activity-identified" if summary["finishedProcessing"] else "activity-not-fully-read"
    return summary, due, failures

def read_user_assets(evidence, file, user, account, region, profile):
    """ListTagsForResource, then the four asset searches, every page, for one not-rejected user."""
    out = {"tags": None, "assets": {}, "errors": {}, "missing": {}}
    record, position = evidence.read(file, "quicksight", "ListTagsForResource", {"ResourceArn": user["Arn"]}, region, profile)
    if record is None:
        out["missing"]["ListTagsForResource"] = position
    elif "error" in record:
        out["errors"]["ListTagsForResource"] = (record["error"], evidence.ref(file, position))
    elif not all(isinstance(tag, dict) for tag in record["output"].get("Tags") or []):  # validation.md: invalid tag evidence leaves protection unknown
        out["errors"]["ListTagsForResource"] = ({"code": "MalformedResponse", "message": "a Tags entry is not an object"}, evidence.ref(file, position))
    else:
        out["tags"] = record["output"].get("Tags") or []
    for key, operation, filter_name, collection, id_field in ASSET_SEARCHES:
        params = {"AwsAccountId": account, "Filters": [{"Operator": "StringEquals", "Name": filter_name, "Value": user["Arn"]}]}
        records, failure = evidence.pages(file, "quicksight", operation, params, region, profile)
        if failure and "error" in failure:
            out["errors"][operation] = (failure["error"], failure["ref"])
        elif failure:
            out["missing"][operation] = failure["missing"]
        elif not all(isinstance(record["output"].get(collection), list) and all(isinstance(item, dict) for item in record["output"][collection]) for record, _ in records):  # an unusable search, never a shorter asset list
            out["errors"][operation] = ({"code": "MalformedResponse", "message": "a page has no %s list, or an entry in it is not an object" % collection}, evidence.ref(file, records[-1][1]))
        else:
            items = [item for record, _ in records for item in record["output"][collection]]
            out["assets"][key] = [item[id_field] for item in items if item.get(id_field) is not None and (key != "analyses" or item.get("Status") != "DELETED")]
    return out

def usage_kind(name):
    """billing-cost-explorer.md: the subscription kind a usage type's own name states, and the deciding part of the name."""
    evidence = name.split("-", 1)[1] if re.match(r"^[A-Z0-9]+-", name) else name
    tokens = [token.lower() for token in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", evidence)]
    subscriber = any(token in tokens for token in ("user", "author", "admin"))
    if "addon" in "".join(tokens):  # an add-on is never a subscription kind
        return None, evidence
    if "pro" in tokens:
        return ("reader_pro" if "reader" in tokens else "author_pro" if subscriber else None), evidence
    return ("author" if "reader" not in tokens and subscriber else None), evidence

def is_user_month(unit, name):
    lowered = unit.lower() if isinstance(unit, str) else ""
    return bool(USER_MONTH_UNIT.match(lowered)) or (lowered in ("user", "users") and name.endswith(MONTH_SUFFIX))

def days_in_month(date):
    first = datetime.date(int(date[:4]), int(date[5:7]), 1)
    return ((first + datetime.timedelta(days=31)).replace(day=1) - first).days

def billing_window(day):
    """The finder's own CUR window: Monday of the week of day - 31 days to Monday of the week of day - 1 day, end exclusive (28 or 35 days)."""
    monday = lambda offset: day - datetime.timedelta(days=offset) - datetime.timedelta(days=(day - datetime.timedelta(days=offset)).weekday())
    return monday(31), monday(1)

def collect_cost_explorer(evidence, account, as_of, profile):
    """The effective monthly rate per subscription kind. Returns (result, failure); failure.kind is read_error, calculation_error or missing_evidence."""
    try:
        return cost_explorer_rates(evidence, account, as_of, profile)
    except (AttributeError, KeyError, TypeError, ValueError) as fault:  # a response of another shape is rejected, never repaired
        return None, {"kind": "calculation_error", "message": "Cost Explorer returned data the method rejects: %s: %s." % (type(fault).__name__, fault)}

def cost_explorer_rates(evidence, account, as_of, profile):
    first, last = billing_window(as_of.date())
    recorded = [record["input"]["TimePeriod"] for record in evidence.files.get("cost-explorer", []) if record.get("service") == "ce"][:1] if evidence.replay else []
    for period in recorded:  # billing-cost-explorer.md: a replay keeps its recorded window
        first, last = datetime.date.fromisoformat(period["Start"]), datetime.date.fromisoformat(period["End"])
    window_days, start, end = (last - first).days, first.isoformat(), last.isoformat()
    common = [{"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account]}}, {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}}]
    identity, position = evidence.read("cost-explorer", "sts", "GetCallerIdentity", {}, "us-east-1", profile)
    if identity is None or "error" in identity:
        return None, dict({"kind": "read_error" if identity else "missing_evidence", "message": "The Cost Explorer caller identity could not be verified.",
                           "request": {"kind": "aws_read", "name": "GetCallerIdentity", "service": "sts", "region": "us-east-1", "parameters": {}}},
                          **({"error": identity["error"], "ref": evidence.ref("cost-explorer", position)} if identity else {}))

    def paged(operation, params):
        records, failure = evidence.pages("cost-explorer", "ce", operation, params, "us-east-1", profile, token="NextPageToken")
        return records, failure and dict({"kind": "read_error" if "error" in failure else "missing_evidence", "message": operation + " did not complete.",
                                          "request": failure.get("missing") or {"kind": "aws_read", "name": operation, "service": "ce", "region": "us-east-1", "parameters": params}},
                                         **({"error": failure["error"], "ref": failure["ref"]} if "error" in failure else {}))
    period = {"TimePeriod": {"Start": start, "End": end}}
    records, failure = paged("GetDimensionValues", dict(period, Dimension="OPERATION", Context="COST_AND_USAGE", SearchString="QS-User-", Filter={"And": common}))
    if failure:
        return None, failure
    operations = [value["Value"] for record, _ in records for value in record["output"].get("DimensionValues") or [] if str(value.get("Value", "")).startswith("QS-User-")]
    result = {"window": {"start": start + "T00:00:00Z", "end": end + "T00:00:00Z"}, "billingAccount": identity["output"].get("Account"), "operations": operations, "classification": [], "kinds": {}}
    if not operations:
        return result, {"kind": "missing_evidence", "message": "Cost Explorer returned no QS-User- operation for this account in %s to %s." % (start, end), "request": COST_INPUT}
    scoped = common + [{"Dimensions": {"Key": "OPERATION", "Values": operations}}]
    records, failure = paged("GetDimensionValues", dict(period, Dimension="USAGE_TYPE", Context="COST_AND_USAGE", Filter={"And": scoped}))
    if failure:
        return None, failure
    units = {}
    for record, _ in records:
        for value in record["output"].get("DimensionValues") or []:
            name, unit = value.get("Value"), (value.get("Attributes") or {}).get("unit")
            if units.get(name, unit) != unit:
                return None, {"kind": "calculation_error", "message": "Usage type %s was returned with two different units." % name}
            if name not in units:
                units[name] = unit
                kind, mapping = usage_kind(name) if is_user_month(unit, name) else (None, None)
                result["classification"].append({"usage_type": name, "unit": unit, "per_user": is_user_month(unit, name), "kind": kind, "mapping_evidence": mapping})
    kept = {row["usage_type"]: row["kind"] for row in result["classification"] if row["kind"]}
    if not kept:
        return result, {"kind": "missing_evidence", "message": "Cost Explorer returned no per-user subscription usage type mapped to a kind.", "request": COST_INPUT}
    records, failure = paged("GetCostAndUsage", dict(period, Granularity="DAILY", Metrics=["NetUnblendedCost", "UnblendedCost", "UsageQuantity"], GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
                                                      Filter={"And": scoped + [{"Dimensions": {"Key": "USAGE_TYPE", "Values": sorted(kept)}}]}))
    if failure:
        return None, failure
    days, totals = {}, {kind: {"selectedCost": Decimal(0), "unblendedCost": Decimal(0), "userMonths": Decimal(0), "usage_types": sorted(n for n, k in kept.items() if k == kind),
                              "net_fallback_periods": 0, "byDay": {}} for kind in set(kept.values())}
    try:
        for record, _ in records:
            if not isinstance(record["output"].get("ResultsByTime"), list):
                raise ValueError("a page has no ResultsByTime list")
            for item in record["output"]["ResultsByTime"]:
                window = item.get("TimePeriod") or {}
                if (parse_time(window["End"] + "T00:00:00Z") - parse_time(window["Start"] + "T00:00:00Z")).days != 1 or not (start <= window["Start"] < end) or not isinstance(item.get("Estimated"), bool):
                    raise ValueError("a period is not one day inside the window with a boolean Estimated")
                seen = days.setdefault(window["Start"], {"Estimated": item["Estimated"], "types": set()})
                if seen["Estimated"] != item["Estimated"]:
                    raise ValueError("a day continued on the next page with a different Estimated value")
                for group in item.get("Groups") or []:
                    keys, metrics = group.get("Keys") or [], group["Metrics"]
                    if len(keys) != 1 or keys[0] not in kept or keys[0] in seen["types"]:
                        raise ValueError("a group does not name exactly one requested usage type once per day")
                    seen["types"].add(keys[0])
                    net, gross, quantity = metrics.get("NetUnblendedCost"), metrics["UnblendedCost"], metrics["UsageQuantity"]
                    if net is not None and not (isinstance(net, dict) and "Amount" in net):  # billing-cost-explorer.md: an empty or malformed net field is no permission to fall back
                        raise ValueError("a NetUnblendedCost field is empty or malformed")
                    net = net or {}
                    if gross.get("Unit") != "USD" or (net.get("Amount") is not None and net.get("Unit") != "USD"):
                        raise ValueError("a cost unit is not USD")
                    if quantity.get("Unit", "N/A").lower() not in ("n/a", str(units[keys[0]]).lower()):
                        raise ValueError("a quantity unit differs from the verified unit of %s" % keys[0])
                    day_gross, day_quantity = Decimal(gross["Amount"]), Decimal(quantity["Amount"])  # CE amounts are decimal strings: exact sums
                    day_selected = day_gross if net.get("Amount") is None else Decimal(net["Amount"])
                    if not all(v.is_finite() for v in (day_gross, day_quantity, day_selected)) or day_quantity < 0 or (day_quantity == 0 and (day_gross or day_selected)):
                        raise ValueError("an amount is not finite, a quantity is negative, or a zero quantity carries a cost")
                    kind = totals[kept[keys[0]]]
                    kind["selectedCost"], kind["unblendedCost"], kind["userMonths"] = kind["selectedCost"] + day_selected, kind["unblendedCost"] + day_gross, kind["userMonths"] + day_quantity
                    kind["net_fallback_periods"] += net.get("Amount") is None
                    cost, quantity = kind["byDay"].get(window["Start"], (Decimal(0), Decimal(0)))
                    kind["byDay"][window["Start"]] = (cost + day_selected, quantity + day_quantity)
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError) as fault:
        return None, {"kind": "calculation_error", "message": "GetCostAndUsage returned data the method rejects: %s." % fault}
    returned = len(days)
    result["coverage"] = {"requested_days": window_days, "returned_days": returned, "missing_days": window_days - returned, "estimated_days": sum(d["Estimated"] for d in days.values())}
    for kind, sums in totals.items():
        by_day = sums.pop("byDay")
        sums["days_with_usage"] = sum(1 for _, quantity in by_day.values() if quantity > 0)  # a zero quantity is no usage
        if sums["userMonths"] > 0 and sums["selectedCost"] < 0:
            return None, {"kind": "calculation_error", "message": "The %s kind's selected cost over the window is negative (%s USD)." % (kind, sums["selectedCost"])}
        if returned == window_days and sums["userMonths"] > 0:
            sums["effectiveMonthlyRate"] = float(sums["selectedCost"] / sums["userMonths"])
            # One subscription's billed cost for each day, summed over the window, then the finder's ROUND(sum / 30 * 365, 2).
            sums["windowCostPerUser"] = sum(cost / (quantity * days_in_month(date)) for date, (cost, quantity) in by_day.items() if quantity > 0)
            sums["annualCostPerUser"] = float((sums["windowCostPerUser"] / 30 * 365).quantize(Decimal("0.01"), ROUND_HALF_UP))
            sums["windowCostPerUser"] = float(sums["windowCostPerUser"])
            sums["impliedSubscriptions"] = float(sums["userMonths"] * 365 / (12 * returned)) if returned else None
            result["kinds"][kind] = sums
        for key in ("selectedCost", "unblendedCost", "userMonths"):
            sums[key] = float(sums[key])
    if returned != window_days:  # the finder sums every day of the window: an absent day is missing evidence, never zero cost
        return result, {"kind": "missing_evidence", "message": "Cost Explorer returned %d of the %d days of the billing window (%s to %s, end exclusive), so no billed cost is calculated." % (returned, window_days, start, end),
                        "request": {"kind": "aws_read", "name": "GetCostAndUsage", "service": "ce", "region": "us-east-1", "parameters": records[0][0]["input"]}}  # the first page's request holds no page token
    return result, None

def run(args):
    out_dir, issues, qualifications = args.out, [], []
    if args.replay:
        evidence = Evidence(args.replay, True)
        saved = evidence.files.get("run-metadata")
        if saved is None:
            sys.stderr.write("The replay folder has no run-metadata.json\n")
            return 1
        metadata, request = (saved.get("run_metadata"), saved.get("request")) if isinstance(saved, dict) else (None, None)
        if not isinstance(metadata, dict) or not isinstance(request, dict):
            raise InputError("run-metadata.json must be an object with a run_metadata object and a request object")
        evidence.directory = os.path.join(out_dir, "evidence")
    else:
        evidence = Evidence(os.path.join(out_dir, "evidence"), False)
        configuration = dict(DEFAULT_CONFIGURATION, **{key: value for key, value in (("daysForIdleUser", args.days_for_idle_user),
                             ("protectedUsersPattern", args.protected_users_pattern), ("minimalSavings", args.minimal_savings)) if value is not None})
        metadata = {"finderId": FINDER_ID, "accountId": args.account, "region": args.region, "asOf": iso_js(parse_time(args.as_of) if args.as_of else datetime.datetime.now(datetime.timezone.utc)),
                    "evaluation_timezone": "UTC", "configuration": configuration, "run_classification": "standard", "experiment": None,
                    "configuration_overrides": {key: value for key, value in configuration.items() if value != DEFAULT_CONFIGURATION[key]}}
        request = {"pricing_mode": args.pricing_mode, "resource_ids": args.user_arn, "profile": args.profile, "billing_profile": args.billing_profile}
        evidence.files["run-metadata"] = {"run_metadata": metadata, "request": request}
        if args.pricing_mode == "supplied":
            if not args.costs:
                sys.stderr.write("--pricing-mode supplied needs --costs FILE\n")
                return 1
            try:
                with open(args.costs, encoding="utf-8") as handle:
                    evidence.files["supplied-costs"] = json.load(handle)
            except ValueError as fault:
                raise InputError("--costs is not a JSON file: %s" % fault)
    account, region, configuration, pricing_mode = metadata.get("accountId"), metadata.get("region"), metadata.get("configuration"), request.get("pricing_mode")
    try:
        as_of = parse_time(metadata.get("asOf"))
    except (AttributeError, TypeError, ValueError):
        as_of = None
    if not (as_of and isinstance(account, str) and isinstance(region, str) and region and isinstance(configuration, dict)
            and isinstance(configuration.get("protectedUsersPattern"), (str, type(None))) and pricing_mode in PRICING_MODES):
        raise InputError("run-metadata.json needs asOf (ISO 8601), accountId, region, a configuration whose protectedUsersPattern is a string or null, and pricing_mode one of %s" % ", ".join(PRICING_MODES))
    selection, profile, days = request.get("resource_ids"), request.get("profile"), configuration.get("daysForIdleUser")
    number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if not (number(days) and isinstance(days, int) and IDLE_DAYS_RANGE[0] <= days <= IDLE_DAYS_RANGE[1] and number(configuration.get("minimalSavings"))):
        raise InputError("daysForIdleUser must be an integer from %d to %d and minimalSavings finite" % IDLE_DAYS_RANGE)
    if selection is not None and not (isinstance(selection, list) and all(isinstance(arn, str) and len(arn.split(":")) >= 6 for arn in selection)):
        raise InputError("resource_ids must be a list of QuickSight user ARNs")
    costs = evidence.files.get("supplied-costs", {"resources": []}) if pricing_mode == "supplied" else {"resources": []}
    if not (isinstance(costs, dict) and costs.get("currency", "USD") == "USD" and isinstance(costs.get("resources", []), list) and all(
            isinstance(row, dict) and isinstance(row.get("resourceId"), str) and number(row.get("annualCost")) and 0 <= row["annualCost"] <= 1e12 for row in costs.get("resources", []))
            and len({row["resourceId"] for row in costs.get("resources", [])}) == len(costs.get("resources", []))):
        raise InputError("the supplied costs must be {\"currency\": \"USD\", \"resources\": [{\"resourceId\": <user arn>, \"annualCost\": <number from 0 to 1e12>}]}, one entry per user")
    assessment = {
        "schema_version": "1.0", "skill": {"id": SKILL_ID, "revision": skill_revision(), "source_ref": None},
        "run": {"observed_at": metadata["asOf"], "mode": "local_code", "status": "complete"},
        "scope": {"account_id": None, "regions": [region], "resource_ids": selection and list(dict.fromkeys(selection)), "pricing_mode": pricing_mode},  # an id named twice is one id
        "coverage": {"inventory_complete": False, "discovered_count": None, "assessed_count": None, "unassessed_ids": []},
        "findings": [], "totals": {"decision_counts": {"recommended": 0, "not_recommended": 0, "needs_evidence": 0, "error": 0}, "annual_savings": None},
        "issues": issues, "qualifications": qualifications, "verification": {"method": "self_check", "summary": ""},
        "evidence_refs": ["evidence/run-metadata.json"],
        "details": {"finderId": FINDER_ID, "namespace": "default", "discovery_method": "list_users" if selection is None else "explicit_selection",
                    "activity_mode": "fresh", "configuration": configuration, "evaluation_timezone": "UTC", "framework_context_complete": False},
        "execution_status": "not_executed"}

    def failed(stage, message, failure, request=None, fatal=False):
        issue = {"kind": "read_error" if failure and failure.get("error") else "missing_evidence", "stage": stage, "resource_id": None, "message": message}
        if failure and failure.get("error"):
            issue["message"] = "%s returned %s (%s). %s" % (failure.get("operation", "The read"), failure["error"]["code"], failure["error"]["message"], message)
            issue["evidence_refs"] = [failure["ref"]]
        if request or (failure or {}).get("missing"):
            issue["request"] = request or failure["missing"]
        issues.append(issue)
        assessment["run"]["status"] = "failed" if fatal or issue["kind"] == "read_error" else "partial"
        assessment["verification"]["summary"] = assessment["verification"]["summary"] or "Read the saved identity record back. No user was listed and no finder check ran."
        return finish(out_dir, evidence, assessment)
    # 1. identity
    identity, position = evidence.read("get-caller-identity", "sts", "GetCallerIdentity", {}, region, profile)
    assessment["evidence_refs"] += ["evidence/get-caller-identity.json"] if "get-caller-identity" in evidence.files else []  # a file never written is no evidence
    if identity is None or "error" in identity:
        return failed("scope", "Identity could not be verified.", {"error": identity["error"], "ref": evidence.ref("get-caller-identity", 0), "operation": "sts:GetCallerIdentity"} if identity else {"missing": position})
    if identity["output"].get("Account") != account:
        return failed("scope", "The credentials belong to account %s, not the requested %s." % (identity["output"].get("Account"), account), None, {"kind": "input", "name": "credentials for account " + account}, fatal=True)
    assessment["scope"]["account_id"] = account
    # 2. users; a denied or unfinished list ends the run: nothing that depends on users is read
    list_params = {"AwsAccountId": account, "Namespace": "default"}
    records, failure = evidence.pages("list-users", "quicksight", "ListUsers", list_params, region, profile)
    assessment["evidence_refs"] += ["evidence/list-users.json"] if "list-users" in evidence.files else []
    if failure:
        decision = "error" if "error" in failure else "needs_evidence"  # a missing page is absent input, not a failed read
        for arn in selection or []:
            assessment["findings"].append({"id": arn, "resource": {"id": arn, "type": RESOURCE_TYPE, "region": arn.split(":")[3]}, "decision": decision,
                                           "checks": [check(name, "not_evaluated", reason="ListUsers failed or is incomplete.") for name in CHECK_NAMES], "costs": {"status": "not_calculated"}})
        assessment["coverage"].update(discovered_count=len(selection) if selection else None, assessed_count=0, unassessed_ids=list(selection or []))
        assessment["totals"]["decision_counts"][decision] = len(selection or [])
        code, request = failure.get("error", {}).get("code"), failure.get("missing") or {"kind": "aws_read", "name": "ListUsers", "service": "quicksight", "region": region, "parameters": list_params}
        if code == "ResourceNotFoundException":  # collection.md: no default namespace here; the role can read, so asking for a permission would be the wrong step
            access, known = "No permission is missing: AWS answered that it found no QuickSight account or default namespace for this account in %s." % region, "Whether the account has QuickSight users is not established."
            step = "Next step: confirm that account %s has a QuickSight subscription and that %s is its identity region, then run the assessment in the identity region." % (account, region)
            request = {"kind": "input", "name": "the identity region of the account's QuickSight subscription, or confirmation that the account has none"}
        elif code and not any(word in code for word in ("AccessDenied", "Unauthorized")):
            access, known = "No missing permission was reported: the read failed with %s." % code, "This is not an empty account."
            step = "Next read: quicksight ListUsers in %s (AwsAccountId %s, Namespace default), or a recorded user list." % (region, account)
        else:
            access, known = "The assessment can run once the customer allows quicksight:ListUsers for the role in use or supplies a recorded user list.", "This is not an empty account."
            step = "Next read: quicksight ListUsers in %s (AwsAccountId %s, Namespace default), once the customer allows it for the role in use, or a recorded user list." % (region, account)
        qualifications.append("No user, count, plan or saving was inferred. Access was not broadened. " + access)
        assessment["verification"]["summary"] = "Read the saved identity and ListUsers records back and confirmed that no finding, count or saving was written for a user that was not listed. No finder check ran."
        return failed("collection", "The users in the default namespace could not be listed, so none was assessed. %s %s" % (known, step), dict(failure, operation="quicksight:ListUsers"), request)
    listed, by_arn, positions, dropped, duplicated, incomplete = [], {}, {}, 0, 0, set()
    for record, page in records:
        if not isinstance(record["output"].get("UserList"), list):  # a missing key, an object or a string would list no user and read as an empty account
            return failed("collection", "ListUsers returned no UserList, or one that is not a list, so no user was assessed. This is not an empty account.",
                          {"error": {"code": "InvalidResponse", "message": "UserList is not a list"}, "ref": evidence.ref("list-users", page), "operation": "quicksight:ListUsers"},
                          {"kind": "aws_read", "name": "ListUsers", "service": "quicksight", "region": region, "parameters": list_params})
        for index, user in enumerate(record["output"].get("UserList") or []):
            listed.append(user)
            if isinstance(user, dict) and all(user.get(key) is not None for key in ("Arn", "PrincipalId", "Role", "IdentityType")):
                duplicated += user["Arn"] in by_arn
                by_arn[user["Arn"]], positions[user["Arn"]] = user, evidence.ref("list-users", page, "/output/UserList/%d" % index)  # collection.md: the last duplicate
            else:
                dropped += 1
                incomplete.update([user["Arn"]] if isinstance(user, dict) and isinstance(user.get("Arn"), str) else [])
    if listed and not by_arn:  # users were listed, but none can be read: never "no users found"
        return failed("collection", "ListUsers returned %d user records, none with Arn, PrincipalId, Role and IdentityType, so no user was assessed. This is not an empty account." % len(listed),
                      {"error": {"code": "InvalidResponse", "message": "no UserList entry is a complete user record"}, "ref": evidence.ref("list-users", records[0][1]), "operation": "quicksight:ListUsers"},
                      {"kind": "aws_read", "name": "ListUsers", "service": "quicksight", "region": region, "parameters": list_params})
    scope_ids = list(dict.fromkeys(selection if selection is not None else by_arn))
    scanned = [arn for arn, user in by_arn.items() if user["Role"] in ALLOWED_ROLES]  # ponytail: a duplicated ARN is walked once
    assessment["details"].update(users_listed=len(listed), users_dropped=dropped, allowed_role_users_scanned=len(scanned))
    if duplicated:
        issues.append({"kind": "qualification", "stage": "collection", "message": "%d listed user records repeated the ARN of an earlier record; the last record of each ARN was assessed, once." % duplicated})
    if dropped:
        issues.append({"kind": "qualification", "stage": "collection", "message": "%d listed user records lacked Arn, PrincipalId, Role or IdentityType and were not normalized; they are kept in evidence/list-users.json." % dropped})
    # 3. regions, probes and walks for every allowed-role user, one worker per region
    window, regions, probes, walks, activity_failure = (iso_js(as_of - datetime.timedelta(days=days)), iso_js(as_of)), [], {}, {}, None
    if scanned:
        records, failure = evidence.pages("describe-regions", "ec2", "DescribeRegions", {}, region, profile)
        assessment["evidence_refs"] += ["evidence/describe-regions.json"] if "describe-regions" in evidence.files else []
        items = [item for record, _ in records for item in record.get("output", {}).get("Regions") or []]
        if not failure and not (all(isinstance(record["output"].get("Regions"), list) for record, _ in records)
                                and all(isinstance(item, dict) and isinstance(item.get("RegionName"), (str, type(None))) for item in items)):  # a dropped region would be unread coverage
            failure = {"error": {"code": "MalformedResponse", "message": "a page has no Regions list, or a Regions entry is not an object with a string RegionName"}, "ref": evidence.ref("describe-regions", records[-1][1])}
        activity_failure = dict(failure, operation="DescribeRegions", region=region) if failure else None
        regions = [item["RegionName"] for item in items if isinstance(item, dict) and isinstance(item.get("RegionName"), str)]
        assessment["details"]["activity_regions"] = regions
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(regions))) as pool:
            for name, probe, walk in pool.map(lambda r: scan_region(evidence, r, [by_arn[arn] for arn in scanned], window, profile), [] if activity_failure else regions):
                probes[name], walks[name] = probe, walk
        assessment["evidence_refs"] += ["evidence/activity-probes-%s.json" % name for name in regions if "activity-probes-" + name in evidence.files]
        for name in regions:
            for source in SOURCES:
                if not activity_failure and "due" not in probes[name][source]:
                    activity_failure = dict(probes[name][source], operation="LookupEvents", region=name)  # a probe error propagates: the whole scan stops
        if not evidence.replay:
            evidence.files["timings"] = {"walk_seconds": {name: {arn: walk["seconds"] for arn, walk in walks[name].items()} for name in walks}}
    summaries = {arn: activity_summary(by_arn[arn], regions, probes, walks) for arn in scanned} if not activity_failure else {}
    active = sorted((arn for arn in summaries if summaries[arn][0].get("eventTime")), key=lambda arn: parse_time(summaries[arn][0]["eventTime"]))
    # receiver-and-assets.md: the receiver is the latest event of every scanned user, so another user's unread walk leaves it unknown
    receiver = by_arn[active[-1]] if active and not any(summaries[arn][2] for arn in scanned if arn != active[-1]) else None
    for arn in summaries:  # one issue per failed walk, under its own user; an identified event decides an active user, so its failed read in another region only qualifies
        for walk in summaries[arn][2]:
            issues.append(dict({"kind": "qualification" if arn in active else "read_error" if walk.get("error") else "missing_evidence", "stage": "collection", "resource_id": arn},
                               **walk_issue(walk, ". Activity was identified in another region, so UserNotActive does not depend on this read." if arn in active else ".")))
    if activity_failure:
        detail = ("failed: %s: %s" % (activity_failure["error"]["code"], activity_failure["error"]["message"])) if activity_failure.get("error") else "is missing from the evidence"
        issues.append(dict({"kind": "read_error" if activity_failure.get("error") else "missing_evidence", "stage": "collection", "resource_id": None,
                            "message": "%s in %s %s; no activity could be read, so UserNotActive was not evaluated for any user." % (activity_failure["operation"], activity_failure["region"], detail),
                            "request": activity_failure.get("missing") or {"kind": "aws_read", "name": activity_failure["operation"], "service": "cloudtrail" if activity_failure["operation"] == "LookupEvents" else "ec2", "region": activity_failure["region"],
                                        "parameters": activity_failure.get("params", {})}},
                           **({"evidence_refs": [activity_failure["ref"]]} if activity_failure.get("ref") else {})))
    # 4. the checks through HasReceiver, then the reads of the users still standing
    pattern, invalid_pattern, unlike_js = None, False, False
    if configuration.get("protectedUsersPattern") and str(configuration.get("protectedUsersPattern")).strip():
        unlike_js = not JS_SAME.fullmatch(configuration["protectedUsersPattern"])
        try:
            pattern = re.compile(configuration.get("protectedUsersPattern"), re.I | re.A)  # as JavaScript without the u flag: \w, \d, \b and the case folding are ASCII
        except re.error:
            invalid_pattern = True
    rows = {}
    for arn in scope_ids:
        user, checks, needs, errors, halt, confidence = by_arn.get(arn), [check("RecommendationFinalized", "not_evaluated", reason=NO_HISTORY)], [], [], False, None
        # stop: a native fail-fast rejection; halt: a check could not be evaluated, so later checks did not run, but it is not a rejection
        if user is None and arn in incomplete:  # listed, but not a record the checks can read: an error, never "does not exist"
            checks.append(check("ResourceExists", "not_evaluated", {"resourceId": arn}, "The user list holds this ARN in a record that lacks PrincipalId, Role or IdentityType."))
            errors.append({"message": "The ListUsers record of this ARN lacks PrincipalId, Role or IdentityType, so the user could not be assessed.",
                           "request": {"kind": "aws_read", "name": "ListUsers", "service": "quicksight", "region": region, "parameters": list_params}})
            halt = True
        elif user is None:
            checks.append(check("ResourceExists", "fail", {"resourceId": arn}, "The completed user list does not hold this ARN."))
        else:
            checks.append(check("ResourceExists", "pass", {"resourceId": arn}))
            checks.append(check("ValidateUserRole", "pass" if user["Role"] in ALLOWED_ROLES else "fail", {"role": user["Role"]},
                                None if user["Role"] in ALLOWED_ROLES else "%s is not one of %s." % (user["Role"], ", ".join(ALLOWED_ROLES))))
        stop = checks[-1]["status"] != "pass"
        if not stop:
            value = {"userName": user.get("UserName")}
            if user.get("UserName") is not None and (unlike_js or (pattern and JS_DIFFERENT.search(user["UserName"]))):  # never a guess at the finder's answer: a protected user must not become a candidate
                checks.append(check("UserNotProtected", "not_evaluated", value, "This script cannot evaluate protectedUsersPattern on this UserName exactly as the finder's JavaScript RegExp does, so protection is unresolved."))
                needs.append({"message": "The configured protectedUsersPattern, or this UserName, holds something Python and JavaScript regular expressions read differently, so protection is unresolved.",
                              "request": {"kind": "input", "name": "a protectedUsersPattern this script reads as JavaScript does (references/validation.md), or the guided path for this user"}})
            elif invalid_pattern:
                checks.append(check("UserNotProtected", "not_evaluated", value, "protectedUsersPattern is not a valid regular expression; the finder ignores it, but the protection intent is unresolved.", True))
                needs.append({"message": "The configured protectedUsersPattern does not compile, so protection is unresolved.", "request": {"kind": "input", "name": "a valid protectedUsersPattern"}})
            elif pattern and user.get("UserName") is not None:
                value["protectedUsersPattern"] = pattern.pattern
                stop = bool(pattern.search(user["UserName"]))
                checks.append(check("UserNotProtected", "fail" if stop else "pass", value, "UserName matches the protection pattern." if stop else None))
            else:
                checks.append(check("UserNotProtected", "pass", value))
        if not stop and activity_failure:
            checks.append(check("UserNotActive", "not_evaluated", reason="The activity reads failed; see the run-level issue.", native=False))
            (errors if activity_failure.get("error") else needs).append(None)  # the run-level issue covers this row
            halt = True
        elif not stop:
            summary, due, failures = summaries[arn]
            value = dict(summary, windowDays=days, reason=summary["reason"])
            verified = user["IdentityType"] in ("IAM", "QUICKSIGHT")
            read = sum(1 for name in due if walks[name].get(arn, {}).get("finished") and not walks[name][arn].get("error"))
            confidence = ("%s identity; the Username lookup is shown to observe this user's %s events; %d of %d due regions read to their last page or to a matching event." % (user["IdentityType"], source_of(user), read, len(due))
                          if verified else "Unverified event model; native no-match only.")
            if value["reason"] == "activity-identified":
                checks.append(check("UserNotActive", "fail", value, "Activity was identified at %s." % value["eventTime"]))
                stop = True
            elif value["reason"] == "no-activity-identified" and failures:  # every due walk finished, but a match or a region's probe was unusable
                halt = True
                checks.append(check("UserNotActive", "not_evaluated", value, "Every due walk reached its last page, so the finder approves. A matching event had no EventTime, or a region's probe had no usable event (see this user's issue), so this skill does not recommend on it.", True))
                errors.extend(None for _ in failures)  # each failure's own issue covers this row
            elif value["reason"] == "no-activity-identified" and verified:
                checks.append(check("UserNotActive", "pass", value))
            elif value["reason"] == "no-activity-identified":
                checks.append(check("UserNotActive", "not_evaluated", value, "%s identity: the Username lookup is not shown to observe this user's sign-in events, so an empty result does not establish inactivity." % user["IdentityType"], True))
                needs.append({"stage": "analysis", "message": "No evidence establishes that CloudTrail Username lookups observe this %s user's QuickSight sign-ins for the whole %d-day window." % (user["IdentityType"], days),
                              "request": {"kind": "input", "name": "Evidence of this user's authentication flow and event-identity mapping, covering every relevant account and region for the whole window"}})
            else:
                halt = True
                checks.append(check("UserNotActive", "not_evaluated", value, "No region had a %s event in the window, so the walk was not due anywhere; this is a coverage gap, not inactivity." % source_of(user)
                                    if value["reason"] == "no-region-with-event-source" else "A due region's walk did not reach its last page.", False))
                for walk in failures:
                    (errors if walk.get("error") else needs).append(None)  # the walk's own issue covers this row
                if not failures:
                    needs.append({"message": "Activity coverage is incomplete: %s." % value["reason"], "request": {"kind": "aws_read", "name": "LookupEvents", "service": "cloudtrail", "region": region}})
        if not stop and halt:
            checks.append(check("HasReceiver", "not_evaluated", reason=HALTED))
        elif not stop and receiver:
            checks.append(check("HasReceiver", "pass", {"receiverArn": receiver["Arn"]}))
        elif not stop and any(summaries[a][2] for a in scanned):
            checks.append(check("HasReceiver", "not_evaluated", reason="Receiver unknown: the activity of the other users was not fully read."))
            unread = sorted((walk for a in scanned for walk in summaries[a][2]), key=lambda walk: not walk.get("error"))  # a failed read first: it decides error over needs_evidence
            more = " Other unread walks (%d) are named in the issues of their own users." % (len(unread) - 1) if len(unread) > 1 else ""
            (errors if unread[0].get("error") else needs).append(walk_issue(unread[0], ", so the receiver is unknown." + more))  # never recommended without a receiver
        elif not stop:
            checks.append(check("HasReceiver", "fail", {"resourceId": arn}, "No other user showed activity in the window, so there is nobody to receive this user's assets."))
            stop = True
        rows[arn] = {"user": user, "checks": checks, "stop": stop or halt, "halt": halt, "needs": needs, "errors": errors, "confidence": confidence}
    standing = [arn for arn in scope_ids if not rows[arn]["stop"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        reads = dict(zip(standing, pool.map(lambda arn: read_user_assets(evidence, "user-%d" % scope_ids.index(arn), by_arn[arn], account, region, profile), standing)))
    # 5. costs
    supplied = evidence.files.get("supplied-costs") if pricing_mode == "supplied" else None
    supplied_rows = {row["resourceId"]: row for row in (supplied or {}).get("resources", [])}
    billing = billing_failure = None
    if pricing_mode == "billing" and any(by_arn.get(arn, {}).get("Role") in ALLOWED_ROLES for arn in scope_ids):
        billing, billing_failure = collect_cost_explorer(evidence, account, as_of, request.get("billing_profile") or profile)
        assessment["details"]["billing_method"] = "cost_explorer"
        if billing_failure and billing_failure["kind"] != "missing_evidence":
            detail = (" %s: %s" % (billing_failure["error"]["code"], billing_failure["error"]["message"])) if billing_failure.get("error") else ""
            issues.append(dict({"kind": billing_failure["kind"], "stage": "collection" if billing_failure["kind"] == "read_error" else "costing", "resource_id": None,
                                "message": "Cost Explorer gave no rate: " + billing_failure["message"] + detail + " No public price replaces a billed cost; a separate public_prices run is one possible next request.",
                                "request": billing_failure.get("request") or {"kind": "aws_read", "name": "GetCostAndUsage", "service": "ce", "region": "us-east-1"}},
                               **({"evidence_refs": [billing_failure["ref"]]} if billing_failure.get("ref") else {})))
    calculations = {"users": {}, "receiver": {"rule": "The latest eventTime among the scanned users that have one; when two are equal, the user listed later.",
                                              "arn": receiver and receiver["Arn"], "eventTime": receiver and summaries[receiver["Arn"]][0]["eventTime"]}}
    if billing:
        calculations["cost_explorer"] = billing
    if billing and "coverage" in billing:
        listed_of = lambda kind: sum(1 for u in by_arn.values() if KIND_BY_ROLE.get(u["Role"]) == kind)
        qualifications += ["Billed costs are the account's effective rate per subscription kind from Cost Explorer (%s to %s, end exclusive): one subscription's billed net cost for each day of that window, summed, then annualized as the finder does (sum / 30 * 365). Not this user's own invoice line, not CUR equivalence; the user's own plan, start date, proration and credits are not reconstructed." % (billing["window"]["start"][:10], billing["window"]["end"][:10]),
                           "Cost Explorer: %d of %d days returned; %d marked Estimated. Per kind: %s. Usage types not per-user or unmapped: %s. A visible net cost does not prove complete payer-discount visibility." % (
                               billing["coverage"]["returned_days"], billing["coverage"]["requested_days"], billing["coverage"]["estimated_days"],
                               "; ".join("%s pooled %s, usage on %d of %d days, %d net fallbacks, %.6g implied subscriptions against %d listed" % (kind, ", ".join(k["usage_types"]), k["days_with_usage"], billing["coverage"]["requested_days"], k["net_fallback_periods"], k["impliedSubscriptions"] or 0, listed_of(kind))
                                         for kind, k in sorted(billing["kinds"].items())) or "none",
                               ", ".join("%s (%s)" % (row["usage_type"], row["unit"]) for row in billing["classification"] if not row["kind"]) or "none")]
        finder_window = tuple(day.isoformat() for day in billing_window(as_of.date()))
        if (billing["window"]["start"][:10], billing["window"]["end"][:10]) != finder_window:
            qualifications.append("This replay keeps the billing window its evidence recorded. The finder's window for this observation time is %s to %s, end exclusive, so the finder's amounts can differ." % finder_window)
    if pricing_mode == "billing":  # billing-cost-explorer.md: disclose the charge, or that no request was needed
        requests = sum(1 for saved in evidence.files.get("cost-explorer", []) if saved.get("service") == "ce")
        qualifications.append("AWS charges $0.01 per Cost Explorer request; evidence/cost-explorer.json holds %d, about %s." % (requests, money(requests / 100)) if requests
                              else "No Cost Explorer request was needed: no user in scope has an allowed role.")
    if pricing_mode == "supplied":
        qualifications.append("Current costs were supplied by the caller (%s) and used unchanged." % (supplied or {}).get("source", "no source stated"))
    shared_costs = dict({"basis": BASIS_BY_MODE.get(pricing_mode), "estimation_method": "configuration_projection", "currency": "USD"},
                        **({"source_period": billing["window"]} if billing else {})) if pricing_mode in BASIS_BY_MODE else None
    # 6. one row per user
    findings = []
    for arn in scope_ids:
        state = rows[arn]
        user, checks, needs, errors, stop = state["user"], state["checks"], state["needs"], state["errors"], state["stop"]
        reads_for = reads.get(arn) or {"tags": None, "assets": {}, "errors": {}, "missing": {}}
        tags, index = reads_for["tags"], scope_ids.index(arn)
        observations = [tag.get("Value") for tag in tags or [] if tag.get("Key") == OPT_OUT_TAG]  # before map normalization: any nonempty value excludes
        needs += [{"message": "The %s response for this user was not supplied." % operation, "request": failure} for operation, failure in reads_for["missing"].items()]
        errors += [{"message": "%s failed: %s: %s." % (operation, error["code"], error["message"]), "request": {"kind": "aws_read", "name": operation, "service": "quicksight", "region": region}, "evidence_refs": [ref]}
                   for operation, (error, ref) in reads_for["errors"].items()]
        asset_fault = not stop and any(operation != "ListTagsForResource" for operation in reads_for["errors"])
        kind, rate, annual_cost, cost_issue = KIND_BY_ROLE.get(user["Role"]) if user else None, None, None, None
        if user and pricing_mode == "supplied":  # a known cost is carried on every row, stopped or not; it is only missing on a row still being assessed
            annual_cost = supplied_rows.get(arn, {}).get("annualCost")
            cost_issue = None if annual_cost is not None or stop else {"message": "No supplied annual cost for this user.", "request": {"kind": "input", "name": "annualCost in USD for " + arn}}
        elif user and pricing_mode == "billing" and kind:
            rate = (billing or {}).get("kinds", {}).get(kind)
            annual_cost = rate["annualCostPerUser"] if rate else None
            if stop:
                pass
            elif rate is None and (billing_failure or {}).get("kind") == "missing_evidence":
                cost_issue = {"stage": "costing", "message": "Cost Explorer gave no rate for the %s kind: %s" % (kind, billing_failure["message"]), "request": billing_failure["request"]}
            elif rate is None and billing and not billing_failure:
                cost_issue = {"stage": "costing", "message": "Cost Explorer returned no usage type mapped to the %s kind, or zero user-months for it." % kind, "request": COST_INPUT}
            elif rate is None:
                errors.append(None)  # the run-level Cost Explorer issue covers this row
        needs += [cost_issue] if cost_issue else []
        savings = annual_cost  # the finder's model: annualSavings = resource.annualCost
        if stop:
            checks += [check(name, "not_evaluated", reason=HALTED if state["halt"] else NOT_RUN) for name in CHECK_NAMES[len(checks):-1]]
            checks.append(check("DontFixItOptOut", "not_evaluated", reason="Not needed: an earlier check could not be evaluated." if state["halt"] else "Not needed after the earlier rejection."))
        else:
            if asset_fault or pricing_mode == "technical_only" or savings is None:
                reason = ("Not run: an asset search failed, which the finder treats as a per-item exception." if asset_fault else "No cost was requested (technical_only)." if pricing_mode == "technical_only"
                          else "The annual cost is not established, so the savings guard could not run.")
                checks += [check(name, "not_evaluated", reason=reason) for name in ("MinimalSavings", "MaximumSavings")]
            else:
                minimal = configuration["minimalSavings"]
                checks.append(check("MinimalSavings", "pass" if savings > minimal else "fail", {"annualSavings": savings, "annualCost": annual_cost, "minimalSavings": minimal},
                                    None if savings > minimal else "%s is not greater than the minimum %s." % (js_number(savings), js_number(minimal))))
                checks.append(check("MaximumSavings", "pass" if savings <= annual_cost else "fail", {"annualSavings": savings, "annualCost": annual_cost}, None if savings <= annual_cost else "Annual savings exceed the annual cost."))
            excluded = next((value for value in observations if isinstance(value, str) and value != ""), None)
            if asset_fault:
                checks.append(check("DontFixItOptOut", "not_evaluated", reason=reason))
            elif tags is None:
                checks.append(check("DontFixItOptOut", "not_evaluated", reason="The tag read failed or is missing, so the opt-out is unknown."))
            elif excluded is not None:
                checks.append(check("DontFixItOptOut", "fail", {"key": OPT_OUT_TAG, "observed": excluded}, "The tag value %s is a nonempty string, so the user is opted out. It is not trimmed or read as boolean false." % json.dumps(excluded)))
            elif not all(isinstance(value, str) for value in observations):  # validation.md: an invalid type leaves protection unknown
                checks.append(check("DontFixItOptOut", "not_evaluated", reason="The %s tag value is not a string, so the opt-out is unknown." % OPT_OUT_TAG))
                needs.append({"message": "The %s tag was returned with a value that is not a string, so protection is unknown." % OPT_OUT_TAG,
                              "request": {"kind": "aws_read", "name": "ListTagsForResource", "service": "quicksight", "region": region, "parameters": {"ResourceArn": arn}}})
            else:
                checks.append(check("DontFixItOptOut", "pass", {"key": OPT_OUT_TAG, "observed": "" if observations else None}))
        failed_names = {row["name"] for row in checks if row["status"] == "fail"}
        decision = "not_recommended" if failed_names else "error" if errors else "needs_evidence" if needs else "recommended"
        finding = {"id": arn, "resource": {"id": arn, "type": RESOURCE_TYPE, "region": arn.split(":")[3]}, "decision": decision, "checks": checks}
        technical = failed_names <= {"DontFixItOptOut", "MinimalSavings", "MaximumSavings"} and not stop
        if decision == "not_recommended" and technical:
            finding["technical_eligibility"] = "eligible"
        elif decision in ("needs_evidence", "error"):
            finding["technical_eligibility"] = "eligible" if technical and all(row["status"] == "pass" for row in checks[1:6]) and checks[-1]["status"] != "not_evaluated" and not asset_fault else "unknown"
        parameters = dict({"fromPrincipalId": user["PrincipalId"], "toUserArn": receiver["Arn"]}, **reads_for["assets"]) if user and receiver and len(reads_for["assets"]) == 4 else None
        if decision == "recommended":
            finding["recommendation"] = {"action": "grant_owner_permissions_and_delete_user", "parameters": parameters}
        if decision == "recommended" and savings is not None:
            finding["costs"] = dict({"status": "calculated"}, **shared_costs, current_annual=annual_cost, target_annual=annual_cost - savings, annual_savings=savings)
            if rate:
                finding["costs"]["qualifications"] = ["Account-level effective rate for the %s subscription kind from Cost Explorer over the source period (%s USD per user-month; one subscription's cost over the window, annualized as the finder does: sum / 30 * 365). It is not this user's own invoice line." % (kind, js_number(rate["effectiveMonthlyRate"]))]
        else:
            finding["costs"] = {"status": "not_calculated", "basis": shared_costs["basis"], "currency": "USD", "current_annual": annual_cost} if annual_cost is not None else {"status": "not_calculated"}
        refs, details = [positions[arn]] if user else [], {}
        if user and not stop and summaries.get(arn):
            refs += [walks[name][arn]["refs"][0] for name in summaries[arn][1] if walks[name].get(arn, {}).get("refs")]
        if arn in reads and "user-%d" % index in evidence.files:
            refs.append("evidence/user-%d.json" % index)
        if user:
            details.update(userName=user.get("UserName"), role=user["Role"], identityType=user["IdentityType"])
            calculations["users"][arn] = {"annualCost": annual_cost, "annualSavings": {"expression": "resource.annualCost", "result": savings}, "subscription_kind": kind,
                                          "projectedAnnualCost": {"expression": "resource.annualCost - annualSavings", "result": annual_cost - savings if annual_cost is not None else None},
                                          "activity": summaries.get(arn, ({},))[0]}
            refs.append("evidence/calculations.json#/users/" + arn.replace("~", "~0").replace("/", "~1"))
        if state["confidence"]:
            details["activity_evidence_confidence"] = state["confidence"]
        if receiver and user and len(checks) > 5 and checks[5]["status"] == "pass":
            details["receiver"] = {"arn": receiver["Arn"], "userName": receiver.get("UserName"), "eventTime": summaries[receiver["Arn"]][0]["eventTime"]}
        if tags is not None:
            details["resourceTags"] = {tag["Key"]: tag["Value"] for tag in tags if tag.get("Key") is not None and tag.get("Value") is not None}
            if observations:
                details["opt_out"] = [{"source": "evidence/user-%d.json#/0" % index, "value": value, "observed_at": evidence.files["user-%d" % index][0]["observed_at"]} for value in observations]
        if rate:
            details.update(subscription_kind=kind, effective_monthly_rate=rate["effectiveMonthlyRate"])
        if decision != "recommended" and annual_cost is not None:
            details.update(annualSavings=savings, projectedAnnualCost=annual_cost - savings)
        if decision != "recommended" and parameters:
            details["candidate_parameters"] = parameters
        finding["evidence_refs"], finding["details"] = refs, details
        issues += [dict({"kind": "read_error" if issue in errors else "missing_evidence", "stage": "collection", "resource_id": arn}, **issue) for issue in needs + errors if issue]
        findings.append(finding)
    evidence.files["calculations"] = calculations
    recommended = [f for f in findings if f["decision"] == "recommended"]
    unassessed = [f["id"] for f in findings if f["decision"] in ("needs_evidence", "error")]
    totals = {"decision_counts": {d: sum(f["decision"] == d for f in findings) for d in ("recommended", "not_recommended", "needs_evidence", "error")}, "annual_savings": None}
    if recommended and shared_costs:
        totals.update({key: shared_costs[key] for key in ("currency", "basis", "estimation_method")})
        totals.update({field: sum(f["costs"][field] for f in recommended) for field in ("current_annual", "target_annual", "annual_savings")})
    elif recommended:
        totals["reason_not_totaled"] = "No cost was requested (technical_only); the recommended rows carry no amounts."
    assessment.update(findings=findings, totals=totals, coverage={"inventory_complete": True, "discovered_count": len(scope_ids), "assessed_count": len(findings) - len(unassessed), "unassessed_ids": unassessed})
    assessment["run"]["status"] = "complete" if not unassessed else "partial"
    qualifications += ["A completed ListUsers read of the default namespace returned no users."] if not scope_ids else []
    qualifications += [
        "Only the default namespace was assessed. No activity identified under the finder's predicates is not proof that deletion is safe.",
        "Framework history was not supplied, so RecommendationFinalized was not evaluated. This is a standalone total, not a native-complete one.",
        "The finder counts any CloudTrail event under the user's name as activity, including API reads made by the identity that runs this assessment or an earlier test; such a user is not idle under this rule, and can be the receiver.",
        "The proposal for a recommended user is to grant the receiver owner-level permissions on the listed analyses, dashboards, data sets and data sources, then delete the user by principal ID. It is not a complete asset transfer; owner intent, receiver suitability and retention needs were not established.",
        "Savings are a conditional gross subscription saving. Verified marginal account savings are unknown: commitments, proration, consolidated licensing and remaining charges were not resolved. One-time costs and first-year savings are unknown."
        if pricing_mode != "technical_only" else "No cost was requested: the rows carry no amounts and the total is not established."]
    assessment["verification"]["summary"] = ("Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. "
                                             "The activity summaries, receiver choice and cost expressions are recorded in evidence/calculations.json. AWS facts were not re-read.")
    return finish(out_dir, evidence, assessment)

if __name__ == "__main__":
    sys.exit(main())
