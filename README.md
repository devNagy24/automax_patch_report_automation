# Automox Patch Impact Report Automation

This pack creates a read-only Automox patch report similar to the supplied "Patch Impact and Performance - 30 Days" PDF. It writes CSV files and a printable `report.html`.

## What It Pulls

- Organizations: resolves numeric org ID and org UUID from the API key.
- Devices: `/servers`.
- Applied patch events: `/events?eventName=system.patch.applied`.
- Failed patch events: `/events?eventName=system.patch.failed`.
- Policy execution history: `/policy-history/policy-runs`.
- Outstanding patch posture: `/reports/prepatch`.
- Needs-attention devices: `/reports/needs-attention`.
- Installed software inventory: `/orgs/{orgID}/packages?awaiting=0&includeUnmanaged=1`.
- Optional service inventory search: `/device-details/orgs/{orgUUID}/devices/{deviceUUID}/inventory?category=Services`.

The Automox Console API spec used here is version `2026-06-19` from `https://console.automox.com/api/docs/specs/console-api.json`. The Cloud Worklets docs use organization-scoped bearer auth under `https://console.automox.com/api/organizations/{orgUuid}`; this report uses the Console API because the PDF is report/policy/package data, not a Cloud Worklet execution.

## Setup

1. Copy `config.example.json` to `config.json`.
2. Fill in either `org_id`, `org_uuid`, or `org_name`.
3. Set the API key as a user environment variable. Do not put it in `config.json`.

```powershell
[Environment]::SetEnvironmentVariable("AUTOMOX_API_KEY", "YOUR_API_KEY", "User")
```

Open a new PowerShell window after setting the variable.

## Manual Run

```powershell
.\run_automox_report.ps1 -ConfigPath .\config.json -OutputRoot .\reports -Days 30
```

Outputs are written to a timestamped folder under `reports`.

Key software inventory outputs:

- `software_inventory.csv`: one row per installed software package/device instance.
- `software_inventory_summary.csv`: install counts grouped by software and version.
- `tracked_software_inventory.csv`: optional drilldown rows for software names in `tracked_software_names`.
- `tracked_software_summary.csv`: optional counts for tracked software grouped by software and version.

Use `software_inventory_summary.csv` to answer install-count questions for any software title in the environment. Use `software_inventory.csv` for the host-level drilldown, including device name, version, OS metadata, and install/date fields when Automox provides them.

`tracked_software_names` is optional. Leave it empty to report the full software inventory only, or add specific names when you want an extra filtered CSV/table. Matching is case-insensitive and partial.

## Service Inventory Search

To find machines with matching services, set `service_search_terms` in `config.json`.

```json
"service_search_terms": [
  "Druvstar"
],
"service_running_only": true
```

When configured, the report checks each device's Services inventory and writes:

- `service_inventory_matches.csv`: matching service rows with device name, UUID, OS metadata, service fields, and matched terms.
- `service_inventory_errors.csv`: devices whose service inventory could not be queried.

The HTML report also adds service match summary tables when matches exist. Service inventory requires one Automox API call per device, so this section may make the report slower on large environments.

## Validate Without Calling Automox

```powershell
py .\automox_patch_report.py --config .\config.json --dry-run
```

## Schedule Daily On Windows

```powershell
.\Register-AutomoxPatchReportTask.ps1 -RunAt "06:00" -ConfigPath .\config.json -OutputRoot .\reports
```

The scheduled task relies on the `AUTOMOX_API_KEY` user environment variable. It does not store the key in the task definition.

## Notes

- The supplied Agent Access Key is not used for reporting. Agent access keys are for device/agent enrollment workflows.
- Exact MTTP is only calculated when the API payload includes both install time and a comparable release/available/create timestamp for the patch. Otherwise the HTML shows `n/a` and still includes the raw CSVs needed to refine the calculation.
- The package exclusion list is seeded from the PDF filters and can be edited in `config.json`.

