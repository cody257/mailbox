"""Fail closed on OSV findings or unavailable advisory service for vendored JS."""
import json
import urllib.request

PACKAGES = {"marked": "11.2.0", "dompurify": "3.4.17"}

request = urllib.request.Request(
    "https://api.osv.dev/v1/querybatch",
    data=json.dumps({"queries": [{"package": {"name": name, "ecosystem": "npm"}, "version": version} for name, version in PACKAGES.items()]}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    results = json.load(response)["results"]
if len(results) != len(PACKAGES):
    raise SystemExit("Incomplete advisory response.")
findings = {name: [v["id"] for v in result.get("vulns", [])] for name, result in zip(PACKAGES, results) if result.get("vulns")}
if findings:
    raise SystemExit(json.dumps(findings))
print("No known OSV advisories for the two pinned browser libraries.")
