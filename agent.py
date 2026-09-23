from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


DEFAULT_CHANNEL_COST = {
    "push": 0.0,
    "sms": 4.0,
    "digital_ads": 22.0,
    "call": 160.0,
}
DEFAULT_CHANNEL_EFFECT = {
    "push": 0.50,
    "sms": 0.65,
    "digital_ads": 0.85,
    "call": 1.20,
}


class Agent:
    """Heuristic tariff-campaign agent with pilot-based exploration.

    The implementation is intentionally data-driven: it estimates tariff uplift from
    available customer data, chooses promising target tariffs and segments, spends a
    small pilot budget to calibrate the estimate, and then emits a final list of up to
    ten campaigns. The strategy is robust even when the environment is partially
    missing or the pilot history is noisy.
    """

    def act(self, env: Any) -> List[Dict[str, Any]]:
        try:
            profile = self._as_dataframe(getattr(env, "customer_profile", None))
            rows = self._profile_rows(profile)
            if profile is None or (hasattr(profile, "empty") and profile.empty) or (not rows and profile is not None):
                return self._fallback_campaigns(env)

            profile = self._normalize_columns(profile)
            self._active_rows = rows = self._profile_rows(profile)
            self._profile_index = self._build_profile_index(rows)
            tariffs = self._extract_tariffs(env, profile)
            channels = self._extract_channels(env)
            campaign_candidates = self._build_campaign_candidates(profile, tariffs, channels)

            pilot_history = self._extract_pilot_history(getattr(env, "pilot_history", []) or [])
            usable_pilot_budget = self._pilot_budget_left(env)
            if usable_pilot_budget > 0 and hasattr(env, "run_pilot"):
                pilot_plan = self._design_pilot_plan(campaign_candidates, profile, usable_pilot_budget)
                for pilot in pilot_plan:
                    try:
                        outcome = env.run_pilot(**pilot)
                        if outcome is not None:
                            pilot_history.append(self._normalize_pilot_outcome(outcome, pilot))
                    except Exception:
                        continue

            scored = self._score_campaigns(campaign_candidates, profile, pilot_history)
            ranked = sorted(scored, key=lambda item: item["score"], reverse=True)
            selected: List[Dict[str, Any]] = []
            seen_signatures = set()
            budget_limit = self._safe_float(getattr(env, "remaining_budget", None), 100000.0)
            contacts_limit = self._safe_float(getattr(env, "remaining_contacts", None), 15000.0)
            used_budget = 0.0
            used_contacts = 0.0
            for item in ranked:
                campaign = item["campaign"]
                key = (
                    campaign.get("filter_arpu_segment"),
                    campaign.get("filter_data_segment"),
                    campaign.get("filter_current_tariff"),
                    campaign.get("filter_call_segment"),
                    campaign.get("target_tariff"),
                    campaign.get("channel"),
                )
                if key in seen_signatures:
                    continue
                estimate = self._campaign_resource_estimate(profile, campaign)
                if budget_limit > 0 and used_budget + estimate["budget_cost"] > budget_limit:
                    continue
                if contacts_limit > 0 and used_contacts + estimate["contacts"] > contacts_limit:
                    continue
                selected.append(campaign)
                seen_signatures.add(key)
                used_budget += estimate["budget_cost"]
                used_contacts += estimate["contacts"]
                if len(selected) >= 10:
                    break

            if not selected:
                return self._fallback_campaigns(env)
            return selected[:10]
        except Exception:
            return self._fallback_campaigns(env)

    def _fallback_campaigns(self, env: Any) -> List[Dict[str, Any]]:
        tariffs = self._extract_tariffs(env, self._as_dataframe(getattr(env, "customer_profile", None)))
        channels = self._extract_channels(env)
        if not tariffs:
            tariffs = ["tariff_1", "tariff_10", "tariff_21"]
        if not channels:
            channels = ["sms", "push", "digital_ads"]
        campaigns = []
        for idx, target in enumerate(tariffs[:3]):
            channel = channels[idx % len(channels)]
            campaigns.append(
                {
                    "campaign_name": f"Fallback_{idx + 1}",
                    "filter_arpu_segment": ["LOW", "MID", "HIGH"][idx % 3],
                    "target_tariff": target,
                    "channel": channel,
                }
            )
        return campaigns[:10]

    def _as_dataframe(self, obj: Any):
        if obj is None:
            return None
        if pd is not None and hasattr(obj, "copy"):
            try:
                return obj.copy()
            except Exception:
                pass
        if pd is not None and hasattr(obj, "columns"):
            return obj
        if isinstance(obj, (list, tuple, set, dict)):
            return obj
        return obj

    def _normalize_columns(self, df):
        if df is None:
            return df
        if isinstance(df, (list, tuple, set)):
            rows = []
            for row in df:
                if isinstance(row, dict):
                    normalized_row = {}
                    for key, value in row.items():
                        normalized_row[str(key).strip().lower()] = value
                    rows.append(normalized_row)
            return rows
        if isinstance(df, dict):
            if all(isinstance(v, (list, tuple, set)) for v in df.values()):
                normalized = {}
                for key, value in df.items():
                    normalized[str(key).strip().lower()] = list(value)
                return normalized
            return {str(key).strip().lower(): value for key, value in df.items()}
        if pd is not None and hasattr(df, "columns"):
            cols = {str(c): str(c).strip() for c in list(df.columns)}
            normalized = df.rename(columns=lambda c: str(c).strip().lower())
            return normalized
        return df

    def _extract_tariffs(self, env: Any, profile: Optional[Any]) -> List[str]:
        tariffs = getattr(env, "tariffs", None)
        if isinstance(tariffs, dict):
            keys = list(tariffs.keys())
            if keys:
                return [str(t) for t in keys]
        if isinstance(tariffs, (list, tuple, set)):
            return [str(t) for t in tariffs]
        rows = self._profile_rows(profile)
        if rows:
            values = self._column_values(rows, "current_tariff")
            if values:
                return values
        if profile is not None and hasattr(profile, "columns"):
            if "current_tariff" in profile.columns:
                values = [str(v) for v in profile["current_tariff"].dropna().unique().tolist()]
                if values:
                    return values
        return [f"tariff_{i}" for i in range(1, 22)]

    def _extract_channels(self, env: Any) -> List[str]:
        channels = getattr(env, "channels", None)
        if isinstance(channels, dict):
            return [str(ch) for ch in channels.keys()]
        if isinstance(channels, (list, tuple, set)):
            return [str(ch) for ch in channels]
        return ["push", "sms", "digital_ads", "call"]

    def _build_campaign_candidates(self, profile: Any, tariffs: Sequence[str], channels: Sequence[str]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        current_values = self._column_values(profile, "current_tariff")
        if not current_values:
            return candidates

        arpu_levels = self._segment_values(profile, "arpu_segment")
        data_levels = self._segment_values(profile, "data_segment")
        call_levels = self._segment_values(profile, "call_segment")

        sorted_tariffs = self._sort_tariffs(tariffs)
        target_pool = self._choose_target_tariffs(sorted_tariffs)

        for arpu_segment in arpu_levels:
            for data_segment in data_levels[:3]:
                for call_segment in call_levels[:3] or [None]:
                    for current_tariff in current_values[: min(3, len(current_values))]:
                        for target_tariff in target_pool[:4]:
                            if target_tariff == current_tariff:
                                continue
                            for channel in channels[:4]:
                                campaign = {
                                    "campaign_name": f"{arpu_segment}_{data_segment}_{call_segment or 'ALL'}_{current_tariff}_to_{target_tariff}_{channel}",
                                    "filter_arpu_segment": arpu_segment,
                                    "filter_data_segment": data_segment,
                                    "filter_call_segment": call_segment,
                                    "filter_current_tariff": current_tariff,
                                    "target_tariff": target_tariff,
                                    "channel": channel,
                                }
                                candidates.append(campaign)
        return candidates

    def _profile_rows(self, profile: Any) -> List[Dict[str, Any]]:
        if profile is None:
            return []
        if isinstance(profile, list):
            return [dict(row) for row in profile if isinstance(row, dict)]
        if isinstance(profile, dict):
            if not profile:
                return []
            if all(isinstance(v, (list, tuple, set)) for v in profile.values()):
                keys = list(profile.keys())
                count = max(len(v) for v in profile.values()) if keys else 0
                return [
                    {
                        key: profile[key][idx] if idx < len(profile[key]) else None
                        for key in keys
                    }
                    for idx in range(count)
                ]
            return [dict(profile)]
        if pd is not None and hasattr(profile, "to_dict"):
            try:
                return [dict(row) for row in profile.to_dict(orient="records")]
            except Exception:
                pass
        return []

    def _build_profile_index(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[Tuple[str, ...], Tuple[float, int]]]:
        indexes: Dict[str, Dict[Tuple[str, ...], List[float]]] = {"value": {}, "arpu": {}}
        for row in rows:
            arpu = str(row.get("arpu_segment", "")).upper()
            data = str(row.get("data_segment", "")).upper()
            call = str(row.get("call_segment", "")).upper()
            current = str(row.get("current_tariff", "")).lower()
            try:
                value = float(row.get("predicted_arpu"))
            except (TypeError, ValueError):
                continue
            indexes["value"].setdefault((arpu, data, call, current), []).append(value)
            indexes["arpu"].setdefault((arpu, call, current), []).append(value)
        return {
            name: {key: (sum(values), len(values)) for key, values in buckets.items()}
            for name, buckets in indexes.items()
        }

    def _column_values(self, profile: Any, column_name: str) -> List[str]:
        rows = getattr(self, "_active_rows", None) or self._profile_rows(profile)
        if not rows:
            return []
        values = []
        for row in rows:
            value = row.get(column_name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                values.append(text)
        # Deduplicate while preserving order.
        unique = []
        for value in values:
            if value not in unique:
                unique.append(value)
        return unique

    def _segment_values(self, profile: Any, column_name: str) -> List[str]:
        values = self._column_values(profile, column_name)
        if not values:
            return []
        order = ["LOW", "MID", "HIGH", "NON_USER", "LITE", "HEAVY", "MEDIUM"]
        ordered = []
        for token in order:
            if token in values and token not in ordered:
                ordered.append(token)
        for v in values:
            if v not in ordered:
                ordered.append(v)
        return ordered

    def _sort_tariffs(self, tariffs: Sequence[str]) -> List[str]:
        def rank(value: str):
            try:
                return int(str(value).split("_")[-1])
            except Exception:
                return 10**9
        return sorted(set(tariffs), key=rank)

    def _choose_target_tariffs(self, sorted_tariffs: Sequence[str]) -> List[str]:
        if len(sorted_tariffs) <= 1:
            return list(sorted_tariffs)
        # Prefer one-step and medium-high upgrades; keep a few realistic target options.
        chosen = []
        for idx in range(min(len(sorted_tariffs), 8)):
            candidate = sorted_tariffs[min(len(sorted_tariffs) - 1, max(0, idx + 2))]
            if candidate not in chosen:
                chosen.append(candidate)
        if not chosen:
            return list(sorted_tariffs)
        return chosen

    def _extract_pilot_history(self, pilot_history: Any) -> List[Dict[str, Any]]:
        if pilot_history is None:
            return []
        if isinstance(pilot_history, list):
            out = []
            for entry in pilot_history:
                if isinstance(entry, dict):
                    out.append(entry)
            return out
        return []

    def _pilot_budget_left(self, env: Any) -> int:
        pilots_left = getattr(env, "pilots_left", None)
        if pilots_left is None:
            return 5
        try:
            return max(0, int(pilots_left))
        except Exception:
            return 5

    def _design_pilot_plan(self, campaign_candidates: Sequence[Dict[str, Any]], profile: Any, pilots_left: int) -> List[Dict[str, Any]]:
        if not campaign_candidates:
            return []
        best = []
        seen = set()
        for item in campaign_candidates:
            signature = (
                item.get("filter_arpu_segment"),
                item.get("filter_current_tariff"),
                item.get("target_tariff"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            best.append(item)
            if len(best) >= min(4, max(1, pilots_left)):
                break
        plan = []
        for item in best:
            pilot = {
                "target_tariff": item.get("target_tariff"),
                "channel": item.get("channel"),
                "n_customers": 50,
            }
            if item.get("filter_arpu_segment"):
                pilot["filter_arpu_segment"] = item["filter_arpu_segment"]
            if item.get("filter_data_segment"):
                pilot["filter_data_segment"] = item["filter_data_segment"]
            if item.get("filter_current_tariff"):
                pilot["filter_current_tariff"] = item["filter_current_tariff"]
            if item.get("filter_call_segment"):
                pilot["filter_call_segment"] = item["filter_call_segment"]
            pilot["n_customers"] = 200 if len(plan) == 0 else 100 if len(plan) < 3 else 50
            plan.append(pilot)
        return plan

    def _normalize_pilot_outcome(self, outcome: Any, pilot: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(outcome, dict):
            normalized = dict(outcome)
        else:
            normalized = {
                "target_tariff": pilot.get("target_tariff"),
                "channel": pilot.get("channel"),
                "effect": float(outcome) if isinstance(outcome, (int, float)) else 0.0,
            }
        normalized.setdefault("target_tariff", pilot.get("target_tariff"))
        normalized.setdefault("channel", pilot.get("channel"))
        normalized.setdefault("n_customers", pilot.get("n_customers", 50))
        normalized.setdefault("score", self._safe_float(normalized.get("effect"), 0.0))
        return normalized

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _score_campaigns(self, campaign_candidates: Sequence[Dict[str, Any]], profile: Any, pilot_history: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored: List[Dict[str, Any]] = []
        for campaign in campaign_candidates:
            estimate = self._estimate_effect(campaign, profile, pilot_history)
            channel_cost = self._channel_cost(campaign.get("channel"))
            base_value = self._base_customer_value(profile, campaign)
            channel_effect = DEFAULT_CHANNEL_EFFECT.get(
                str(campaign.get("channel", "sms")).lower(), 0.65
            )
            expected_profit = (
                estimate * max(base_value, 1.0) * channel_effect
                - channel_cost * 0.5
            )
            scored.append({"campaign": campaign, "score": expected_profit})
        return scored

    def _estimate_effect(self, campaign: Dict[str, Any], profile: Any, pilot_history: Sequence[Dict[str, Any]]) -> float:
        # Pilot history is the strongest signal if present. It should dominate the prior.
        target = campaign.get("target_tariff")
        channel = campaign.get("channel")
        arpu_filter = campaign.get("filter_arpu_segment")
        current_filter = campaign.get("filter_current_tariff")
        if pilot_history:
            matches = []
            for row in pilot_history:
                if row.get("target_tariff") and row.get("target_tariff") != target:
                    continue
                if channel and row.get("channel") and str(row.get("channel")).lower() != str(channel).lower():
                    continue
                if arpu_filter and row.get("filter_arpu_segment") and str(row.get("filter_arpu_segment")) != str(arpu_filter):
                    continue
                if current_filter and row.get("filter_current_tariff") and str(row.get("filter_current_tariff")) != str(current_filter):
                    continue
                call_filter = campaign.get("filter_call_segment")
                if call_filter and row.get("filter_call_segment") and str(row.get("filter_call_segment")) != str(call_filter):
                    continue
                matches.append(row)
            if matches:
                values = []
                sample_sizes = []
                for row in matches:
                    value = self._safe_float(row.get("effect"), self._safe_float(row.get("score"), 0.0))
                    sample_size = max(1.0, self._safe_float(row.get("n_customers"), 30.0))
                    sample_sizes.append(sample_size)
                    values.append(value)
                pilot_value = sum(values) / max(1, len(values))
                reliability = min(0.85, sum(sample_sizes) / (sum(sample_sizes) + 180.0))
                return reliability * pilot_value + (1.0 - reliability) * self._prior_effect(campaign, profile)

        # Heuristic estimate using customer profile and tariff ordering.
        index = getattr(self, "_profile_index", {})
        if index:
            key = (
                str(arpu_filter or "").upper(),
                str(campaign.get("filter_call_segment") or "").upper(),
                str(current_filter or "").lower(),
            )
            aggregate = index.get("arpu", {}).get(key)
            if aggregate:
                price_signal = aggregate[0] / aggregate[1] / 1000.0
                if arpu_filter and str(arpu_filter).upper() == "HIGH":
                    return max(0.14, 0.12 + price_signal * 0.08)
                if arpu_filter and str(arpu_filter).upper() == "MID":
                    return max(0.08, 0.09 + price_signal * 0.05)
                return max(0.04, 0.05 + price_signal * 0.03)

        rows = getattr(self, "_active_rows", None) or self._profile_rows(profile)
        price_signal = 0.0
        if rows:
            subset = rows
            if arpu_filter:
                subset = [
                    row for row in subset
                    if str(row.get("arpu_segment", "")).upper() == str(arpu_filter).upper()
                ]
            if current_filter:
                subset = [
                    row for row in subset
                    if str(row.get("current_tariff", "")).lower() == str(current_filter).lower()
                ]
            call_filter = campaign.get("filter_call_segment")
            if call_filter:
                subset = [
                    row for row in subset
                    if str(row.get("call_segment", "")).upper() == str(call_filter).upper()
                ]
            if subset:
                arpus = []
                for row in subset:
                    value = row.get("predicted_arpu")
                    try:
                        arpus.append(float(value))
                    except Exception:
                        continue
                if arpus:
                    price_signal = sum(arpu for arpu in arpus) / len(arpus) / 1000.0

        if arpu_filter and str(arpu_filter).upper() == "HIGH":
            return max(0.14, 0.12 + price_signal * 0.08)
        if arpu_filter and str(arpu_filter).upper() == "MID":
            return max(0.08, 0.09 + price_signal * 0.05)
        return max(0.04, 0.05 + price_signal * 0.03)

    def _prior_effect(self, campaign: Dict[str, Any], profile: Any) -> float:
        segment = str(campaign.get("filter_arpu_segment", "")).upper()
        if segment == "HIGH":
            return 0.14
        if segment == "MID":
            return 0.09
        return 0.05

    def _base_customer_value(self, profile: Any, campaign: Dict[str, Any]) -> float:
        index = getattr(self, "_profile_index", {})
        if index:
            key = (
                str(campaign.get("filter_arpu_segment") or "").upper(),
                str(campaign.get("filter_data_segment") or "").upper(),
                str(campaign.get("filter_call_segment") or "").upper(),
                str(campaign.get("filter_current_tariff") or "").lower(),
            )
            aggregate = index.get("value", {}).get(key)
            if aggregate:
                return aggregate[0] / aggregate[1]
        rows = getattr(self, "_active_rows", None) or self._profile_rows(profile)
        if not rows:
            return 1.0
        subset = rows
        if campaign.get("filter_arpu_segment"):
            subset = [
                row for row in subset
                if str(row.get("arpu_segment", "")).upper() == str(campaign["filter_arpu_segment"]).upper()
            ]
        if campaign.get("filter_data_segment"):
            subset = [
                row for row in subset
                if str(row.get("data_segment", "")).upper() == str(campaign["filter_data_segment"]).upper()
            ]
        if campaign.get("filter_current_tariff"):
            subset = [
                row for row in subset
                if str(row.get("current_tariff", "")).lower() == str(campaign["filter_current_tariff"]).lower()
            ]
        if campaign.get("filter_call_segment"):
            subset = [
                row for row in subset
                if str(row.get("call_segment", "")).upper() == str(campaign["filter_call_segment"]).upper()
            ]
        if not subset:
            return 1.0
        values = []
        for row in subset:
            try:
                values.append(float(row.get("predicted_arpu")))
            except Exception:
                continue
        if not values:
            return 1.0
        return float(sum(values) / len(values))

    def _campaign_resource_estimate(self, profile: Any, campaign: Dict[str, Any]) -> Dict[str, float]:
        rows = getattr(self, "_active_rows", None) or self._profile_rows(profile)
        base_contacts = 200.0
        if rows:
            base_contacts = max(150.0, float(len(rows)) * 0.12)
        arpu_factor = 1.0
        if campaign.get("filter_arpu_segment"):
            segment = str(campaign["filter_arpu_segment"]).upper()
            if segment == "HIGH":
                arpu_factor = 1.5
            elif segment == "MID":
                arpu_factor = 1.2
            elif segment == "LOW":
                arpu_factor = 0.85
        data_factor = 1.0
        if campaign.get("filter_data_segment"):
            segment = str(campaign["filter_data_segment"]).upper()
            if segment == "HEAVY":
                data_factor = 1.3
            elif segment == "LITE":
                data_factor = 0.9
        channel_factor = 1.0
        channel = str(campaign.get("channel", "sms")).lower()
        if channel == "push":
            channel_factor = 0.8
        elif channel == "sms":
            channel_factor = 1.0
        elif channel == "digital_ads":
            channel_factor = 1.3
        elif channel == "call":
            channel_factor = 1.8
        contacts = max(30.0, base_contacts * arpu_factor * data_factor * channel_factor)
        budget_cost = self._channel_cost(channel) * contacts * 0.12
        return {"contacts": contacts, "budget_cost": budget_cost}

    def _channel_cost(self, channel: Optional[str]) -> float:
        if not channel:
            return DEFAULT_CHANNEL_COST.get("sms", 4.0)
        channel_name = str(channel).lower()
        return DEFAULT_CHANNEL_COST.get(channel_name, DEFAULT_CHANNEL_COST.get("sms", 4.0))


def _extract_env_from_local_eval() -> Any:
    try:
        import importlib
        names = ["local_eval", "eval", "main", "runner"]
        for name in names:
            try:
                mod = importlib.import_module(name)
                if hasattr(mod, "make_env"):
                    return mod.make_env()
                if hasattr(mod, "env"):
                    return mod.env
                if hasattr(mod, "Env"):
                    return mod.Env()
            except Exception:
                continue
    except Exception:
        pass
    return None


if __name__ == "__main__":
    env = _extract_env_from_local_eval()
    agent = Agent()
    campaigns = agent.act(env) if env is not None else []
    print(campaigns)
