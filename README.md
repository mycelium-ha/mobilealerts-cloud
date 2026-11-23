# Mobile Alerts Cloud

[![HA Marketplace](https://img.shields.io/badge/Home%20Assistant-Marketplace-41BDF5.svg?style=for-the-badge)](https://contributors.home-assistant.io/marketplace/)
[![Release](https://img.shields.io/github/v/release/mycelium-ha/mobilealerts-cloud?style=for-the-badge)](https://github.com/mycelium-ha/mobilealerts-cloud/releases)
[![Validate & Test](https://github.com/mycelium-ha/mobilealerts-cloud/actions/workflows/validate.yml/badge.svg?style=for-the-badge)](https://github.com/mycelium-ha/mobilealerts-cloud/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

**Bring your Technoline Mobile Alerts and TFA Dostmann WeatherHub sensors into Home Assistant**: temperature, humidity, rain, wind, water leaks, door contacts and wireless buttons, set up entirely in the UI.

Mobile Alerts Cloud (`mobilealerts_cloud`) is built on the official [Mobile Alerts REST API](https://mobile-alerts.eu/info/public_server_api_documentation.pdf) and follows the Home Assistant [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) ([rule status](custom_components/mobilealerts_cloud/quality_scale.yaml)). It is part of [Mycelium HA](https://github.com/mycelium-ha).

---

## Highlights

**Set up in minutes**
- Add a hub, enter your sensor IDs, done. Options, reconfiguration, and adding or removing sensors work without a restart.
- Native entities with proper device and state classes: sensors, binary sensors for battery, water, contact, mains power and smoke, and event entities for the wireless buttons.
- Translated into 7 languages, including entity names, errors and repair issues.

**Reliable by design**
- Uses only the official API, with one batched request per poll for all sensors of a hub.
- Respects the API limits automatically, so the service doesn't block your sensors or your IP address: at most 3 requests per sensor per minute, every ID checked locally first, and automatic waiting if the service asks for a pause.
- Shows a sensor as unavailable once it stops reporting, instead of silently keeping an old value, and raises a repair issue when a sensor disappears for a day.
- Tested against real Home Assistant: the minimum supported release, the current release and, every week, the next beta.

**Easy to switch to**
- Already using the `mobile_alerts` integration? Mobile Alerts Cloud runs next to it without conflicts, imports your sensors during setup, and one action takes over names, areas and history. See [Switching from `mobile_alerts`](#switching-from-mobile_alerts).

**Safe to share**
- Diagnostics replace every sensor ID with an alias, so you can attach them to an issue without exposing your sensors.

---

## Prerequisites

- **Home Assistant `2026.9.0` or newer.**
- One or more Mobile Alerts / TFA WeatherHub sensors connected to your Mobile Alerts Gateway, identified by their **12-character hexadecimal Sensor ID** (printed on the sensor housing or visible in the Mobile Alerts app).
- Internet access from Home Assistant to `https://www.data199.com/api/pv1/device/lastmeasurement`.

---

## Installation

### Via Home Assistant Marketplace (HA 2026.11+, formerly HACS)

Starting with **Home Assistant 2026.11**, HACS is integrated directly into Home Assistant Core under **Settings → Marketplace**. Adding custom repositories requires a connected GitHub account in Marketplace settings.

1. Open **Settings → Marketplace** and connect your GitHub account if not already linked.
2. Open the menu (**⋮**) → **Custom repositories**.
3. Add `https://github.com/mycelium-ha/mobilealerts-cloud` with category **Integration**.
4. Install **Mobile Alerts Cloud** and restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for **Mobile Alerts Cloud**.

### Via HACS (Home Assistant 2026.9–2026.10)

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/mycelium-ha/mobilealerts-cloud` with category **Integration**.
3. Install **Mobile Alerts Cloud** and restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **Mobile Alerts Cloud**.

### Manual Installation
Copy `custom_components/mobilealerts_cloud` into your Home Assistant `<config>/custom_components/mobilealerts_cloud` directory and restart Home Assistant.

---

## Configuration

Mobile Alerts Cloud has one **hub** per Home Assistant installation (you can add more, for example to poll with different intervals) and one **sensor** entry under the hub for every Mobile Alerts sensor. A sensor ID can only belong to one hub.

### Setting up the hub

[![Open your Home Assistant instance and start setting up Mobile Alerts Cloud.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=mobilealerts_cloud)

Or add **Mobile Alerts Cloud** under **Settings → Devices & Services → Add Integration**. The setup form has these fields:

| Field | Default | What it does |
|---|---|---|
| **Hub name** | `Mobile Alerts Cloud` | Name of the hub entry and its service device. |
| **Import sensors from legacy mobile_alerts** | on | Only shown when the `mobile_alerts` integration has sensors that are not on a hub yet. Adds them as sensors of this hub, with their existing names, and leaves the `mobile_alerts` entries untouched. |
| **Initial sensor IDs** | empty | Comma- or newline-separated 12-character sensor IDs to add right away. Leave it empty to add sensors one by one later. |
| **Polling interval (minutes)** | `10` | How often the hub asks the API for new values, from 1 to 1440. See [How data is updated](#how-data-is-updated). |
| **Device name prefix** | empty | Text put in front of every sensor device name, for example `Test ` while you test next to the `mobile_alerts` integration. |
| **Phone ID** | empty | The numeric Phone ID of your Mobile Alerts app (6 to 20 digits). Only used when the next field is on. |
| **Send Phone ID with requests** | off | Sends the Phone ID with every request. The API answers with HTTP 400 when a queried sensor is not linked to that Phone ID; the hub then retries without it and stops sending it until the hub reloads. Leave this off unless you know you need it (see [Known limitations](#known-limitations)). |

Before the hub is created, every sensor ID is checked locally and then with one API request, so a typo is caught before anything is saved.

### Hub options

**Configure** on the hub changes these settings. The hub reloads when you save.

| Option | Default | What it does |
|---|---|---|
| **Polling interval (minutes)** | `10` | As in the setup form. The stale threshold follows it, and the rain gauge has its own longer threshold (see [Availability](#how-data-is-updated)). |
| **Device name prefix** | empty | As in the setup form. Applies to all sensor devices of the hub. |

**Reconfigure** on the hub changes the **Phone ID** and **Send Phone ID with requests** settings.

### Adding, editing and removing sensors

- **Add sensor** on the hub's integration page asks for the **Sensor ID**, an optional **Name** and the **Hardware profile**. The sensor is checked with one API request before it is added. The default name is the model code plus the last four characters of the ID, for example `MA 10650 ABCD`.
- **Hardware profile** stays on *Automatic* in almost every case: the first two characters of the ID select the profile. Choose another profile only when the automatic one is wrong, for example `ID04_CABLE` for an `ID04` sensor with a cable temperature probe.
- **Edit sensor** (reconfigure) on a sensor entry changes its name and hardware profile.
- **Delete** on a sensor entry removes the sensor, its device and its entities. The other sensors of the hub are not affected.

---

## How data is updated

Mobile Alerts sensors send their values by radio to the Mobile Alerts gateway, which uploads them to the Mobile Alerts cloud. Mobile Alerts Cloud never talks to the gateway; it reads the latest values from the official cloud API.

- **Polling:** every polling interval (10 minutes by default) the hub sends **one** request for all of its sensors. Most sensors transmit about every 7 minutes, so a shorter interval rarely gives fresher values and a longer one is fine for slow-changing values such as temperature. The rain gauge transmits about every 2 hours.
- **Force refresh:** the `mobilealerts_cloud.force_refresh` action polls right away, for example from an automation.
- **Rate limits:** the API allows 3 requests per sensor per minute and blocks a sensor for 7 minutes when this is exceeded (HTTP 429). More than 5 invalid requests from one IP address in 15 minutes block the address for 15 minutes (HTTP 403). The integration counts every request, from polls, setup, actions and the migration, against these limits, refuses requests that would break them and waits out a block. While a poll is rate limited, entities keep their last values and one warning is logged.
- **Reloads:** a hub reload within 60 seconds of the last poll reuses the last response instead of asking the API again.
- **Availability:** each reading carries the time the sensor last reported (`lastseen`). When that is older than 60 minutes, or three polling intervals if that is longer, the sensor's entities become `unavailable` until it reports again; its `last_seen` entity stays available. The rain gauge (MA 10650) transmits about every 2 hours, so its threshold is at least 5 hours, or three polling intervals if that is longer. If the API does not return a sensor at all for 24 hours, a repair issue appears.

---

## Use cases

- **Room climate:** temperature and humidity in every room, for dashboards, heating automations or a mould warning when humidity stays high.
- **Weather station:** outdoor temperature, rain totals (with daily and monthly utility meters) and wind speed, gusts and direction.
- **Leak and window monitoring:** the water detector of the MA 10350 and the door and window contact MA 10800 as binary sensors for alerts.
- **Wireless buttons:** the four buttons of the MA 10880 as `event` entities that trigger scenes or lights.
- **Battery and outage alerts:** a low-battery binary sensor on every sensor, and the `unavailable` state when a sensor stops reporting.
- **Freezer and pool:** cable and pool temperature probes for freezers, aquariums and pools.

---

## Switching from `mobile_alerts`

Mobile Alerts Cloud uses its own domain, device identifiers, unique IDs, events and actions, so it can run next to the `mobile_alerts` integration without interfering with it. You can test it side by side first and switch over when you are ready.

1. **Import your sensors:** when you set up the hub, the sensors of `mobile_alerts` are offered for import (see [Setting up the hub](#setting-up-the-hub)). A **Device name prefix** such as `Test ` keeps both sets of devices apart while you compare.
2. **Preview the takeover:** run `mobilealerts_cloud.migrate_from_legacy` with `dry_run: true` and `cutover: true`. It changes nothing and makes no API request.
3. **Copy names and settings (optional):** run it with `dry_run: false` and `cutover: false` to copy device names, areas, labels, entity names, icons, aliases and Assist exposure while both integrations keep running.
4. **Switch over:** run it with `dry_run: false` and `cutover: true`. The `mobile_alerts` entries are removed and the new sensor entities take over the old entity IDs, so history, statistics, dashboards, automations and utility meters continue without a gap.

The [migration guide](docs/MIGRATION.md) explains every step, including the channels that become binary sensors or events and therefore start a new history.

---

## Supported Hardware Prefixes (`ID01`–`ID20`)

Every device also exposes diagnostic **`battery`** (`binary_sensor`, `BATTERY`) and **`last_seen`** (`sensor`, `TIMESTAMP`) entities.

**Availability:** a sensor's entities become `unavailable` when it has not reported for more than 60 minutes, or three poll intervals if that is longer (for example 90 minutes at a 30 minute interval). The rain gauge (MA 10650) has a threshold of at least 5 hours, or three poll intervals if that is longer, because it transmits about every 2 hours. `last_seen` stays available, so you can see when the sensor last reported. If the API stops returning a sensor for more than 24 hours, a repair issue in **Settings → System → Repairs** points to it.

**Disabled by default** on new installations: `rr` rain last increment, `rf` rain flip counter and the `kp1c`–`kp4c` press counters (use the button events instead). Enable them on the entity page if you need them.

| Prefix | Model(s) | Exposed Channels & Platforms |
|---|---|---|
| `01` (`ID01`) | **MA 10101** | `t1` Temperature, `t2` Cable temperature (`sensor`) |
| `02` (`ID02`) | **MA 10100** | `t1` Temperature (`sensor`) |
| `03` (`ID03`) | **MA 10200 / TFA 30.3303.02** | `t1` Temperature, `h` Humidity (`sensor`) |
| `04` (`ID04`) | **MA 10350** | `t1` Temperature, `h` Humidity (`sensor`), `water` Moisture (`binary_sensor`; or `ID04_CABLE` override for `t2` Cable temp). *The API spec documents `t2` as a cable temperature; the MA 10350 ships with a water detector, so the integration reads `t2` as water by default. Use the `ID04_CABLE` profile if your unit has a cable probe.* |
| `05` (`ID05`) | **MA 10402** | `t1` Temperature, `t2` External temperature, `h` Humidity, `ppm` CO₂ (`sensor`) |
| `06` (`ID06`) | **MA 10700** | `t1` Temperature, `t2` Pool temperature, `h` Humidity (`sensor`) |
| `07` (`ID07`) | **MA 10410** | `t1` Indoor temp, `t2` External temp, `h` Humidity 1, `h2` Humidity 2 (`sensor`) |
| `08` (`ID08`) | **MA 10650** | `t1` Temperature, `r` Rain total (mm), `rf` Rain flip counter, `rr` Rain last increment (`sensor`, both disabled by default) |
| `09` (`ID09`) | **MA 10300 / MA 10320** | `t1` Temperature, `t2` Cable temperature, `h` Humidity (`sensor`) |
| `0A` (`ID0A`) | **MA 10860** | `t1` Temperature (`sensor`), `smoke_1`–`smoke_4` (`binary_sensor`, `SMOKE`) |
| `0B` (`ID0B`) | **MA 10660** | `ws` Wind speed, `wg` Wind gust, `wd` Wind direction (`ENUM`), `wd_degrees` Direction (°, `WIND_DIRECTION`) (`sensor`) |
| `0E` (`ID0E`) | **MA 10241 / MA 10250PRO** | `t1` Temperature, `h` Humidity (`sensor`) |
| `0F` (`ID0F`) | **MA 10101 PRO** | `t1` Temperature, `t2` Cable temperature (`sensor`) |
| `10` (`ID10`) | **MA 10800** | `contact` Window/Door contact (`binary_sensor`, `OPENING`) |
| `11` (`ID11`) | **TFA 30.3060.01 (KLIMA@HOME)** | `t1`–`t4` Temperatures, `h1`–`h4` Humidities (`sensor`) |
| `12` (`ID12`) | **MA 10230** | `t1` Temperature, `h` Humidity, `h3havg`, `h24havg`, `h7davg`, `h30davg` (`sensor`) |
| `15` (`ID15`) | **MA 10880** | `button_1`–`button_4` (`event`: `short_press`/`double_press`/`long_press`), `kp1c`–`kp4c` Counters (`sensor`, disabled by default) |
| `17` (`ID17`) | **MA 10870** | `t1` Temperature (`sensor`), `ac_power` AC mains (`binary_sensor`, `POWER`, inverted `t2 == 0`) |
| `18` (`ID18`) | **MA 10238** | `t1` Temperature, `h` Humidity, `ap` Air pressure (hPa) (`sensor`) |
| `20` (`ID20`) | **MA 10120** | `t1` Temperature (`sensor`) |
| *Other* | Dynamic Fallback | Unknown prefixes automatically map any recognized measurement keys |

---

## Examples

Entity IDs below follow the default device names (model code plus the last four characters of the sensor ID, here `ABCD`). Look up your own entity IDs under **Settings → Devices & Services → Entities**.

### Daily and monthly rainfall (MA 10650)

The rain total (`r`) is a cumulative value in mm with the `total_increasing` state class. Utility meters turn it into daily and monthly amounts:

```yaml
utility_meter:
  rain_daily:
    source: sensor.ma_10650_abcd_rain_total
    cycle: daily
  rain_monthly:
    source: sensor.ma_10650_abcd_rain_total
    cycle: monthly
```

### Toggle a light with a button of the MA 10880

```yaml
alias: "MA 10880 button 1 short press"
triggers:
  - trigger: state
    entity_id: event.ma_10880_abcd_button_1
    not_from: [unknown, unavailable]
    not_to: [unknown, unavailable]
conditions:
  - condition: state
    entity_id: event.ma_10880_abcd_button_1
    attribute: event_type
    state: short_press
actions:
  - action: light.toggle
    target:
      entity_id: light.living_room
```

The same press also fires the `mobilealerts_cloud_key_press` event with `device_id`, `channel` (`button_1` to `button_4`), `press_type` (`short_press`, `double_press`, `long_press`) and `count`.

### Notify when a battery is low

```yaml
alias: "Mobile Alerts battery low"
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.ma_10650_abcd_battery
      - binary_sensor.ma_10350_abcd_battery
    to: "on"
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.to_state.name }} needs a new battery."
```

### Notify when a sensor stops reporting

Use a sensor that transmits about every 7 minutes. The example entity `sensor.ma_10101_abcd_temperature_1` assumes the default device name of an MA 10101 and English as the Home Assistant system language; otherwise check the entity ID under **Settings → Devices & services**. The rain gauge (MA 10650) becomes `unavailable` only after at least 5 hours without a report, so do not use the same trigger for it.

```yaml
alias: "Mobile Alerts sensor silent"
triggers:
  - trigger: state
    entity_id: sensor.ma_10101_abcd_temperature_1
    to: unavailable
    for: "00:10:00"
actions:
  - action: notify.notify
    data:
      message: "The sensor has not reported for over an hour."
```

### Water leak alert (MA 10350)

```yaml
alias: "Water detected in the basement"
triggers:
  - trigger: state
    entity_id: binary_sensor.ma_10350_abcd_water_detected
    to: "on"
actions:
  - action: notify.notify
    data:
      message: "Water detected in the basement!"
```

---

## Actions

All actions select a hub with the **`config_entry_id`** field (the hub picker in the UI).

### `mobilealerts_cloud.force_refresh`

Polls the API right away, within the rate limits. The action fails with an error when the poll gets no new data, for example during a rate-limit block; the entities then keep their last values.

| Field | Required | Description |
|---|---|---|
| `config_entry_id` | no | Hub to refresh. Without it, all loaded hubs are refreshed. |

### `mobilealerts_cloud.dump_raw_response`

Returns the raw API readings of each hub as an action response, together with the rate-limit status. The response contains the full sensor IDs, so use it for local debugging only and never post it publicly; use diagnostics for support requests.

| Field | Required | Default | Description |
|---|---|---|---|
| `config_entry_id` | no | | Hub to dump. Without it, all loaded hubs are dumped. |
| `refresh` | no | `false` | Poll the API first instead of returning the last readings. Fails like `force_refresh` when the poll gets no new data. |

### `mobilealerts_cloud.migrate_from_legacy`

Takes over sensors, names, areas, entity settings and, with cutover, entity IDs and history from the `mobile_alerts` integration. Returns the migration plan or result as an action response. See the [migration guide](docs/MIGRATION.md).

| Field | Required | Default | Description |
|---|---|---|---|
| `config_entry_id` | when more than one hub is loaded | | Hub that receives the sensors. |
| `dry_run` | no | `true` | Only show the plan; change nothing. |
| `cutover` | no | `false` | Remove the `mobile_alerts` entries and move their entity IDs, and with them the history, to the new entities. Without it, only names, areas and settings are copied. |
| `device_ids` | no | all | Comma- or newline-separated sensor IDs to migrate. |

### Diagnostics

**Settings → Devices & Services → Mobile Alerts Cloud → ⋮ → Download diagnostics** (or the same menu on a sensor device) downloads the configuration, the resolved profiles, the rate-limit status and the last raw readings. Phone IDs are removed and every sensor ID is replaced by an alias such as `sensor_1`, so the file is safe to attach to an issue.

---

## Known limitations

- **Cloud only.** Values come from the Mobile Alerts cloud. Without internet access, or when the cloud is down, there are no new values; there is no local access to the gateway.
- **Delayed values.** A value reaches Home Assistant after the sensor's next transmission, the gateway upload and the next poll, typically within one polling interval plus about 7 minutes; for the rain gauge, plus up to about 2 hours.
- **Button presses between polls.** The API only reports the press counter and the type of the last press. Several presses between two polls show up as one event with the type of the last press, and a press is only seen at the next poll.
- **Alerts from the app.** Alert settings and alarm flags of the Mobile Alerts app are not used. The API only returns alarm flags with a Phone ID, and it is not yet confirmed whether the smoke detector states of the MA 10860 (`ID0A`) arrive without one.
- **MA 10350 (`ID04`).** The API documentation describes `t2` as a cable temperature, while the MA 10350 has a water detector. The integration reads it as water; choose the `ID04_CABLE` profile if your unit has a cable probe.
- **Unknown sensor types.** Sensors with an unknown ID prefix get entities for the measurement keys the integration recognises; other values are ignored.
- **Number of sensors.** The API documents no maximum number of sensors per request. Very large hubs are untested.

---

## Troubleshooting

- **"Sensor ID was not found" when adding a sensor:** check the 12-character ID on the sensor or in the Mobile Alerts app, and make sure the sensor is registered on a gateway and has reported at least once.
- **"Rate limit reached" or "IP blocked":** wait 7 or 15 minutes and do not retry in the meantime; every retry can extend a block. Avoid very short polling intervals together with frequent `force_refresh` calls.
- **Entities are `unavailable`:** look at the sensor's `last_seen` entity. If it is old, the sensor has not reported: check its battery, the radio range to the gateway and whether the gateway is online in the Mobile Alerts app.
- **A repair issue says a sensor is missing:** the API has not returned the sensor for 24 hours. Check the sensor and gateway, or delete the sensor entry if you no longer own it.
- **Wrong entities for a sensor:** check the hardware profile under **Edit sensor**, and open an issue with the diagnostics download if the automatic profile is wrong.
- **Debug logging:** enable debug logging on the integration page (**⋮ → Enable debug logging**), reproduce the problem, then disable it again to download the log. Or add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.mobilealerts_cloud: debug
  ```

---

## Removal

1. Go to **Settings → Devices & Services → Mobile Alerts Cloud**.
2. Open the menu (**⋮**) of the hub and choose **Delete**. This removes the hub, all its sensors, devices and entities. Repeat for every hub.
3. To remove the files as well, uninstall **Mobile Alerts Cloud** in the Marketplace (or HACS), or delete `custom_components/mobilealerts_cloud`, then restart Home Assistant.

Removing Mobile Alerts Cloud does not touch the `mobile_alerts` integration. After a switch-over with cutover, the `mobile_alerts` entries are already gone; set that integration up again if you want to go back.

---

## Documentation

- [Migration guide](docs/MIGRATION.md) — switching from the `mobile_alerts` integration with names, areas, settings and history.
- [Changelog](CHANGELOG.md) — Release history and implemented features.
- [Contributing](CONTRIBUTING.md) — Local development setup, testing, and architecture guidelines.
- [HA Integration Standards](docs/HA_INTEGRATION_STANDARDS.md) — Architectural patterns and packaging rules.
- [Design decisions](docs/DECISIONS.md) and [API spec notes](docs/API_SPEC_NOTES.md).

---

## Development & Testing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and the quality gate (`ruff`, `mypy --strict`, `pytest` with a 95 % coverage gate, translation check).

A standalone mock of the REST API covering all sensor prefixes is included at [tests/mock_api_server.py](tests/mock_api_server.py):

```bash
python3 tests/mock_api_server.py --port 8888
```

The tests point the client at it (`point_client_at` in `tests/conftest.py`); the integration itself always talks to the official API endpoint.

---

<sub>Mobile Alerts Cloud is an independent community project of [Mycelium HA](https://github.com/mycelium-ha). It is not affiliated with Technoline, TFA Dostmann, Mobile Alerts, Home Assistant, the Open Home Foundation or Nabu Casa.</sub>
