"""GP2 to GP3 finder: settings and name."""
from cloudfix.common.settings import Setting

NAME = "gp2-to-gp3"

SETTINGS: tuple[Setting, ...] = (
    Setting("account", "Which AWS account id should be assessed?", "account_id"),
    Setting("region", "Which AWS region should be assessed?", "region", required=True),
    Setting("profile", "Which AWS profile should be used?", "string"),
    Setting("volume_ids", "Which volume ids should be assessed? Leave empty for all gp2 volumes.",
            "string_list", default=[]),
    Setting("cost_basis", "Should savings use public prices or your supplied costs?", "choice",
            default="public", choices=("public", "supplied")),
    Setting("costs_file", "Which file holds your supplied volume costs?", "path"),
    Setting("volume_age_threshold_days", "How many days old must a volume be?", "integer",
            default=7, minimum=0),
    Setting("minimal_savings_usd", "What is the minimum annual saving per volume in USD?", "number",
            default=0),
)
