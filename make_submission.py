from __future__ import annotations

import csv
import importlib
import os
from pathlib import Path
from typing import Any, List


def _load_agent():
    candidates = ["agent"]
    for name in candidates:
        try:
            module = importlib.import_module(name)
            if hasattr(module, "Agent"):
                return module.Agent()
        except Exception:
            continue
    raise ImportError("agent.py with Agent class was not found")


def _build_env():
    candidates = ["local_eval", "eval", "runner", "main"]
    for name in candidates:
        try:
            module = importlib.import_module(name)
            for attr_name in ("make_env", "build_env", "create_env"):
                if hasattr(module, attr_name):
                    factory = getattr(module, attr_name)
                    return factory()
            for attr_name in ("env", "ENV"):
                if hasattr(module, attr_name):
                    return getattr(module, attr_name)
            if hasattr(module, "Env"):
                return module.Env()
        except Exception:
            continue
    root = Path(".")
    matches = list(root.rglob("customer_profile.csv"))
    if matches:
        try:
            import pandas as pd
            profile = pd.read_csv(matches[0])
            class FallbackEnv:
                customer_profile = profile
                tariffs = list(sorted(set(profile["current_tariff"].dropna().astype(str).tolist())))
                channels = ["push", "sms", "digital_ads", "call"]
                remaining_budget = 100000
                remaining_contacts = 15000
                pilots_left = 20
                pilot_history = []
            return FallbackEnv()
        except Exception:
            pass
    class EmptyEnv:
        customer_profile = None
        tariffs = ["tariff_1", "tariff_10", "tariff_21"]
        channels = ["push", "sms", "digital_ads", "call"]
        remaining_budget = 100000
        remaining_contacts = 15000
        pilots_left = 20
        pilot_history = []
        def run_pilot(self, **kwargs):
            return {"effect": 0.0, "n_customers": kwargs.get("n_customers", 0)}
    return EmptyEnv()


def _normalize_campaign(campaign: Any) -> dict:
    if not isinstance(campaign, dict):
        return {}
    row = {
        "campaign_name": campaign.get("campaign_name", "Campaign"),
        "filter_arpu_segment": campaign.get("filter_arpu_segment"),
        "filter_data_segment": campaign.get("filter_data_segment"),
        "filter_call_segment": campaign.get("filter_call_segment"),
        "filter_current_tariff": campaign.get("filter_current_tariff"),
        "target_tariff": campaign.get("target_tariff"),
        "channel": campaign.get("channel"),
    }
    return row


def main() -> None:
    agent = _load_agent()
    env = _build_env()
    campaigns = agent.act(env) if hasattr(agent, "act") else []
    normalized = [_normalize_campaign(c) for c in campaigns[:10]]
    fieldnames = [
        "campaign_name",
        "filter_arpu_segment",
        "filter_data_segment",
        "filter_call_segment",
        "filter_current_tariff",
        "target_tariff",
        "channel",
    ]
    out_path = Path("submission.csv")
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in normalized:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    print(f"Generated {len(normalized)} campaigns into {out_path.resolve()}")
    evaluator = getattr(importlib.import_module("local_eval"), "evaluate_campaigns", None)
    if evaluator is not None and env.__class__.__module__ == "local_eval":
        summary = evaluator(normalized, env)
        print(
            "Local evaluation: "
            f"score={summary['net_score']:.2f}, "
            f"contacts={summary['contacts']}, "
            f"budget={summary['budget']:.2f}, "
            f"within_limits={summary['within_limits']}, "
            f"invalid={summary['invalid_campaigns']}"
        )


if __name__ == "__main__":
    main()
