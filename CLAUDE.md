# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

HACS custom integration for Home Assistant exposing Tion devices (Breezer O2/3S/4S, MagicAir, Module CO2+) through the Tion / MagicAir cloud API (`api2.magicair.tion.ru`). `iot_class` is `cloud_polling`: every device command is sent through the cloud, there is no local command transport. The one nuance to "cloud only" is the optional **local PID controller** (see below) — it reads a *local* Home Assistant CO2 sensor entity and drives a breezer's speed, so the control loop runs locally even though commands still travel over the cloud. The shipped code lives under `custom_components/tion/`; the repo root is not a Python package.

## Common commands

There is no `pyproject.toml` / `requirements*.txt` in the repo — tooling assumes Home Assistant (and `pytest`, `ruff`) are already installed in the environment.

- Run tests: `pytest tests/`
- Run a single test: `pytest tests/test_pid.py::test_pid_maps_positive_error_to_speed`
- Lint / format: `ruff check .` and `ruff format .`. A local, untracked `ruff.toml` (devcontainer-specific, intentionally not committed) `extend`s Home Assistant core's `pyproject.toml`, so `ruff` here matches core's rules exactly. CI does not enforce lint.
- The two GitHub Actions that gate the integration are `hassfest` (Home Assistant manifest/structure check) and `hacs/action` (HACS metadata validation). Anything that breaks `manifest.json` or `hacs.json` will fail CI.
- Releases: bump `version` in `custom_components/tion/manifest.json` (currently `2026.6.3`) and the version badge in `README.md`. The Home Assistant minimum version (`2026.1`) is pinned in a separate README badge.

## Architecture

The integration is a fairly standard HA cloud-polling integration with a few quirks layered on top of the coordinator. The pieces that require reading multiple files together are:

### Cloud client (`client.py`)
- `TionClient` wraps the Tion REST API with OAuth-style password grant against `idsrv/oauth2/token`. The bearer token is cached on the client and persisted back into the config entry via the `update_auth_data` listener wired up in `__init__.py` — the `AUTH_DATA` key in `entry.data` is the source of truth across restarts.
- `_request` transparently re-authenticates on a single 401 (`retry_auth=True` recursion). Errors are normalized to three exception types — `TionAuthError`, `TionConnectionError`, `TionApiError` (all subclasses of `TionError`) — and the coordinator maps `TionAuthError` to `ConfigEntryAuthFailed` to trigger the reauth flow.
- Write operations (`send_breezer`, `send_zone`, `send_settings`) POST to the API which returns a `task_id`; `_wait_for_task` polls `task/{id}` every 500 ms up to 5 s waiting for `status == "completed"`. The whole command path is therefore async and may take seconds.
- The API response shape is mirrored by plain wrapper classes (`TionLocation` → `TionZone` → `TionZoneDevice` → `TionZoneDeviceData`). These are constructed defensively with `.get()`; new fields just need to be added to `TionZoneDeviceData.__init__`.

### Coordinator (`coordinator.py`) — stale-data guard + PID hook
`TionDataUpdateCoordinator` extends `DataUpdateCoordinator[TionData]`. Two behaviours here are easy to miss and must be preserved when changing the update path:

- **Data shape.** The coordinator data is a `TionData` dataclass wrapping `locations`, with `devices()` / `device(guid)` / `zone(guid)` lookup helpers. The coordinator's `get_devices` / `get_device` / `get_device_zone` and the PID manager both go through these instead of walking the location→zone→device tree themselves.
- **Stale-poll filter.** `_current_command_started_at` is set while a command is in flight; `_last_command_completed_at` records when the last command finished. In `_async_update_data`, if a refresh started before the most recent command completed, the new server snapshot is **discarded** and the previous `self.data` is returned. This is necessary because the Tion cloud frequently returns the *pre-command* state for several seconds after a task completes, so a naive poll would visibly bounce the UI back.
- **Reconcile hook.** After a fresh (non-stale) snapshot is built the coordinator runs the desired-state convergence: if `pid_manager.has_active_pid()` it calls `pid_manager.write_all(data)` (PID writes its desired fields), then `reconciler.reconcile(data)` drives cloud state toward *all* desired state (PID + manual writers). Both run *inside* the coordinator update cycle — there is no separate timer. See "Desired-state reconciler" below.
- All cloud sends go through `_async_send_command`, which takes `track_stale` and `request_refresh` flags. The reconciler sends with `track_stale=False, request_refresh=False` so its background commands neither trip the stale-guard nor recurse into a refresh from within the update cycle. New mutating helpers must use this wrapper rather than calling `self.client.send_*` directly. Settings writes (backlight/sound) are serialized per device by `async_settings_command`; the old per-device breezer/zone command locks were removed once every writer moved onto the reconciler.
- `self.pid_manager` is assigned by `__init__.py` immediately after construction (typed but not set in `__init__`).

### Desired-state reconciler (`desired_state.py`, `reconciler.py`)
The single path by which every breezer/zone command reaches the cloud. Writers never build a full payload or send directly — they declare intent, and a background reconciler converges the cloud to it idempotently. This replaced the old "every writer does read-modify-send under a lock" model.
- `desired_state.py`: `DesiredBreezer` / `DesiredZone` are sparse frozen value objects holding only the fields a writer explicitly set. `merge(reported)` overlays the desired fields on the last reported snapshot to build the full `send_*` payload — so a `None` value is distinct from an absent key, and unset fields are taken from reported.
- `reconciler.py`: one `TionReconciler` per coordinator holds `_breezers` / `_zones` desired overlays, a `_confirmed` set, and an `_inflight` set. Writers call `set_breezer(guid, fields)` / `set_zone(guid, fields)` (merge fields, un-confirm them). Read/clear helpers: `current_breezer` / `current_zone` (copies, used for restore persistence), `holds(guid, fields)` (are all fields still desired?), `release` / `release_zone`.
- `reconcile(data)` runs every coordinator cycle and immediately after a writer's `_push`: per breezer it resolves the breezer payload and its zone precondition in one background task (zone before breezer). `_detect` is per-field — a field that reached its target is *confirmed*; a confirmed field that later diverges is treated as an **external change** and dropped from the overlay; an unconfirmed divergent field is (re)sent. Sends are fire-and-forget background tasks owned by the config entry (`async_create_background_task`) and deduplicated per guid by `_inflight`; this `_inflight` serialization is what replaced the command locks. `reconcile` also applies the resolved breezer payload onto the in-memory `data` so the UI reflects the change optimistically before the trailing refresh.

### Local PID controller (`pid.py`, `pid_manager.py`)
Optional per-breezer feature: drive a breezer's speed from a local Home Assistant CO2 sensor instead of the Tion cloud's own auto mode.
- `pid.py` is **pure logic with no Home Assistant dependencies** (so it can be unit-tested in isolation): `PidController` plus the `PidCoefficients` / `PidState` / `PidOutput` dataclasses. `calculate()` maps the CO2 error (`source - target`) to a speed within `[speed_min, min(speed_max, device_max_speed)]`, with anti-windup (the integral term is clamped to `[0, 100]`).
- `pid_manager.py` is the runtime: one `TionPidManager` per config entry, one private `_TionBreezerPidController` per breezer. Per-breezer config lives in `entry.options[CONF_PID_BREEZERS][guid]` (`CONF_PID_ENABLED`, `CONF_CO2_SENSOR_ENTITY_ID`, `CONF_PID_KP/KI/KD`, `CONF_PID_BASE_OUTPUT`).
- `write_all(data)` calls each controller's `evaluate(data)` from the coordinator cycle. `evaluate` reads the local CO2 sensor, pauses with one of the `PID_STATUS_*` constants when the sensor/device is unavailable or the device data is invalid, and otherwise **writes its desired state into the reconciler**: `set_breezer(guid, {is_on, speed})` (PID owns only is_on/speed; off ⇒ speed 0) and `set_zone(zone, {mode: MANUAL, co2})`. It no longer builds or sends a breezer payload — the reconciler decides what to send and applies the optimistic update. Because PID re-writes its fields every cycle they stay "owned" by PID (re-confirmed), so a manual/preset field released on external change stays released while PID keeps driving speed.
- Runtime state is surfaced to the owning climate entity as extra state attributes (`pid_active`, `pid_source_co2`, `pid_error`, `pid_output_speed`, `pid_status`, `pid_last_update`, ...).
- Lifecycle: created in `async_setup_entry`; `async_start()` returns the unload callback (`async_stop`) that disarms every controller.

### Speed presets (`presets.py`)
Optional per-breezer named speed presets exposed as the climate entity's `preset_mode`.
- Polymorphic `Preset` hierarchy (frozen dataclass `ABC`): `ManualPreset(speed)` overlays `{is_on: True, speed}` and is not auto; `AutoPreset(min_speed, max_speed)` overlays `{speed_min_set, speed_max_set}` and runs in auto. Each exposes `desired_fields()` and `is_auto()`; `from_config` / `from_storage` / `to_storage` are the (de)serializers. There is no `apply` / `snapshot` / `PresetTarget` anymore.
- A preset applies by **writing its `desired_fields()` into the reconciler** (plus arming/disarming local PID or cloud auto), synchronously and with no rollback.
- `PresetBaseline(overrides, was_auto)` captures the desired overlay (restricted to preset-managed fields) plus the auto-vs-manual mode in effect *before* the preset — taken from the desired overlay, never a live snapshot — so returning to `PRESET_NONE` restores both, and a cancelled-then-retried apply cannot pollute it.
- `TionPresetController` is pure logic for one breezer: `managed_fields` (union of every preset's desired field keys), `active_preset()`, `saved`, `activate(name, baseline)` (stashes the baseline only on the first activation from `PRESET_NONE`), `deactivate()`, and `restore` / `restore_attributes` for surviving a restart (`ATTR_SAVED_PRESET`).
- Presets are stored in `entry.options[CONF_PRESETS][guid]`. Exit-from-preset on external change is decided by the climate entity via `reconciler.holds(...)` (the managed fields were dropped from the overlay), not by a snapshot value comparison.

### Entry setup (`__init__.py`)
`async_setup_entry` creates the client, builds the coordinator, builds the `TionPidManager` and assigns it to `coordinator.pid_manager`, runs `async_config_entry_first_refresh()` (so platform setup sees populated data), registers `pid_manager.async_start()` via `entry.async_on_unload`, then registers a device-registry entry per known device, wires the options-update listener (which reloads the entry on change), and finally forwards to `PLATFORMS`. Only device types in `MODELS_SUPPORTED` (defined in `const.py`) get registered; unknown types are logged and skipped. Adding support for a new device model requires extending both `TionDeviceType` and `MODELS_SUPPORTED`. (Note: `TionDeviceType.CLEVER` exists but is intentionally absent from `MODELS_SUPPORTED`, so it is not registered.)

### Platforms
Each file in `custom_components/tion/{binary_sensor,button,climate,number,sensor,switch}.py` is a HA platform module exposing `async_setup_entry`. All entities subclass `CoordinatorEntity[TionDataUpdateCoordinator]` and use `coordinator.get_device(guid)` for fresh state. Breezer/zone writers (climate commands + presets, switch auto-mode/heater, number target-CO2/min/max speed) **write desired state via `coordinator.reconciler.set_breezer/set_zone` and call a local `_push`** (immediate `reconcile` + optimistic reload + `async_request_refresh`) — they do not call `coordinator.async_send_*` directly. The exceptions are the settings switches (backlight/sound), which POST settings under `async_settings_command`, and `TionLocalTargetCO2`, which only updates the local PID target.

`climate.py` is by far the largest and carries the per-model branching for Breezer O2 vs 3S vs 4S (heater modes, swing/gate, speed limits). Behaviour differences between breezer models are not abstracted — they're conditional on `self._type` against `TionDeviceType` values, so adding a model means touching the climate branches as well. Beyond the per-model logic, the breezer climate entity also:
- is a `RestoreEntity`;
- exposes `preset_mode` / `preset_modes` via a `TionPresetController` (the `ClimateEntityFeature.PRESET_MODE` flag is only set when presets are configured); applying a preset is synchronous — capture the baseline and write the desired fields before any `await`, so a restart-mode cancel/retry can't pollute the saved baseline;
- on every coordinator update, drops an active preset when `reconciler.holds(...)` reports its managed fields were released by an external change;
- persists the breezer/zone desired overlays (`desired_breezer` / `desired_zone`) and the preset (`ATTR_SAVED_PRESET` + preset_mode) in `extra_state_attributes`, and on startup restores them into the reconciler / preset controller plus the active local PID state from `last_state`;
- treats Auto specially: if the breezer has a local PID configured, selecting Auto starts the PID (the zone goes MANUAL); otherwise it falls back to the cloud's auto mode. PID runtime attributes are merged into `extra_state_attributes`.

### Config flow (`config_flow.py`)
- Unique ID is `sha256(username)` — keep this stable so existing installs don't get duplicated entries.
- Username/password validation goes through `TionClient.async_validate_auth`, which both fetches a token and pulls locations (catches accounts where auth succeeds but the user has no devices).
- `async_step_reauth_confirm` reuses the original username and asks only for the new password.
- The options flow is **multi-step**, dispatched from `async_step_init` (which sets `CONF_SCAN_INTERVAL`, min 10 s / default `DEFAULT_SCAN_INTERVAL` = 60 s, and asks what to configure next):
  - **Local PID branch:** `async_step_local_pid` picks the breezer, then `async_step_breezer` edits that breezer's PID config (CO2 sensor entity, enabled flag, `base_output`, `kp`, `ki`, `kd`).
  - **Presets branch:** `async_step_presets` picks the breezer, then `async_step_preset_add` (name + type) and `async_step_preset_config` (speed for manual, min/max for auto).

### Translations
UI strings live in `custom_components/tion/translations/` (Russian `ru.json` and English `en.json`). New entities need their `translation_key` in both files, and the multi-step options flow (PID + presets) strings live here too — missing keys show up as raw keys in the UI.

## Branch context

- `master` is the released line (`origin/HEAD`). Active development happens in feature branches — `presets`, `local-pid`, `breezerO2`, `api-v2`, `dev` — that merge in as they land.
- The **speed presets**, **local CO2 PID**, and the **desired-state reconciler** are implemented and tested; they are not WIP. `tests/` is tracked in git: `tests/test_presets.py`, `tests/test_pid.py`, `tests/test_pid_manager.py`, `tests/test_climate.py`, `tests/test_config_flow.py`, `tests/test_coordinator.py`, `tests/test_desired_state.py`, `tests/test_reconciler.py`, `tests/test_switch.py`, `tests/test_number.py`, `tests/test_client.py`.
