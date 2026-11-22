# Architecture Decision Records

Binding design decisions for `mobilealerts_cloud`. Each record states the decision, why it was made, and what it implies. A new decision that overrides an old one gets a new record; the old one is marked *Superseded*.

---

## ADR-001: Minimum Home Assistant version 2026.9.0

**Status:** Accepted

**Context.** Every supported Home Assistant release doubles the code paths that need tests, and `pytest-homeassistant-custom-component` tests against one concrete HA release. HA 2026.3+ requires Python 3.14.

**Decision.** The minimum is **2026.9.0** (`hacs.json`, README, `requirements_test_min_ha.txt`). No compatibility guards for older releases. Python 3.14 everywhere. CI runs the test suite on the minimum release as well as on the release pinned in `requirements_test.txt`.

**Consequences.** Less code, a clear test target. Raising the minimum is a breaking change and needs a new record.

---

## ADR-002: Test against real Home Assistant, not stubs

**Status:** Accepted

**Context.** Tests against hand-written imitations of Home Assistant prove behaviour against the imitation, not against HA; entities that never load on real HA can pass such tests.

**Decision.** Tests use `pytest-homeassistant-custom-component` (real HA core, `hass` fixture, `MockConfigEntry` with subentries) and `syrupy` snapshots of entity and device registries per platform. `tests/mock_api_server.py` is the API double. `requirements_test.txt` pins the HA version under test. Snapshots protect unique IDs and names.

**Consequences.** Line and branch coverage stays above 95 %; CI fails below that.

---

## ADR-003: The phone ID is no longer used

**Status:** Deferred. The precondition cannot be checked: nobody involved owns a smoke detector (ID0A). The phone ID stays an optional, opt-in field until a user with an ID0A confirms the behaviour.

**Context.** The API spec makes `phoneid` optional; its only documented effect is that alert flags (`*hi`, `*lo`, `wds`, `a1`–`a4`, …) are added to the response. The integration discards alert flags. Sending a phone ID that is not linked to every queried sensor makes the server answer HTTP 400, which is why the option is opt-in with a retry fallback. The option therefore adds risk and configuration without a benefit.

**Decision.** Remove `phone_id` and `send_phone_id` from setup and reconfigure, and stop sending `phoneid`. Existing entries keep the stored values but they are ignored; a config entry migration may delete them.

**Precondition.** Before removal, verify with real data that the smoke detector adapter (ID0A, `a1`–`a4`) reports its alarm values without a phone ID. If it does not, this ADR is revisited (alternative: keep the phone ID for alert entities).

**Consequences.** Breaking change for users who set a phone ID (CHANGELOG). Simpler flows, one HTTP 400 path less.

---

## ADR-004: Several hubs are allowed; a sensor ID is unique across all hubs

**Status:** Accepted

**Context.** A hub has no natural unique ID (no account, no login). Users may want several hubs, for example with different polling intervals.

**Decision.** Several hub entries are allowed. Uniqueness is enforced per sensor: a sensor ID can exist in only one subentry across all `mobilealerts_cloud` hubs (subentry `unique_id` = sensor ID, checked in both flows). This satisfies the intent of `unique-config-entry` (the same device cannot be set up twice).

**Consequences.** `quality_scale.yaml` documents the rule as done with this reasoning. Services that change state need an explicit target when several hubs are loaded.

---

## ADR-005: Sensors that stop reporting become unavailable

**Status:** Accepted

**Context.** A sensor with a dead battery or out of radio range would otherwise keep showing its last value for days (`lastseen` stops moving), which breaks automations silently (`entity-unavailable`). Mobile Alerts sensors transmit roughly every 7 minutes; the server keeps the last measurement.

**Decision.** A sensor's entities are unavailable when `lastseen` (`max(lastseen, c)`) is older than **max(60 min, 3 × polling interval)**. The diagnostic `last_seen` sensor stays available so users can see when it last reported. One warning per sensor when it goes stale, one info when it recovers (`log-when-unavailable`).

**Consequences.** Automations see `unavailable` instead of stale values. A sensor missing from a response is treated the same way once its last reading is too old.

---

## ADR-006: ID04 defaults to the water sensor

**Status:** Accepted. The deviation is documented in the README. Verification with anonymised MA 10350 data is still open: no such data is available yet.

**Context.** The API spec documents ID04 `t2` as a cable temperature in °C. The MA 10350 (prefix 04) ships with a water detector, and the integration maps `t2` to a moisture binary sensor by default, with the `ID04_CABLE` profile override for cable probes. The spec is copied between device types in several places, so it is not conclusive.

**Decision.** Keep the water sensor as the default unless anonymised MA 10350 data contradicts it. Document the deviation from the spec in the README (supported devices, known limitations). Unique IDs of existing entities do not change either way.

**Consequences.** If real data contradicts the default, a new ADR decides with a registry migration.

---

## ADR-007: Process-wide state outside `runtime_data`

**Status:** Accepted, amended by ADR-009

**Context.** Two things must outlive a single config entry: the rate-limit state (a reload must not reset the API budget, otherwise it risks a 7 minute block) and a reload cache (60 s, so that a reload right after a flow does not query the API again).

**Decision.** Per-hub objects live in `entry.runtime_data`. The rate-limit state and the reload cache are process-wide, behind one typed `HassKey[MobileAlertsCloudData]`, documented as the only state outside `runtime_data`. Services resolve entries through `hass.config_entries` and `config_entry_id`.

---

## ADR-008: The project is called Mobile Alerts Cloud (`mobilealerts_cloud`)

**Status:** Accepted

**Context.** The name should say what the integration is on its own terms, not in relation to the older `mobile_alerts` integration.

**Decision.** Name "Mobile Alerts Cloud", domain `mobilealerts_cloud`, repository `mobilealerts-cloud`, event `mobilealerts_cloud_key_press`, actions `mobilealerts_cloud.*`. The name describes what the integration uses (the official cloud REST API) and sets it apart from solutions that read the gateway locally. It is a separate domain from `mobile_alerts`, so side-by-side operation and the takeover work.

**Consequences.** Renaming the domain later would need a registry migration for every installation; the name is therefore fixed.

---

## ADR-009: One rate-limit state per Home Assistant instance

**Status:** Accepted

**Context.** The IP block (HTTP 403 after more than 5 invalid calls in 15 minutes) applies to the whole IP address, so separate rate-limit states per hub or flow would let one hub keep calling the API while another is blocked. A rejected phone ID (HTTP 400) must not block a corrected phone ID.

**Decision.** `MobileAlertsCloudData` holds exactly one `RateLimitState`; `get_rate_limit_state(hass)` returns it to every hub, flow, action and the migration. Rejected phone IDs are stored by value (`rejected_phone_ids`), so a new phone ID gets its own chance. The API endpoint is a constant; tests inject the mock URL.

**Consequences.** Per-sensor budgets are unaffected because sensor IDs are unique across hubs (ADR-004), and an IP block stops every request.
