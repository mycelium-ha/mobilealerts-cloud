# Mobile Alerts API: spec notes

Deviations, gaps and live-data observations around the official spec ([PDF](https://mobile-alerts.eu/info/public_server_api_documentation.pdf)):

- `deviceids`: the text says colon-separated, the example uses commas (the code uses commas, confirmed by live data). No maximum number of IDs per request is documented; consider chunking.
- Rate limits: 3 calls per sensor per minute, else HTTP 429 and a 7 minute block; more than 5 invalid calls per IP in 15 minutes, else HTTP 403.
- Error body: `success: false`, `errorcode`, `errormessage`; only code 1 appears in the spec.
- Battery: the spec names `lowbat`; live data also has `lowbattery` and `lb`.
- Sentinels: 43530 (not connected) and 65295 (out of range); 65296 comes from live data.
- ID15: `kpXt` 0 = no press, 1 short, 2 double, 3 long; `kpXc` running counter. Polling loses presses between two polls (known limitation).
- ID17: `t2` 0.0 = AC on, 1.0 = off.
- Alert flags are only returned with a phone ID (see ADR-003).
- ID08 (rain gauge, MA 10650): live data shows a transmission about every 2 hours, not every 7 minutes, unchanged after a battery replacement, with occasional short bursts. The stale threshold for this profile follows ADR-010. Whether the spec names an interval is not verified.
