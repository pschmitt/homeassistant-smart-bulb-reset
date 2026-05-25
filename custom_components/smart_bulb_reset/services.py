"""Services for the Smart Bulb Reset integration."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_POWER_CYCLE_DELAY,
    DEFAULT_POWER_CYCLE_DELAY,
    DOMAIN,
    SERVICE_POWER_CYCLE,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = "device_id"

# entity_id and device_id are both optional; the handler validates that at
# least one resolves to a relay.  ALLOW_EXTRA lets area_id pass through
# without a schema error (we don't expand areas but we don't want to crash).
_POWER_CYCLE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENTITY_ID): vol.Any(cv.entity_id, [cv.entity_id]),
        vol.Optional(CONF_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(
            CONF_POWER_CYCLE_DELAY, default=DEFAULT_POWER_CYCLE_DELAY
        ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=60.0)),
    },
    extra=vol.ALLOW_EXTRA,
)


def _relay_for_light(hass: HomeAssistant, light_entity_id: str) -> str | None:
    """Return the relay entity_id paired with the given light, or None."""
    for data in hass.data.get(DOMAIN, {}).values():
        if isinstance(data, dict) and data.get("light") == light_entity_id:
            return data["relay"]
    return None


def _relays_for_device(hass: HomeAssistant, device_id: str) -> list[str]:
    """Return relay entity_ids for all paired light entities on device_id."""
    entity_reg = er.async_get(hass)
    relays: list[str] = []
    for entry in entity_reg.entities.values():
        if entry.device_id != device_id or entry.entity_id.split(".")[0] != "light":
            continue
        relay = _relay_for_light(hass, entry.entity_id)
        if relay and relay not in relays:
            relays.append(relay)
    return relays


async def _do_power_cycle(
    hass: HomeAssistant, relay_entity_id: str, delay: float
) -> None:
    _LOGGER.debug("Power-cycling %s (off for %.1f s)", relay_entity_id, delay)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": relay_entity_id}, blocking=True
    )
    await asyncio.sleep(delay)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": relay_entity_id}, blocking=True
    )


async def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE):
        return

    async def _handle_power_cycle(call: ServiceCall) -> None:
        delay: float = call.data.get(CONF_POWER_CYCLE_DELAY, DEFAULT_POWER_CYCLE_DELAY)
        relays: list[str] = []

        # entity_id — string (legacy YAML) or list (target picker)
        raw_eid = call.data.get(CONF_ENTITY_ID)
        if raw_eid:
            eids = [raw_eid] if isinstance(raw_eid, str) else list(raw_eid)
            for eid in eids:
                domain = eid.split(".")[0]
                if domain == "light":
                    relay = _relay_for_light(hass, eid)
                    if relay is None:
                        raise ServiceValidationError(
                            translation_domain=DOMAIN,
                            translation_key="no_relay_for_light",
                            translation_placeholders={"entity_id": eid},
                        )
                    relays.append(relay)
                elif domain == "switch":
                    relays.append(eid)
                else:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="unsupported_entity",
                        translation_placeholders={"entity_id": eid},
                    )

        # device_id — string or list (from target picker)
        raw_did = call.data.get(CONF_DEVICE_ID)
        if raw_did:
            dids = [raw_did] if isinstance(raw_did, str) else list(raw_did)
            for did in dids:
                found = _relays_for_device(hass, did)
                if not found:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="no_relay_for_device",
                        translation_placeholders={"device_id": did},
                    )
                relays.extend(found)

        if not relays:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_target_specified",
            )

        for relay in relays:
            await _do_power_cycle(hass, relay, delay)

    hass.services.async_register(
        DOMAIN,
        SERVICE_POWER_CYCLE,
        _handle_power_cycle,
        schema=_POWER_CYCLE_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove services when the last config entry is unloaded."""
    if hass.data.get(DOMAIN):
        return
    if hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE):
        hass.services.async_remove(DOMAIN, SERVICE_POWER_CYCLE)
