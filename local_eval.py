from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List

from db import TARIFFS as ALL_TARIFFS, database_summary, load_profile


TARIFFS = list(ALL_TARIFFS)
CHANNELS = ["push", "sms", "digital_ads", "call"]
CHANNEL_COST = {"push": 0.0, "sms": 4.0, "digital_ads": 22.0, "call": 160.0}


def _build_profile() -> List[Dict[str, Any]]:
    return load_profile()


class LocalEnv:
    """Small deterministic environment for local smoke testing.

    The official challenge environment can replace this module without changing
    Agent.act or the submission format.
    """

    def __init__(self, customer_profile: List[Dict[str, Any]] | None = None) -> None:
        self.customer_profile = customer_profile or _build_profile()
        self.tariffs = list(TARIFFS)
        self.channels = list(CHANNELS)
        self.remaining_budget = 100_000.0
        self.remaining_contacts = 15_000
        self.pilots_left = 20
        self.pilot_history: List[Dict[str, Any]] = []

    def run_pilot(self, **kwargs: Any) -> Dict[str, Any]:
        if self.pilots_left <= 0:
            raise RuntimeError("No pilot budget remaining")
        self.pilots_left -= 1
        target = str(kwargs.get("target_tariff", ""))
        channel = str(kwargs.get("channel", "sms")).lower()
        tariff_number = _tariff_number(target)
        effect = 0.04 + min(0.18, tariff_number * 0.012)
        if channel == "push":
            effect += 0.015
        elif channel == "call":
            effect += 0.025
        result = {
            "target_tariff": target,
            "channel": channel,
            "filter_arpu_segment": kwargs.get("filter_arpu_segment"),
            "filter_data_segment": kwargs.get("filter_data_segment"),
            "filter_current_tariff": kwargs.get("filter_current_tariff"),
            "filter_call_segment": kwargs.get("filter_call_segment"),
            "effect": effect,
            "n_customers": int(kwargs.get("n_customers", 50)),
        }
        self.pilot_history.append(result)
        return result


def _tariff_number(value: str) -> int:
    try:
        return int(str(value).rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        return 0


def make_env() -> LocalEnv:
    return LocalEnv()


def dataset_summary() -> Dict[str, int]:
    return database_summary()


def evaluate_campaigns(campaigns: Iterable[Dict[str, Any]], env: LocalEnv) -> Dict[str, Any]:
    """Run basic local validation and return a transparent score summary."""
    rows = env.customer_profile
    total_contacts = 0
    total_cost = 0.0
    total_gain = 0.0
    invalid = []
    for campaign in list(campaigns)[:10]:
        channel = str(campaign.get("channel", "")).lower()
        target = str(campaign.get("target_tariff", ""))
        if channel not in CHANNELS or target not in TARIFFS:
            invalid.append(campaign)
            continue
        matched = _matching_rows(rows, campaign)
        contacts = len(matched)
        cost = contacts * CHANNEL_COST[channel]
        gain = contacts * max(0, _tariff_number(target) - 2) * 12.0
        total_contacts += contacts
        total_cost += cost
        total_gain += gain
    return {
        "campaigns": min(10, len(list(campaigns))),
        "contacts": total_contacts,
        "budget": total_cost,
        "gross_gain": total_gain,
        "net_score": total_gain - total_cost,
        "within_limits": total_contacts <= env.remaining_contacts and total_cost <= env.remaining_budget,
        "invalid_campaigns": len(invalid),
        "channel_mix": {
            channel: sum(1 for campaign in campaigns if str(campaign.get("channel", "")).lower() == channel)
            for channel in CHANNELS
        },
    }


def _matching_rows(rows: List[Dict[str, Any]], campaign: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        matches = True
        for field in ("arpu_segment", "data_segment", "current_tariff"):
            filter_value = campaign.get(f"filter_{field}")
            if filter_value and str(row.get(field)).lower() != str(filter_value).lower():
                matches = False
                break
        call_filter = campaign.get("filter_call_segment")
        if matches and call_filter and str(row.get("call_segment", "")).lower() != str(call_filter).lower():
            matches = False
        if matches:
            result.append(row)
    return result


def write_profile_csv(path: Path) -> None:
    rows = _build_profile()
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
