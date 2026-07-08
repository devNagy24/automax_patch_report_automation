#!/usr/bin/env python3
"""Generate an Automox patch impact/performance report pack.

The script is intentionally read-only. It writes local CSV and HTML outputs
from Automox Console API data and never stores API keys.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://console.automox.com/api"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(value: Any) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def load_config(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_setting(config: dict[str, Any], key: str, env_name: str | None = None, default: Any = None) -> Any:
    env_name = env_name or f"AUTOMOX_{key.upper()}"
    return os.environ.get(env_name) or config.get(key, default)


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(item, child))
        return out
    if isinstance(value, list):
        return {prefix: json.dumps(value, ensure_ascii=False)}
    return {prefix: value}


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item if isinstance(item, dict) else {"value": item} for item in payload]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "items", "rows", "servers", "packages", "worklets", "policies", "policy_runs"):
        item = payload.get(key)
        if isinstance(item, list):
            return [row if isinstance(row, dict) else {"value": row} for row in item]
        if isinstance(item, dict):
            nested = rows_from_payload(item)
            if nested:
                return nested
    if "metadata" in payload and len(payload) == 1:
        return []
    return [payload]


def deep_values(obj: Any, wanted: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = key.lower()
            if any(token in lower for token in wanted):
                values.append(value)
            values.extend(deep_values(value, wanted))
    elif isinstance(obj, list):
        for value in obj:
            values.extend(deep_values(value, wanted))
    return values


def first_value(row: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    flat = flatten(row)
    lower_map = {key.lower(): value for key, value in flat.items()}
    for key in keys:
        if key.lower() in lower_map and lower_map[key.lower()] not in (None, ""):
            return str(lower_map[key.lower()])
    for key, value in lower_map.items():
        if any(token in key for token in keys) and value not in (None, ""):
            return str(value)
    return default


class AutomoxClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL) -> None:
        if not api_key:
            raise ValueError("AUTOMOX_API_KEY is required.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "automox-patch-report/1.0",
            },
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    text = response.read().decode("utf-8")
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    time.sleep(60)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Automox API error {exc.code} for {path}: {body[:500]}") from exc

    def page_all(self, path: str, params: dict[str, Any] | None = None, limit: int = 500) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        page = 0
        while True:
            payload = self.get(path, {**(params or {}), "page": page, "limit": limit})
            batch = rows_from_payload(payload)
            all_rows.extend(batch)
            if len(batch) < limit:
                return all_rows
            page += 1

    def offset_all(self, path: str, params: dict[str, Any] | None = None, limit: int = 250) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self.get(path, {**(params or {}), "offset": offset, "limit": limit})
            batch = rows_from_payload(payload)
            all_rows.extend(batch)
            if len(batch) < limit:
                return all_rows
            offset += limit


def resolve_org(client: AutomoxClient, config: dict[str, Any]) -> tuple[int, str, dict[str, Any]]:
    org_id = get_setting(config, "org_id")
    org_uuid = get_setting(config, "org_uuid")
    org_name = get_setting(config, "org_name")
    account_uuid = get_setting(config, "account_uuid")
    organizations = client.page_all("/orgs", limit=500)
    if account_uuid:
        try:
            zones = client.page_all(f"/accounts/{account_uuid}/zones", limit=500)
            seen = {str(org.get("uuid") or org.get("id")) for org in organizations}
            organizations.extend(zone for zone in zones if str(zone.get("uuid") or zone.get("id")) not in seen)
        except Exception:
            pass
    selected: dict[str, Any] | None = None

    if org_id:
        selected = next((org for org in organizations if str(org.get("id")) == str(org_id)), None)
    if not selected and org_uuid:
        selected = next((org for org in organizations if str(org.get("uuid", "")).lower() == str(org_uuid).lower()), None)
    if not selected and org_name:
        selected = next((org for org in organizations if str(org.get("name", "")).lower() == str(org_name).lower()), None)
    if not selected and len(organizations) == 1:
        selected = organizations[0]
    if not selected:
        choices = ", ".join(f"{org.get('name')} (id={org.get('id')}, uuid={org.get('uuid')})" for org in organizations[:10])
        raise RuntimeError(f"Unable to pick an organization. Set org_id or org_uuid in config. Available: {choices}")

    numeric_id = selected.get("id")
    uuid = selected.get("uuid") or org_uuid
    if not numeric_id or not uuid:
        raise RuntimeError("Selected organization is missing id or uuid in the API response.")
    return int(numeric_id), str(uuid), selected


def filter_devices(rows: list[dict[str, Any]], excluded_group_names: list[str]) -> list[dict[str, Any]]:
    excluded = [name.lower() for name in excluded_group_names]
    if not excluded:
        return rows
    kept = []
    for row in rows:
        group = first_value(row, ("server_group_name", "group_name", "group", "server_group"))
        if not any(skip and skip in group.lower() for skip in excluded):
            kept.append(row)
    return kept


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat_rows = [flatten(row) for row in rows]
    fieldnames = sorted({key for row in flat_rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in flat_rows:
            writer.writerow(row)


def filtered_packages(rows: list[dict[str, Any]], excluded_names: list[str]) -> list[dict[str, Any]]:
    excluded = [name.lower() for name in excluded_names]
    if not excluded:
        return rows
    kept = []
    for row in rows:
        name = first_value(row, ("display_name", "package_name", "name"))
        if not any(skip and skip in name.lower() for skip in excluded):
            kept.append(row)
    return kept


def expand_prepatch_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for row in rows:
        container = row.get("prepatch") if isinstance(row.get("prepatch"), dict) else row
        devices = container.get("devices") if isinstance(container, dict) else None
        if not isinstance(devices, list):
            expanded.append(row)
            continue
        for device in devices:
            if not isinstance(device, dict):
                continue
            patches = device.get("patches")
            if not isinstance(patches, list) or not patches:
                expanded.append(
                    {
                        "device_id": device.get("id"),
                        "device_name": device.get("name"),
                        "device_group": device.get("group"),
                        "connected": device.get("connected"),
                        "needs_reboot": device.get("needsReboot"),
                        "os_family": device.get("os_family"),
                        "compliant": device.get("compliant"),
                    }
                )
                continue
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                expanded.append(
                    {
                        "device_id": device.get("id"),
                        "device_name": device.get("name"),
                        "device_group": device.get("group"),
                        "connected": device.get("connected"),
                        "needs_reboot": device.get("needsReboot"),
                        "os_family": device.get("os_family"),
                        "compliant": device.get("compliant"),
                        "package_id": patch.get("id"),
                        "package_version_id": patch.get("packageVersionId"),
                        "package_name": patch.get("name"),
                        "severity": patch.get("severity"),
                        "cve": patch.get("cve"),
                        "create_time": patch.get("createTime"),
                        "patch_time": patch.get("patchTime"),
                        "needs_approval": patch.get("needsApproval"),
                    }
                )
    return expanded


def expand_needs_attention_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for row in rows:
        container = row.get("nonCompliant") if isinstance(row.get("nonCompliant"), dict) else row
        devices = container.get("devices") if isinstance(container, dict) else None
        if not isinstance(devices, list):
            expanded.append(row)
            continue
        for device in devices:
            if not isinstance(device, dict):
                continue
            policies = device.get("policies")
            if not isinstance(policies, list) or not policies:
                expanded.append(
                    {
                        "device_id": device.get("id"),
                        "device_name": device.get("name"),
                        "connected": device.get("connected"),
                        "needs_reboot": device.get("needsReboot"),
                        "group_id": device.get("groupId"),
                        "os_family": device.get("os_family"),
                        "compliant": device.get("compliant"),
                        "disconnected_thirty_days": device.get("disconnectedThirtyDays"),
                    }
                )
                continue
            for policy in policies:
                if not isinstance(policy, dict):
                    continue
                packages = policy.get("packages")
                if not isinstance(packages, list) or not packages:
                    expanded.append(
                        {
                            "device_id": device.get("id"),
                            "device_name": device.get("name"),
                            "connected": device.get("connected"),
                            "needs_reboot": device.get("needsReboot"),
                            "group_id": device.get("groupId"),
                            "os_family": device.get("os_family"),
                            "compliant": device.get("compliant"),
                            "policy_id": policy.get("id"),
                            "policy_name": policy.get("name"),
                            "policy_type": policy.get("type"),
                            "reason_for_fail": policy.get("reasonForFail"),
                        }
                    )
                    continue
                for package in packages:
                    if not isinstance(package, dict):
                        continue
                    expanded.append(
                        {
                            "device_id": device.get("id"),
                            "device_name": device.get("name"),
                            "connected": device.get("connected"),
                            "needs_reboot": device.get("needsReboot"),
                            "group_id": device.get("groupId"),
                            "os_family": device.get("os_family"),
                            "compliant": device.get("compliant"),
                            "policy_id": policy.get("id"),
                            "policy_name": policy.get("name"),
                            "policy_type": policy.get("type"),
                            "reason_for_fail": policy.get("reasonForFail"),
                            "package_id": package.get("id"),
                            "package_version_id": package.get("packageVersionId"),
                            "package_name": package.get("name"),
                            "severity": package.get("severity"),
                            "create_time": package.get("createTime"),
                        }
                    )
    return expanded


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def package_matches(name: str, wanted_names: list[str]) -> bool:
    if not wanted_names:
        return True
    lowered = name.lower()
    return any(wanted.lower() in lowered for wanted in wanted_names if wanted)


def normalize_software_inventory(
    rows: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    wanted_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    device_lookup = {
        str(device.get("id")): device
        for device in devices
        if device.get("id") not in (None, "")
    }
    normalized: list[dict[str, Any]] = []
    wanted_names = wanted_names or []

    for row in rows:
        package_name = first_value(row, ("display_name", "name", "package_name", "software_name"))
        if not package_matches(package_name, wanted_names):
            continue

        device_id = first_value(row, ("server_id", "device_id", "serverId", "deviceId"))
        device = device_lookup.get(str(device_id), {})
        device_name = (
            first_value(row, ("server_name", "device_name", "hostname", "systemname"))
            or first_value(device, ("display_name", "name", "hostname"))
        )

        normalized.append(
            {
                "software_name": package_name,
                "version": first_value(row, ("version", "package_version", "display_version")),
                "device_id": device_id,
                "device_name": device_name,
                "device_group_id": first_value(device, ("server_group_id", "group_id")),
                "os_family": first_value(row, ("os_family", "osFamily")) or first_value(device, ("os_family",)),
                "os_name": first_value(row, ("os_name", "osName")) or first_value(device, ("os_name",)),
                "installed": first_value(row, ("installed",)),
                "install_date": first_value(row, ("install_date", "installed_at", "installed_time", "create_time", "createTime")),
                "package_id": first_value(row, ("id", "package_id", "packageId")),
                "package_version_id": first_value(row, ("package_version_id", "packageVersionId")),
                "software_id": first_value(row, ("software_id", "softwareId")),
                "repo": first_value(row, ("repo", "repository")),
                "severity": first_value(row, ("severity",)),
                "cves": first_value(row, ("cves", "cve")),
            }
        )
    return normalized


def software_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (row.get("software_name") or "Unknown", row.get("version") or "Unknown")
        device_key = str(row.get("device_id") or row.get("device_name") or "")
        grouped.setdefault(key, set()).add(device_key)

    summary_rows = [
        {
            "software_name": software_name,
            "version": version,
            "install_count": len(devices),
        }
        for (software_name, version), devices in grouped.items()
    ]
    return sorted(summary_rows, key=lambda item: (-as_int(item["install_count"]), item["software_name"].lower(), item["version"].lower()))


def summarize(
    org: dict[str, Any],
    devices: list[dict[str, Any]],
    applied_events: list[dict[str, Any]],
    failed_events: list[dict[str, Any]],
    policy_runs: list[dict[str, Any]],
    outstanding_packages: list[dict[str, Any]],
    needs_attention: list[dict[str, Any]],
    software_inventory: list[dict[str, Any]],
    target_mttp_days: int,
) -> dict[str, Any]:
    successes = sum(as_int(row.get("success")) for row in policy_runs)
    failures = sum(as_int(row.get("failed")) for row in policy_runs)
    if not successes and not failures:
        status_values = [first_value(row, ("result_status", "status", "status_name")).lower() for row in policy_runs]
        successes = sum(1 for value in status_values if "success" in value)
        failures = sum(1 for value in status_values if "fail" in value or "error" in value)
    total_policy_runs = successes + failures or len(policy_runs)

    mttp_samples: list[float] = []
    for event in applied_events:
        install_time = parse_iso(first_value(event, ("create_time", "install_time", "installed_at", "event_time")))
        source_dates = [parse_iso(str(value)) for value in deep_values(event, ("release", "available", "create"))]
        source_dates = [value for value in source_dates if value and install_time and value < install_time]
        if install_time and source_dates:
            earliest = min(source_dates)
            mttp_samples.append((install_time - earliest).total_seconds() / 86400)

    mttp = round(sum(mttp_samples) / len(mttp_samples), 2) if mttp_samples else None
    return {
        "organization": org.get("name", ""),
        "organization_id": org.get("id", ""),
        "organization_uuid": org.get("uuid", ""),
        "device_count": len(devices),
        "applied_patch_events": len(applied_events),
        "failed_patch_events": len(failed_events),
        "policy_runs": len(policy_runs),
        "policy_success_rate": round((successes / total_policy_runs) * 100, 2) if total_policy_runs else None,
        "outstanding_patch_instances": len(outstanding_packages),
        "needs_attention_devices": len({first_value(row, ("device_id", "device_name", "name")) for row in needs_attention}),
        "software_inventory_installs": len(software_inventory),
        "unique_software_titles": len({(row.get("software_name") or "").lower() for row in software_inventory if row.get("software_name")}),
        "mttp_days": mttp,
        "target_mttp_days": target_mttp_days,
        "mttp_samples": len(mttp_samples),
    }


def top_counts(rows: list[dict[str, Any]], keys: tuple[str, ...], limit: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in rows:
        value = first_value(row, keys, "Unknown").strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        counter[value] += 1
    return counter.most_common(limit)


def policy_result_counts(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    totals = {
        "SUCCESS": sum(as_int(row.get("success")) for row in rows),
        "FAIL": sum(as_int(row.get("failed")) for row in rows),
        "PENDING": sum(as_int(row.get("pending")) for row in rows),
        "NOT INCLUDED": sum(as_int(row.get("not_included")) for row in rows),
        "NOT APPLICABLE": sum(as_int(row.get("remediation_not_applicable")) for row in rows),
    }
    return [(key, value) for key, value in totals.items() if value]


def summary_top_counts(rows: list[dict[str, Any]], name_key: str, count_key: str, limit: int = 10) -> list[tuple[str, int]]:
    counts: list[tuple[str, int]] = []
    for row in rows[:limit]:
        label = str(row.get(name_key) or "Unknown")
        version = str(row.get("version") or "")
        if version and version.lower() != "unknown":
            label = f"{label} ({version})"
        counts.append((label, as_int(row.get(count_key))))
    return counts


def html_table(rows: list[tuple[str, int]], first_header: str) -> str:
    body = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>"
        for name, count in rows
    )
    return f"<table><thead><tr><th>{html.escape(first_header)}</th><th>Count</th></tr></thead><tbody>{body}</tbody></table>"


def write_html(path: Path, summary: dict[str, Any], top_tables: dict[str, list[tuple[str, int]]], generated_at: dt.datetime) -> None:
    generated_display = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    cards = "\n".join(
        f"<section class='card'><span>{html.escape(key.replace('_', ' ').title())}</span><strong>{html.escape(str(value if value is not None else 'n/a'))}</strong></section>"
        for key, value in summary.items()
        if key not in {"organization_uuid"}
    )
    tables = "\n".join(
        f"<section><h2>{html.escape(title)}</h2>{html_table(rows, title)}</section>"
        for title, rows in top_tables.items()
        if rows
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Automox Patch Impact Report</title>
  <style>
    :root {{ color-scheme: light; font-family: Segoe UI, Arial, sans-serif; }}
    body {{ margin: 0; background: #f6f7f9; color: #172033; }}
    header {{ background: #172033; color: white; padding: 28px 34px; }}
    header p {{ margin: 6px 0 0; color: #cbd5e1; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
    .card {{ background: white; border: 1px solid #d8dee8; border-radius: 6px; padding: 14px; }}
    .card span {{ display: block; color: #576174; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .card strong {{ display: block; margin-top: 8px; font-size: 24px; }}
    section {{ margin-top: 22px; }}
    h2 {{ font-size: 18px; margin: 0 0 8px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8dee8; border-radius: 6px; overflow: hidden; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #e7ebf1; text-align: left; }}
    th {{ background: #eef2f7; font-size: 12px; text-transform: uppercase; color: #465164; }}
  </style>
</head>
<body>
  <header>
    <h1>Automox Patch Impact Report</h1>
    <p>{html.escape(str(summary.get('organization', '')))} - generated {html.escape(generated_display)}</p>
  </header>
  <main>
    <div class="grid">{cards}</div>
    {tables}
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Automox patch impact report outputs.")
    parser.add_argument("--config", type=Path, help="Path to JSON config.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days.")
    parser.add_argument("--output-dir", type=Path, default=Path("automox-report-output"), help="Output directory.")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and show planned endpoints without calling Automox.")
    parser.add_argument("--skip-software-inventory", action="store_true", help="Skip installed software inventory collection.")
    args = parser.parse_args()

    config = load_config(args.config)
    api_key = get_setting(config, "api_key")
    base_url = get_setting(config, "base_url", default=DEFAULT_BASE_URL)
    account_uuid = get_setting(config, "account_uuid")
    target_mttp_days = int(config.get("target_mttp_days", 7))
    excluded_names = list(config.get("excluded_package_names", []))
    excluded_group_names = list(config.get("excluded_group_names", []))
    tracked_software_names = list(config.get("tracked_software_names", []))

    end = utc_now()
    start = end - dt.timedelta(days=args.days)

    if args.dry_run:
        print("Dry run: no API calls made.")
        print(f"Base URL: {base_url}")
        print(f"Account UUID configured: {'yes' if account_uuid else 'no'}")
        print(f"Org ID configured: {'yes' if get_setting(config, 'org_id') else 'no'}")
        print(f"Org UUID configured: {'yes' if get_setting(config, 'org_uuid') else 'no'}")
        print("Endpoints: /orgs, /servers, /events, /policy-history/policy-runs, /reports/prepatch, /reports/needs-attention, /orgs/{orgID}/packages")
        return 0

    client = AutomoxClient(api_key, base_url)
    org_id, org_uuid, org = resolve_org(client, config)
    out = args.output_dir / end.strftime("%Y-%m-%d_%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)

    devices = client.page_all("/servers", {"o": org_id, "include_details": 1, "include_next_patch_time": 1}, limit=500)
    devices = filter_devices(devices, excluded_group_names)
    applied_events = client.page_all(
        "/events",
        {"o": org_id, "eventName": "system.patch.applied", "startDate": start.date().isoformat(), "endDate": end.date().isoformat()},
        limit=500,
    )
    failed_events = client.page_all(
        "/events",
        {"o": org_id, "eventName": "system.patch.failed", "startDate": start.date().isoformat(), "endDate": end.date().isoformat()},
        limit=500,
    )
    policy_runs = client.page_all(
        "/policy-history/policy-runs",
        {"org": org_uuid, "start_time": start.isoformat().replace("+00:00", "Z"), "end_time": end.isoformat().replace("+00:00", "Z")},
        limit=5000,
    )
    outstanding = client.offset_all("/reports/prepatch", {"o": org_id}, limit=250)
    needs_attention = client.offset_all("/reports/needs-attention", {"o": org_id}, limit=250)
    outstanding = expand_prepatch_rows(outstanding)
    needs_attention = expand_needs_attention_rows(needs_attention)
    outstanding = filtered_packages(outstanding, excluded_names)
    needs_attention = filtered_packages(needs_attention, excluded_names)
    installed_packages = []
    software_inventory: list[dict[str, Any]] = []
    tracked_software_inventory: list[dict[str, Any]] = []
    if not args.skip_software_inventory:
        installed_packages = client.page_all(
            f"/orgs/{org_id}/packages",
            {"awaiting": 0, "includeUnmanaged": 1},
            limit=500,
        )
        software_inventory = normalize_software_inventory(installed_packages, devices)
        if tracked_software_names:
            tracked_software_inventory = normalize_software_inventory(installed_packages, devices, tracked_software_names)

    software_summary_rows = software_summary(software_inventory)
    tracked_software_summary_rows = software_summary(tracked_software_inventory)

    write_csv(out / "devices.csv", devices)
    write_csv(out / "patch_events_applied.csv", applied_events)
    write_csv(out / "patch_events_failed.csv", failed_events)
    write_csv(out / "policy_runs.csv", policy_runs)
    write_csv(out / "outstanding_packages.csv", outstanding)
    write_csv(out / "needs_attention.csv", needs_attention)
    write_csv(out / "software_inventory.csv", software_inventory)
    write_csv(out / "software_inventory_summary.csv", software_summary_rows)
    write_csv(out / "tracked_software_inventory.csv", tracked_software_inventory)
    write_csv(out / "tracked_software_summary.csv", tracked_software_summary_rows)

    summary = summarize(org, devices, applied_events, failed_events, policy_runs, outstanding, needs_attention, software_inventory, target_mttp_days)
    write_csv(out / "summary.csv", [summary])
    top_tables = {
        "Top Applied Packages": top_counts(applied_events, ("data.patches", "package_name", "package", "display_name", "name")),
        "Top Outstanding Packages": top_counts(outstanding, ("package_name", "display_name", "name")),
        "Devices With Outstanding Patches": top_counts(outstanding, ("device_name", "server_name", "hostname", "name")),
        "Policy Execution Results": policy_result_counts(policy_runs),
        "Top Installed Software": summary_top_counts(software_summary_rows, "software_name", "install_count"),
    }
    if tracked_software_summary_rows:
        top_tables["Tracked Software Installs"] = summary_top_counts(tracked_software_summary_rows, "software_name", "install_count")
    write_html(out / "report.html", summary, top_tables, end)
    print(f"Wrote Automox report outputs to {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
