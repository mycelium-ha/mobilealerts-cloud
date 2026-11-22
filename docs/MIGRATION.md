# Migration Guide: Legacy `mobile_alerts` → Mobile Alerts Cloud (`mobilealerts_cloud`)

This step-by-step guide helps you move safely from the legacy `mobile_alerts` custom integration to **Mobile Alerts Cloud (`mobilealerts_cloud`)**—preserving your **device names, areas, labels, entity names, icons, aliases, Voice/Assist exposure, display precision, dashboards, automations, Utility Meters, and historical Recorder / Long-Term Statistics data**.

---

## 1. Before You Start (Prerequisites & Backup)

1. **Home Assistant version:** `2026.9.0` or newer.
2. **Create a Backup first:**
   - Go to **Settings → System → Backups → Create Backup** before running a cutover migration, just in case you want to roll back.
3. **Know how Marketplace / HACS works in Home Assistant:**
   - **Home Assistant `2026.11+` (Marketplace):** Starting with HA `2026.11`, HACS is integrated directly into Home Assistant Core under **Settings → Marketplace**. Adding a **custom repository** (such as `https://github.com/mycelium-ha/mobilealerts-cloud`) in Marketplace still requires connecting your GitHub account via the device coupling dialog in Marketplace settings.
   - **Home Assistant `2026.9`–`2026.10` (HACS):** Go to **HACS → Integrations → ⋮ → Custom repositories**, add `https://github.com/mycelium-ha/mobilealerts-cloud` as category **Integration**, install **Mobile Alerts Cloud**, and restart Home Assistant.

> [!IMPORTANT]
> **Keep legacy `mobile_alerts` installed until the migration finishes!**
> `mobilealerts_cloud` reads your legacy config entries, devices, and entities directly from Home Assistant's registries. Do **not** uninstall or delete the legacy integration beforehand.

---

## 2. What Gets Migrated Automatically vs. What Changes

| Item | Side-by-Side Mode (`cutover: false`) | Full Cutover Mode (`cutover: true`) |
|---|---|---|
| **Sensors & Profile Overrides** (`MA10300` on `04...` → `ID04_CABLE`) | Missing sensors imported as Subentries | Missing sensors imported as Subentries |
| **Device Name (`name_by_user`), Area & Labels** | Copied to Mobile Alerts Cloud devices | Copied to Mobile Alerts Cloud devices |
| **Entity Custom Name, Icon, Area, Aliases, Labels** | Copied to Mobile Alerts Cloud entities | Copied to Mobile Alerts Cloud entities |
| **User Disabled / Hidden State** | Copied (user-only; system defaults untouched) | Copied (user-only; system defaults untouched) |
| **Assist / Google / Alexa Exposure & Explicit Display Precision** | Copied to Mobile Alerts Cloud entities | Copied to Mobile Alerts Cloud entities |
| **`entity_id` + Recorder History & Long-Term Statistics** | Legacy keeps old `entity_id`s (parallel testing) | **Legacy entries removed & Mobile Alerts Cloud `sensor.*` reuses exact old `entity_id`s** |

### Why `sensor.*` History Is 100% Preserved—and What Changes Domain

In Home Assistant, Recorder history and Long-Term Statistics (including Utility Meters, Energy/Weather graphs, dashboards, and automations) are tied to the **`entity_id`**, not the integration domain.

- **All standard measurement channels stay in `sensor.*`** (`t1`–`t4`, `h`, `h1`–`h4`, `h3havg`–`h30davg`, `r`, `rf`, `rr`, `ws`, `wg`, `wd`, `wd_degrees`, `ap`, `ppm`, `kp1c`–`kp4c`, `last_seen`). When `cutover: true` removes the legacy entry and renames the new `mobilealerts_cloud` sensor back to your old `entity_id` (for example `sensor.wohnung1_temperatur1` or `sensor.regensensor`), **all history, statistics, dashboards, and Utility Meters continue seamlessly without a gap**.
- **Domain upgrades (cannot inherit `sensor.*` Recorder graphs):**
  The legacy integration created several binary or button-press channels as text `sensor.*` entities. `mobilealerts_cloud` uses native Home Assistant platforms instead:
  - `battery` → **`binary_sensor.*_battery`** (`BinarySensorDeviceClass.BATTERY`, `on` = Low battery, `off` = Normal)
  - `water` → **`binary_sensor.*_water_detected`** (`BinarySensorDeviceClass.MOISTURE`)
  - `w` / `contact` → **`binary_sensor.*_contact`** (`BinarySensorDeviceClass.OPENING`)
  - `ac_power` → **`binary_sensor.*_ac_power`** (`BinarySensorDeviceClass.POWER`, `on` = AC power present)
  - `kp1t`..`kp4t` (`ID15` switch) → **`event.*_button_1`..`4`** (`EventEntity`)
  Because Home Assistant does not allow renaming a `sensor.*` entity ID to `binary_sensor.*` or `event.*`, metadata (custom name, area, labels, icon, aliases, Assist/Google/Alexa exposure) is still copied, while automations/dashboards referencing those binary or switch channels should be updated to the new `binary_sensor.*` / `event.*` entity IDs.

---

## 3. Scenario A: Fresh Hub Setup with Automatic Legacy Sensor Import

If you have **not** added the **Mobile Alerts Cloud** hub yet:

1. Go to **Settings → Devices & Services → Add Integration** and select **Mobile Alerts Cloud**.
2. Because legacy `mobile_alerts` devices exist in your system, the setup form automatically shows the toggle:
   - **Import sensors from legacy `mobile_alerts`** *(enabled by default)*.
3. **Recommended for parallel testing:** Set **Device name prefix** to `Test ` so new test devices stand out clearly while leaving your existing `mobile_alerts` setup completely untouched.
4. Submit the form. `mobilealerts_cloud` validates all discovered sensor IDs in **one single batched REST API call** and creates one Subentry per sensor (including `ID04_CABLE` when your legacy device was `MA10300`).
5. Once verified, follow **Scenario B** below to transfer entity names, areas, Assist settings, and Recorder history.

---

## 4. Scenario B: Step-by-Step Migration & Cutover (`mobilealerts_cloud.migrate_from_legacy`)

Whether you imported sensors during setup or already run `mobilealerts_cloud` side by side with `mobile_alerts`, use the **`mobilealerts_cloud.migrate_from_legacy`** action under **Developer Tools → Actions** (*Entwicklerwerkzeuge → Aktionen*).

### Step 1: Run a Mandatory Dry-Run Preview (`dry_run: true`)

Nothing is changed during a dry run—not even a single API call is made. Check what the cutover will do:

```yaml
action: mobilealerts_cloud.migrate_from_legacy
data:
  dry_run: true
  cutover: true
```

*(If you have multiple Mobile Alerts Cloud hubs, also pass `config_entry_id: "<your_hub_entry_id>"`, or pick the hub in the action editor.)*

Inspect the YAML response carefully:
- **`summary`:** Counts of devices to import vs. already present, device metadata updates, same-domain entities (with history preserved), domain-changed entities, conflicts, and legacy entries to remove.
- **`entity_migrations`:** Lists every legacy `entity_id` alongside its target `mobilealerts_cloud` channel and confirms `history_preserved: true`.
- **`domain_changed_entities`:** Shows any `sensor.*` → `binary_sensor.*` / `event.*` transitions so you know which battery/contact/water/power automations or cards to update afterward.
- **`conflicts`:** Lists any target `entity_id` currently held by a third integration (for example, another integration occupying the same name). Those entities will still get their metadata copied, without forcing an ID collision.
- **`warnings`:** Highlights hints such as whether `name_prefix` is currently set on your Mobile Alerts Cloud hub.

### Step 2 (Optional): Copy Metadata First Without Removing Legacy (`cutover: false`)

If you want to keep running both integrations in parallel a bit longer, run with `cutover: false`:

```yaml
action: mobilealerts_cloud.migrate_from_legacy
data:
  dry_run: false
  cutover: false
```

This imports any missing sensors and copies **device names, areas, labels, entity names, icons, aliases, and Voice/Assist exposure** to `mobilealerts_cloud`, while leaving every legacy `mobile_alerts` entry and entity active.

### Step 3: Execute the Full Cutover (`dry_run: false`, `cutover: true`)

When you are ready to switch over permanently and preserve Recorder history:

```yaml
action: mobilealerts_cloud.migrate_from_legacy
data:
  dry_run: false
  cutover: true
```

**What happens atomically under the hood:**
1. Any legacy sensors not yet in `mobilealerts_cloud` are fetched from the API in a **single batch request**, added as Subentries, and the Hub reloads once cleanly from the 60-second cache. *(If any missing sensor fails API validation, its legacy entry is never removed.)*
2. All migrated legacy `mobile_alerts` config entries (plus any orphaned legacy YAML entities) are removed—freeing up their `entity_id`s while **keeping all historical Recorder data intact**.
3. Device metadata (`name_by_user`, `area_id`, `labels`) and entity metadata (`name`, `icon`, `area_id`, `aliases`, `labels`, user `disabled_by`/`hidden_by`, Assist/Google/Alexa exposure, explicit display precision) are applied.
4. All matching `sensor.*` entities in `mobilealerts_cloud` are renamed directly to their original `entity_id`s so dashboards, Utility Meters, and automations immediately point to `mobilealerts_cloud`.

### Step 4: Clean Up `name_prefix` & Update Domain-Changed References

1. If you used a temporary **Device name prefix** (such as `Test `) for parallel testing:
   - Go to **Settings → Devices & Services → Mobile Alerts Cloud → Configure (Hub Options)** and clear **Device name prefix**.
2. Review **`domain_changed_entities`** from the action output:
   - Update any dashboards, notifications, or automations that watched legacy string sensors (such as `sensor.*_battery == "Low"` or `"OK"`) to the new native binary sensors (e.g., `binary_sensor.*_battery` being `"on"` for low battery / `"off"` for OK).

### Step 5: Restart & Verify

1. Restart Home Assistant (**Settings → System → Restart**).
2. Open one or two key sensor charts (e.g. outdoor temperature or rain total): confirm the historical graph continues seamlessly across the cutover timestamp with zero gaps.
3. Check **Settings → System → Logs** to verify there are no errors or orphaned-entity warnings.
4. Once you are satisfied, remove the old `mobile_alerts` repository from **Settings → Marketplace** (or **HACS** on older HA versions) and restart Home Assistant once more to unload the old files from `/config/custom_components/mobile_alerts/`.

---

## 5. Selective Migration & Troubleshooting

### Migrating Specific Sensors Only

To test the cutover on a single device first before migrating the rest, pass **`device_ids`**:

```yaml
action: mobilealerts_cloud.migrate_from_legacy
data:
  dry_run: true
  cutover: true
  device_ids: "0100005B2E9D"
```

Once verified, run with `dry_run: false`, then repeat without `device_ids` for the remaining devices.

### Common Questions

- **Will I lose history if a sensor had a different friendly name in `mobile_alerts`?**
  No. History is keyed on `entity_id`, and both the old `entity_id` and your custom friendly name (`name`) are copied onto the `mobilealerts_cloud` entity.
- **I already had `mobile_alerts` entities in `configuration.yaml` (no UI ConfigEntry). Does migration work?**
  Yes! Legacy `mobile_alerts` entities in `core.entity_registry` are discovered automatically even without a UI config entry. Make sure to remove the old `mobile_alerts:` block from `configuration.yaml` before restarting Home Assistant in Step 5 so Home Assistant does not recreate legacy entities on boot.
- **I hit `rate_limited` during Step 3.**
  Wait ~1 minute (the Mobile Alerts cloud enforces max 3 requests/min per sensor) and run the action again. If your sensors were already added to `mobilealerts_cloud` beforehand, Step 3 needs **zero** REST API calls.
- **One of my `entity_id`s is listed under `conflicts`.**
  Another integration (neither `mobile_alerts` nor `mobilealerts_cloud`) currently owns that `entity_id`. Rename or remove that conflicting entity first if you want `mobilealerts_cloud` to take over that exact `entity_id`, then rename manually in the Entity settings.
