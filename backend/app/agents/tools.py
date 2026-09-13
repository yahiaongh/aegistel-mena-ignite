# app/agents/tools.py
"""Nokia NaC (Network-as-Code) integration — SDK-first per-signal provenance.

The eight CAMARA tools run through Nokia's Network-as-Code SDK (RapidAPI) and
every tool follows the same explicit ladder, labeling the result with its actual
source. The SDK client is **lazily initialized** at the first tool call
(`_get_nac_client()`), so the SDK tier is always genuinely attempted, even on
the very first audit:

    1. Nokia NaC SDK
    2. CAMARA REST passthrough (Nokia NaC REST API)
    3. Documented local sandbox simulator (Nokia CAMARA Sandbox / LOCAL FALLBACK)

Why a signal lands on a fallback — observed, not theoretical:

- A NaC capability on the provisioned key is entitled but OAuth-gated (Number
  Verification's v2 flow needs an `Authorization` header from the SDK consent
  flow; without it the endpoint returns ~401), so that tool degrades through
  SDK → REST to the documented sandbox semantics.
- An arbitrary (non-demo) E.164 has no simulator behavior, so Number Verification
  reports honest UNKNOWN instead of "verified".
- The Nokia host is transiently unreachable, in which case each tool rides the
  same SDK → REST → sandbox ladder automatically and no signal is ever silently
  assumed.

Therefore the defensible product claim is "Nokia NaC integration with per-signal
source evidence", never "eight unconditional live carrier checks" — the Evidence
Explorer renders the per-signal source badge for exactly that reason."""
import json
import os
import sys
from functools import wraps
from typing import Any, Dict
from urllib.parse import urlsplit

import requests


class _LocalTool:
    """Small CrewAI-compatible callable wrapper for direct CAMARA execution.

    The orchestrator invokes these functions itself; it does not hand tool
    objects to CrewAI. Keeping this adapter local prevents deterministic audits
    from importing CrewAI's complete LLM/auth stack just to attach `.run`.
    """

    def __init__(self, func):
        self._func = func
        wraps(func)(self)

    def run(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)


def tool(func):
    return _LocalTool(func)

NOKIA_NAC_HOST = os.getenv("NOKIA_NAC_HOST", "network-as-code.nokia.rapidapi.com")
NOKIA_BASE_URL = os.getenv(
    "NOKIA_CAMARA_BASE_URL",
    f"https://{NOKIA_NAC_HOST}/passthrough/camara/v1",
)
NOKIA_API_KEY = os.getenv("NOKIA_NAC_API_KEY", "sandbox-key")

# Demo simulator subscribers are registered at the Riyadh home circle the
# dashboard defaults to (24.7136, 46.6753). Nokia's demo entitlement answers
# location verification per device line (TRUE for the clean line, FALSE for the
# fraud line) WITHOUT evaluating the queried circle geometry — the 50/50 device
# coordinates in the frontend still came back "TRUE". So every ladder tier adds
# its own geometry coherence guard: if the queried circle does NOT contain the
# subscriber's registered home location, a TRUE/PARTIAL network answer is
# contradictory and is downgraded to FALSE ("OUTSIDE GEOFENCE" on the tile).
_DEMO_HOME_LAT = 24.7136
_DEMO_HOME_LON = 46.6753
_DEMO_HOME_MSISDNS = {"+99999991000", "+99999991001", "+99999991002", "+99999991003"}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 coordinates."""
    import math

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(a))


def _geo_guard(
    msisdn: str,
    latitude: float,
    longitude: float,
    radius: int,
    verification_result: str,
) -> tuple[str, Dict[str, Any]]:
    """Geometry coherence check applied to every ladder tier.

    Returns ``(result, extra_payload_fields)``. For the demo lines whose
    registered home location is known, the requested circle must actually
    contain that home; otherwise a ``TRUE``/``PARTIAL`` answer is impossible and
    is downgraded to ``FALSE``. Non-demo numbers keep the network answer, and
    the distance is always attached so the Evidence Explorer can show it.
    """
    extra: Dict[str, Any] = {"geo_match_meters": None}
    if msisdn not in _DEMO_HOME_MSISDNS:
        return verification_result, extra
    try:
        distance = _haversine_m(latitude, longitude, _DEMO_HOME_LAT, _DEMO_HOME_LON)
    except (TypeError, ValueError):
        return verification_result, extra
    if math_isnan(distance):
        return verification_result, extra
    extra["geo_match_meters"] = round(distance, 1)
    if distance > max(radius, 0) and verification_result in {"TRUE", "PARTIAL"}:
        return "FALSE", {
            **extra,
            "geo_overridden": True,
            "geo_detail": f"{distance:,.0f}m from Riyadh home circle exceeds {radius:,}m radius",
        }
    return verification_result, extra


def math_isnan(value: float) -> bool:
    try:
        import math

        return math.isnan(value)
    except (TypeError, ValueError):
        return True


def _get_nac_client():
    """Create the NaC client once, lazily. Always None in test mode.

    A single initializer: in pytest the client is stubbed by conftest (and the
    real SDK would be network-dependent), so construction is skipped there.
    Outside tests the module-global is built on first use and cached.
    """
    global nac_client
    if nac_client is not None:
        return nac_client
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    try:
        from network_as_code import NetworkAsCodeApi

        print(
            f"[TOOLS INIT] NOKIA_API_KEY configured: "
            f"{'YES' if NOKIA_API_KEY != 'sandbox-key' else 'NO (using sandbox-key)'}"
        )
        nac_client = NetworkAsCodeApi(api_key=NOKIA_API_KEY, rapidapi_host=NOKIA_NAC_HOST)
        print(f"[TOOLS INIT] nac_client ready: {nac_client is not None}")
        if nac_client:
            print(
                f"[TOOLS INIT] Available modules: "
                f"{[a for a in dir(nac_client) if not a.startswith('_')]}"
            )
            print(
                f"[TOOLS INIT] Has number_verification: "
                f"{hasattr(nac_client, 'number_verification')}"
            )
    except Exception as exc:
        print(f"[TOOLS INIT] nac_client initialization failed: {type(exc).__name__}: {exc}")
        nac_client = None
    return nac_client


# Module-global, lazily initialized and cached on first use.
nac_client = None


def _get_headers() -> Dict[str, str]:
    """RapidAPI authentication headers.

    x-rapid-api-host must match the hostname of NOKIA_BASE_URL exactly or
    RapidAPI rejects the call, so it is derived from the URL rather than
    hardcoded (the SDK host and the REST base can differ by region).
    """
    host = urlsplit(NOKIA_BASE_URL).hostname or NOKIA_NAC_HOST
    return {
        "Content-Type": "application/json",
        "x-rapid-api-host": host,
        "x-rapidapi-key": NOKIA_API_KEY,
    }

def _safe_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


@tool
def check_sim_swap(msisdn: str, max_age: int = 240) -> str:
    """Check the Nokia NaC CAMARA SIM Swap API for recent SIM changes."""
    print(f"[SWAP] Checking SIM swap for {msisdn} with max_age {max_age} hours")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client:
        try:
            # Official SDK usage: client.sim_swap.check(phone_number, max_age)
            sim_swap_result = client.sim_swap.check(
                phone_number=msisdn, max_age=max_age
            )

            # Response has a boolean `swapped` field
            swapped = getattr(sim_swap_result, "swapped", False)
            sim_swap_date = client.sim_swap.retrieve_date(phone_number=msisdn)
            res = {
                "swapped": swapped,
                "last_sim_swap_date": sim_swap_date.latest_sim_change.isoformat(),
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }
            print(f"[SWAP:SDK SUCCESS] {res}")
            return _safe_json(res)
        except Exception as e:
            print(f"[SWAP:SDK ERROR] Nokia NaC SDK SIM Swap check failed: {e}")

    # 2. Fallback Method: Direct CAMARA REST API Call
    url = f"{NOKIA_BASE_URL}/sim-swap/sim-swap/v0/check"
    payload = {"phoneNumber": msisdn, "maxAge": max_age}
    try:
        response = requests.post(url, headers=_get_headers(), json=payload, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return _safe_json(
                {
                    "swapped": data.get("swapped", False),
                    "swap_age_hours": max_age if data.get("swapped") else None,
                    "status_code": 200,
                    "source": "Nokia NaC REST API",
                }
            )
    except Exception as e:
        print(f"[SWAP:REST ERROR] Nokia NaC REST API SIM Swap request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data (for offline hackathon testing)
    sandbox_map = {
        "+99999991000": True,
        "+99999991001": False,
    }
    if msisdn in sandbox_map:
        swapped = sandbox_map[msisdn]
        return _safe_json(
            {
                "swapped": swapped,
                "swap_age_hours": 6 if swapped else None,
                "status_code": 200,
                "source": "Nokia CAMARA Sandbox",
            }
        )
    # No documented simulator behavior: report the signal as unknown (no
    # `swapped` key) so the fail-safe gate escalates instead of treating an
    # unchecked number as a confirmed-clean one.
    print(
        f"[SWAP:SANDBOX] No documented simulator behavior for {msisdn}; "
        "reporting UNKNOWN, not not-swapped."
    )
    return _safe_json(
        {
            "verificationStatus": "UNKNOWN",
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback, undocumented signal)",
        }
    )


@tool
def check_device_swap(msisdn: str, max_age: int = 120) -> str:
    """Check whether the subscriber recently changed handsets.

    Distinct from a SIM swap, and useful corroborating evidence for account
    takeover. Uses the documented NaC CAMARA REST endpoint when entitled; the
    local simulator stays explicit in the response.
    """
    client = _get_nac_client()
    if client and hasattr(client, "device_swap"):
        try:
            result = client.device_swap.check(phone_number=msisdn, max_age=max_age)
            swapped = getattr(result, "swapped", None)
            return _safe_json(
                {
                    "deviceSwapped": (None if swapped is None else bool(swapped)),
                    "maxAge": max_age,
                    "status_code": 200,
                    "source": "Nokia NaC SDK",
                }
            )
        except Exception as exc:
            print(f"[DEVICE_SWAP:SDK ERROR] {exc}")

    url = f"{NOKIA_BASE_URL}/device-swap/device-swap/v1/check"
    try:
        response = requests.post(
            url,
            json={"phoneNumber": msisdn, "maxAge": max_age},
            headers=_get_headers(),
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            swapped = data.get("swapped") if isinstance(data, dict) else None
            return _safe_json(
                {
                    "deviceSwapped": (None if swapped is None else bool(swapped)),
                    "maxAge": max_age,
                    "status_code": 200,
                    "source": "Nokia NaC REST API",
                }
            )
    except Exception as exc:
        print(f"[DEVICE_SWAP:REST ERROR] {exc}")

    sandbox_value = {"+99999991000": True, "+99999991001": False}.get(msisdn)
    return _safe_json(
        {
            "deviceSwapped": sandbox_value,
            "maxAge": max_age,
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    )


@tool
def verify_location(
    msisdn: str,
    latitude: float,
    longitude: float,
    radius: int = 5000,
    max_age: int = 3600,
) -> str:
    """Queries Nokia NaC CAMARA Location Verification API to validate the device position."""
    print(f"\n[VERIFY_LOC] --- EXECUTING verify_location TOOL ---")
    print(f"[VERIFY_LOC] Target MSISDN: {msisdn}")
    print(f"[VERIFY_LOC] Target Coordinates: Lat {latitude}, Lon {longitude}")
    print(f"[VERIFY_LOC] Search Radius: {radius} meters")
    print(
        f"[VERIFY_LOC] SDK Available: {bool(_get_nac_client())} | Location Module: {hasattr(_get_nac_client(), 'location') if _get_nac_client() else False}"
    )

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client and hasattr(client, "location"):
        try:

            # Location verification via the SDK:
            # client.location.verify_v1(device={"phone_number": msisdn}, area={...}, max_age=3600)
            location_res = client.location.verify_v1(
                device={"phone_number": msisdn},
                area={
                    "area_type": "CIRCLE",
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": radius,
                },
                max_age=3600,
            )

            # Extract verification outcome ("TRUE", "FALSE", "PARTIAL", "UNKNOWN")
            raw_result = getattr(location_res, "verification_result", "TRUE")
            verification_result = str(raw_result).upper()
            verification_result, geo_extra = _geo_guard(
                msisdn, latitude, longitude, radius, verification_result
            )

            res_payload = {
                "verificationResult": verification_result,
                "status_code": 200,
                "source": "Nokia NaC SDK",
                "radius_meters": radius,
                "latitude": latitude,
                "longitude": longitude,
                **geo_extra,
            }

            output_json = _safe_json(res_payload)
            print(f"[VERIFY_LOC:SDK SUCCESS] Response Payload: {output_json}")
            return output_json

        except Exception as e:
            print(f"[VERIFY_LOC:SDK ERROR] Nokia NaC SDK Location Verification failed: {e}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    url = f"{NOKIA_BASE_URL}/location-verification/v1/verify"
    payload = {
        "device": {"phoneNumber": msisdn},
        "area": {
            "areaType": "CIRCLE",
            "center": {"latitude": latitude, "longitude": longitude},
            "radius": radius,
        },
        "maxAge": max_age,
    }

    print(f"[VERIFY_LOC:REST] Attempting REST API Call to: {url}")
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=5)
        print(f"[VERIFY_LOC:REST] Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            raw_result = data.get("verificationResult", "TRUE")
            verification_result, geo_extra = _geo_guard(
                msisdn, latitude, longitude, radius, str(raw_result).upper()
            )

            res_payload = {
                "verificationResult": verification_result,
                "status_code": 200,
                "source": "Nokia NaC REST API",
                "radius_meters": radius,
                "matchRate": data.get("matchRate"),
                **geo_extra,
            }
            output_json = _safe_json(res_payload)
            print(f"[VERIFY_LOC:REST SUCCESS] Response Payload: {output_json}")
            return output_json

    except Exception as e:
        print(f"[VERIFY_LOC:REST ERROR] Nokia NaC REST API Location request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    print(f"[VERIFY_LOC:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    map = {
        "+99999991000": (200, "FALSE"),
        "+99999991001": (200, "TRUE"),
        "+99999991002": (200, "PARTIAL"),
        "+99999991003": (200, "UNKNOWN"),
        # Error rows cannot return a verification result; report UNKNOWN so the
        # synthesis layer treats the unchecked location as a risk signal rather
        # than mistaking an absence of data for a clean match.
        "+99999990400": (400, "UNKNOWN"),
        "+99999990404": (404, "UNKNOWN"),
        "+99999990422": (422, "UNKNOWN"),
        "+99999990500": (500, "UNKNOWN"),
        "+99999990502": (502, "UNKNOWN"),
        "+99999990503": (503, "UNKNOWN"),
        "+99999990504": (504, "UNKNOWN"),
    }
    sandbox_entry = map.get(msisdn)
    if sandbox_entry is None:
        sandbox_entry = (200, "UNKNOWN")
        print(
            f"[VERIFY_LOC:SANDBOX FALLBACK] No documented simulator behavior for {msisdn}; "
            "defaulting to UNKNOWN (never assume a confirmed match)."
        )
    sandbox_payload = {
        "verificationResult": sandbox_entry[1],
        "status_code": sandbox_entry[0],
        "source": "Nokia CAMARA Sandbox",
        "radius_meters": radius,
        "latitude": latitude,
        "longitude": longitude,
    }
    verification_result, geo_extra = _geo_guard(
        msisdn, latitude, longitude, radius, sandbox_payload.get("verificationResult", "UNKNOWN")
    )
    sandbox_payload["verificationResult"] = verification_result
    sandbox_payload.update(geo_extra)

    output_json = _safe_json(sandbox_payload)
    print(f"[VERIFY_LOC:SANDBOX RESULT] Payload: {output_json}")
    return output_json

@tool
def check_roaming_status(msisdn: str) -> str:
    """Queries Nokia CAMARA Device Status APIs to determine if the device is roaming internationally."""
    print(f"\n[ROAMNG] --- EXECUTING check_roaming_status TOOL ---")
    print(f"[ROAMNG] Target MSISDN: {msisdn}")
    print(f"[ROAMNG] SDK Available: {bool(_get_nac_client())}")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client:
        try:
            # Confirmed against Nokia's own docs (device-roaming-status page): there is no
            # devices.get(...).get_roaming_status() chain in this SDK. The Device Status
            # roaming check is called directly on the client.
            roaming_res = client.device_status.retrieve_roaming_status(
                device={"phone_number": msisdn}
            )

            # Documented response fields (snake_case Python attrs, same aliasing pattern
            # as every other API in this SDK):
            #   roaming          -> bool
            #   country_code     -> int (MCC), present only if roaming
            #   country_name     -> list[str] of ISO 3166 alpha-2 codes, present only if
            #                       roaming — NOT a single human-readable country name,
            #                       despite the field name. Can be an empty list even
            #                       while roaming, per Nokia's own sample response.
            #   last_status_time -> optional ISO 8601 string
            is_roaming = getattr(roaming_res, "roaming", False)
            country_code = getattr(roaming_res, "country_code", None)
            country_iso_codes = getattr(roaming_res, "country_name", None) or []

            res_payload = {
                "roamingStatus": "INTERNATIONAL_ROAMING" if is_roaming else "DOMESTIC",
                "roaming": is_roaming,
                "countryCode": country_code if is_roaming else None,
                "countryIsoCodes": country_iso_codes if is_roaming else [],
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }

            output_json = _safe_json(res_payload)
            print(f"[ROAMNG:SDK SUCCESS] Response Payload: {output_json}")
            return output_json

        except Exception as e:
            print(f"[ROAMNG:SDK ERROR] Nokia NaC SDK Device Roaming Status check failed: {e}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    # Nokia's public docs only document SDK usage for this endpoint — no REST
    # passthrough path is published anywhere we've found for this SDK (same was true for
    # SIM Swap, Location Verification, and QoD). This URL is unverified; confirm it
    # against your actual RapidAPI subscription before trusting it in a live demo.
    url = f"{NOKIA_BASE_URL}/device-status/device-roaming-status/v1/retrieve"
    payload = {"device": {"phoneNumber": msisdn}}

    print(f"[ROAMNG:REST] Attempting REST API Call to: {url}")
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=5)
        print(f"[ROAMNG:REST] Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            is_roaming = data.get("roaming", False)

            res_payload = {
                "roamingStatus": "INTERNATIONAL_ROAMING" if is_roaming else "DOMESTIC",
                "roaming": is_roaming,
                "countryCode": data.get("countryCode") if is_roaming else None,
                "countryIsoCodes": data.get("countryName", []) if is_roaming else [],
                "status_code": 200,
                "source": "Nokia NaC REST API",
            }
            output_json = _safe_json(res_payload)
            print(f"[ROAMNG:REST SUCCESS] Response Payload: {output_json}")
            return output_json

    except Exception as e:
        print(f"[ROAMNG:REST ERROR] Nokia NaC REST API Device Roaming request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    # Corrected to Nokia's actual documented simulator identifiers. The previous
    # "+999987" prefix / "87" suffix heuristic matched nothing in Nokia's real sandbox —
    # only these two exact numbers have documented behavior for this API.
    print(f"[ROAMNG:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    if msisdn == "+99999991000":
        sandbox_payload = {
            "roamingStatus": "INTERNATIONAL_ROAMING",
            "roaming": True,
            "countryCode": None,
            "countryIsoCodes": [],
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    elif msisdn == "+99999991001":
        sandbox_payload = {
            "roamingStatus": "DOMESTIC",
            "roaming": False,
            "countryCode": None,
            "countryIsoCodes": [],
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    else:
        # No documented simulator behavior — genuinely unknown, not domestic.
        # `roamingStatus` is deliberately absent so the fail-safe gate escalates
        # instead of treating an unchecked number as confirmed non-roaming.
        print(
            f"[ROAMNG:SANDBOX FALLBACK] {msisdn} has no documented simulator behavior — "
            f"reporting UNKNOWN, not DOMESTIC. Use +99999991000 (roaming) or "
            f"+99999991001 (not roaming) for reliable sandbox results."
        )
        sandbox_payload = {
            "roaming": None,
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback, undocumented signal)",
        }

    output_json = _safe_json(sandbox_payload)
    print(f"[ROAMNG:SANDBOX RESULT] Payload: {output_json}")
    return output_json


@tool
def check_device_reachability(msisdn: str) -> str:
    """Queries device reachability and connectivity status for the target subscriber."""
    print(f"[DEV_REACH] Checking device reachability for {msisdn}")

    def _derive_status(connectivity):
        has_data = "DATA" in connectivity
        has_sms = "SMS" in connectivity
        if has_data and has_sms:
            return "DATA_AND_SMS"
        if has_data:
            return "DATA_ONLY"
        if has_sms:
            return "SMS_ONLY"
        return "UNREACHABLE"

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client:
        try:
            # Confirmed against Nokia's device-reachability-status docs — the real call is
            # device_status.retrieve_reachability_status(device={...}), not a raw REST POST.
            reach_res = client.device_status.retrieve_reachability_status(
                device={"phone_number": msisdn}
            )

            # Documented response fields:
            #   connectivity     -> list, one of ["DATA"], ["SMS"], ["DATA","SMS"];
            #                        absent/empty if the device is not reachable
            #   reachable        -> bool
            #   last_status_time -> optional ISO 8601 string
            connectivity = getattr(reach_res, "connectivity", None) or []
            is_reachable = getattr(reach_res, "reachable", False)

            res_payload = {
                "reachabilityStatus": _derive_status(connectivity) if is_reachable else "UNREACHABLE",
                "reachable": is_reachable,
                "connectivity": connectivity,
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }
            output_json = _safe_json(res_payload)
            print(f"[DEV_REACH:SDK SUCCESS] Response Payload: {output_json}")
            return output_json

        except Exception as e:
            print(f"[DEV_REACH:SDK ERROR] Nokia NaC SDK Device Reachability check failed: {e}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    # As with roaming status, Nokia's public docs only show SDK usage for this
    # endpoint — no REST passthrough path is published. This URL is unverified; confirm
    # against your own RapidAPI subscription before trusting it in a live demo.
    url = f"{NOKIA_BASE_URL}/device-status/device-reachability-status/v1/retrieve"
    try:
        res = requests.post(
            url, json={"device": {"phoneNumber": msisdn}}, headers=_get_headers(), timeout=5
        )
        if res.status_code == 200:
            data = res.json()
            connectivity = data.get("connectivity") or []
            is_reachable = data.get("reachable", False)
            res_payload = {
                "reachabilityStatus": _derive_status(connectivity) if is_reachable else "UNREACHABLE",
                "reachable": is_reachable,
                "connectivity": connectivity,
                "status_code": 200,
                "source": "Nokia NaC REST API",
            }
            output_json = _safe_json(res_payload)
            print(f"[DEV_REACH:REST SUCCESS] Response Payload: {output_json}")
            return output_json
    except Exception as e:
        print(f"[DEV_REACH:REST ERROR] Nokia NaC REST API Device Reachability request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    # Nokia's documented simulator identifiers for this API (previous code ignored the
    # msisdn entirely and always returned CONNECTED_DATA regardless of input):
    #   +99999991000 -> SMS only       +99999991002 -> DATA and SMS
    #   +99999991001 -> DATA only      +99999991003 -> lost connectivity (unreachable)
    print(f"[DEV_REACH:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    sandbox_map = {
        "+99999991000": (True, ["SMS"]),
        "+99999991001": (True, ["DATA"]),
        "+99999991002": (True, ["DATA", "SMS"]),
        "+99999991003": (False, []),
    }
    if msisdn in sandbox_map:
        is_reachable, connectivity = sandbox_map[msisdn]
    else:
        # No documented behavior — genuinely unknown, not confirmed unreachable.
        # Mirrors the UNKNOWN/PARTIAL distinction already given to location
        # verification: "we don't know" should not silently score the same
        # as "confirmed bad." Report UNKNOWN instead of defaulting to UNREACHABLE.
        is_reachable, connectivity = None, []
        print(
            f"[DEV_REACH:SANDBOX FALLBACK] {msisdn} has no documented simulator behavior — "
            f"reporting UNKNOWN, not UNREACHABLE. Use one of {list(sandbox_map)} for documented results."
        )

    # Reachable is tri-state: True -> derived status, False -> UNREACHABLE, None -> UNKNOWN
    if is_reachable is True:
        reach_status = _derive_status(connectivity)
    elif is_reachable is False:
        reach_status = "UNREACHABLE"
    else:
        reach_status = "UNKNOWN"

    sandbox_payload = {
        "reachabilityStatus": reach_status,
        "reachable": is_reachable,
        "connectivity": connectivity,
        "status_code": 200,
        "source": "Nokia CAMARA Sandbox (local fallback)",
    }
    output_json = _safe_json(sandbox_payload)
    print(f"[DEV_REACH:SANDBOX RESULT] Payload: {output_json}")
    return output_json

@tool
def create_qod_session(
    msisdn: str,
    service_ip: str = "233.252.0.2",
    profile: str = "QOS_E",
    duration_seconds: int = 3600,
) -> str:
    """Requests a Quality-on-Demand session to prioritize bandwidth/latency between a device
    and an application server for a bounded duration. This is QoD, not network slicing —
    it does not provision a dedicated network slice. See Network Slice Management for that."""
    print(f"[QOD] Creating QoD session for {msisdn} -> {service_ip} with profile {profile}")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client:
        try:
            # Confirmed live against Nokia's sandbox earlier in this project. Two hard-won
            # details: the field is "ipv4address" (no underscores — Nokia's own docs example
            # shows "ipv_4_address", which 422s), and profile must be a real CAMARA QoS
            # label. QOS_E is the default here (not QOS_L) because auth/step-up traffic is
            # small and latency-sensitive, not bandwidth-hungry like video — same reasoning
            # already applied to the main agent's QoD tool.
            result = client.qod.create_session_v1(
                application_server={"ipv4address": service_ip},
                qos_profile=profile,
                device={"phone_number": msisdn},
                duration=duration_seconds,
            )
            # Confirmed live response attrs: session_id, qos_status (snake_case).
            res_payload = {
                "sessionId": getattr(result, "session_id", None),
                "qosStatus": getattr(result, "qos_status", None),
                "qosProfile": profile,
                "durationSeconds": duration_seconds,
                "source": "Nokia NaC SDK",
            }
            output_json = _safe_json(res_payload)
            print(f"[QOD:SDK SUCCESS] Response Payload: {output_json}")
            return output_json
        except Exception as e:
            print(f"[QOD:SDK ERROR] Nokia NaC SDK QoD session creation failed: {e}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    # Same caveat as the other tools: no published REST passthrough path exists for this
    # SDK, so this URL/shape is unverified. The original payload was also missing
    # applicationServer entirely — the same omission that 422s on the confirmed SDK path —
    # so it's fixed here too, even though the endpoint itself remains unconfirmed.
    url = f"{NOKIA_BASE_URL}/qod/v0/sessions"
    payload = {
        "device": {"phoneNumber": msisdn},
        "applicationServer": {"ipv4Address": service_ip},
        "qosProfile": profile,
        "duration": duration_seconds,
    }
    try:
        res = requests.post(url, json=payload, headers=_get_headers(), timeout=5)
        print(f"-- {res.status_code}")
        if res.status_code in (200, 201):
            data = res.json()
            res_payload = {
                "sessionId": data.get("sessionId") or data.get("session_id"),
                "qosStatus": data.get("qosStatus") or data.get("qos_status"),
                "qosProfile": profile,
                "durationSeconds": duration_seconds,
                "source": "Nokia NaC REST API",
            }
            output_json = _safe_json(res_payload)
            print(f"[QOD:REST SUCCESS] Response Payload: {output_json}")
            return output_json
    except Exception as e:
        print(f"[QOD:REST ERROR] Nokia NaC REST API QoD session request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    print(f"[QOD:SANDBOX FALLBACK] Returning simulated QoD session for {msisdn}")
    return _safe_json(
        {
            "sessionId": "qod-sess-883920",
            "qosStatus": "REQUESTED",
            "qosProfile": profile,
            "durationSeconds": duration_seconds,
            "source": "Nokia CAMARA Sandbox (local fallback, not a live session)",
        }
    )


@tool
def verify_number(msisdn: str) -> str:
    """Queries the Nokia NaC CAMARA Number Verification API to confirm that the
    MSISDN presented for the transaction belongs to the subscriber's device.
    Uses the Number Verification v0.2 flow (the same silent-check family as
    SIM Swap), which authenticates the number without an SMS OTP."""
    print(f"\n[NUMVER] --- EXECUTING verify_number TOOL ---")
    print(f"[NUMVER] Target MSISDN: {msisdn}")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    print(f"[NUMVER] SDK Available: {bool(client)} | NV Module: {hasattr(client, 'number_verification') if client else False}")
    print(f"[NUMVER] NOKIA_API_KEY configured: {'YES' if NOKIA_API_KEY != 'sandbox-key' else 'NO (using sandbox-key)'}")

    if client and hasattr(client, "number_verification"):
        try:
            # Verified against the installed network-as-code SDK
            # (network_as_code/number_verification): the v0.2 VERIFY method is
            #   client.number_verification.verify_v2(
            #       request={"phone_number": msisdn}, correlator=...
            #   )
            # and the documented response model carries
            #   device_phone_number_verified (alias devicePhoneNumberVerified)
            # as a strict bool: True when the number matches the device.
            # A correlator ties the verify request to a prior OAuth2 consent
            # redirect (client.oauth); on the sandbox the direct phone_number
            # request is accepted unless the API key lacks Number Verification
            # entitlement, in which case we degrade to the fallbacks below.
            print(f"[NUMVER] Calling SDK verify_v2 for {msisdn}")
            verify_result = client.number_verification.verify_v2(
                request={"phone_number": msisdn}
            )

            verified = getattr(verify_result, "device_phone_number_verified", None)
            res_payload = {
                "devicePhoneNumberVerified": verified,
                "verified": verified,
                "verificationStatus": "VERIFIED" if verified is True else "FAILED",
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }
            output_json = _safe_json(res_payload)
            print(f"[NUMVER:SDK SUCCESS] Response Payload: {output_json}")
            return output_json

        except Exception as e:
            print(f"[NUMVER:SDK ERROR] Nokia NaC SDK Number Verification failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    else:
        if not client:
            print("[NUMVER] nac_client is None - SDK not initialized")
        elif not hasattr(client, "number_verification"):
            print(f"[NUMVER] nac_client has NO number_verification attribute. Available attrs: {[a for a in dir(client) if not a.startswith('_')]}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    # Nokia NaC's documented Number Verification v2 passthrough. Retain the
    # SDK-first path above; this REST route is the confirmed fallback endpoint.
    url = f"{NOKIA_BASE_URL}/number-verification/number-verification/v2/verify"
    print(f"[NUMVER] Trying REST fallback to {url}")
    try:
        response = requests.post(
            url, json={"phoneNumber": msisdn}, headers=_get_headers(), timeout=5
        )
        print(f"[NUMVER] REST Response: HTTP {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            verified = data.get("devicePhoneNumberVerified")
            res_payload = {
                "devicePhoneNumberVerified": verified,
                "verified": verified,
                "verificationStatus": "VERIFIED" if verified is True else "FAILED",
                "status_code": 200,
                "source": "Nokia NaC REST API",
            }
            output_json = _safe_json(res_payload)
            print(f"[NUMVER:REST SUCCESS] Response Payload: {output_json}")
            return output_json
        else:
            print(f"[NUMVER:REST ERROR] HTTP {response.status_code}: {response.text[:500]}")
    except Exception as e:
        print(f"[NUMVER:REST ERROR] Nokia NaC REST API Number Verification request failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # 3. Fallback Method: Simulated Sandbox Data
    # Documented demo numbers: the fraud-test subscriber (+99999991000) fails
    # identity verification; the clean subscriber (+99999991001) passes. Any
    # other number has no documented simulator behavior, so we report UNKNOWN —
    # "we don't know" must not silently score the same as "confirmed good".
    print(f"[NUMVER:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    if msisdn == "+99999991000":
        verification_status = "FAILED"
        verified = False
    elif msisdn == "+99999991001":
        verification_status = "VERIFIED"
        verified = True
    else:
        verification_status = "UNKNOWN"
        verified = None
        print(
            f"[NUMVER:SANDBOX FALLBACK] {msisdn} has no documented simulator behavior — "
            f"reporting UNKNOWN, not VERIFIED. Use +99999991000 (fails) or +99999991001 (passes)."
        )

    return _safe_json(
        {
            "devicePhoneNumberVerified": verified,
            "verified": verified,
            "verificationStatus": verification_status,
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    )


@tool
def get_congestion_insights(msisdn: str, lookback_hours: int = 1) -> str:
    """Queries the Nokia NaC CAMARA Congestion Insights API for recent cell-level
    congestion around the subscriber's serving area. Returns per-interval
    congestion levels (Low/Medium/High) with confidence, plus the worst level
    seen in the lookback window. Relevant to crowd-gathering detection in
    dense urban zones and mega-events (smart-city context)."""
    from datetime import datetime, timedelta, timezone

    print(f"\n[CONGEST] --- EXECUTING get_congestion_insights TOOL ---")
    print(f"[CONGEST] Target MSISDN: {msisdn} | lookback_hours: {lookback_hours}")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client and hasattr(client, "congestion_insights"):
        try:
            # Verified against the installed network-as-code SDK
            # (network_as_code/congestion_insights): the QUERY method is
            #   client.congestion_insights.query(
            #       device={"phone_number": msisdn}, start=..., end=...
            #   )
            # and returns a list of items with documented fields
            #   time_interval_start / time_interval_stop (datetime),
            #   congestion_level ("Low" | "Medium" | "High"),
            #   confidence_level (int, optional).
            start = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            end = datetime.now(timezone.utc)
            congestion_results = client.congestion_insights.query(
                device={"phone_number": msisdn}, start=start, end=end
            )

            intervals = []
            for item in congestion_results or []:
                intervals.append(
                    {
                        "timeIntervalStart": getattr(item, "time_interval_start", None).isoformat()
                        if getattr(item, "time_interval_start", None)
                        else None,
                        "timeIntervalStop": getattr(item, "time_interval_stop", None).isoformat()
                        if getattr(item, "time_interval_stop", None)
                        else None,
                        "congestionLevel": getattr(item, "congestion_level", None),
                        "confidenceLevel": getattr(item, "confidence_level", None),
                    }
                )
            levels = [it.get("congestionLevel") for it in intervals]
            max_level = "High" if "High" in levels else ("Medium" if "Medium" in levels else ("Low" if levels else None))

            res_payload = {
                "congestionLevels": intervals,
                "maxCongestionLevel": max_level,
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }
            output_json = _safe_json(res_payload)
            print(f"[CONGEST:SDK SUCCESS] Response Payload: {output_json}")
            return output_json

        except Exception as e:
            print(f"[CONGEST:SDK ERROR] Nokia NaC SDK Congestion Insights query failed: {e}")

    # 2. Fallback Method: Direct Nokia CAMARA REST API Call
    # CAMARA Congestion Insights v0: POST /congestion-insights/v0/queries
    # Unverified passthrough path, same caveat as the other tools.
    url = f"{NOKIA_BASE_URL}/congestion-insights/v0/queries"
    try:
        response = requests.post(
            url,
            json={
                "device": {"phoneNumber": msisdn},
                "startTime": (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat(),
                "endTime": datetime.now(timezone.utc).isoformat(),
            },
            headers=_get_headers(),
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            intervals = data.get("congestionLevels") or []
            levels = [it.get("congestionLevel") for it in intervals]
            max_level = "High" if "High" in levels else ("Medium" if "Medium" in levels else ("Low" if levels else None))
            res_payload = {
                "congestionLevels": intervals,
                "maxCongestionLevel": max_level,
                "status_code": 200,
                "source": "Nokia NaC REST API",
            }
            output_json = _safe_json(res_payload)
            print(f"[CONGEST:REST SUCCESS] Response Payload: {output_json}")
            return output_json
    except Exception as e:
        print(f"[CONGEST:REST ERROR] Nokia NaC REST API Congestion Insights request failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    # Documented demo numbers: the smart-city crowd number (+99999991002)
    # simulates gathering in a dense zone (Medium), the fraud number
    # (+99999991000) sits in a stressed cell (High), clean subscribers see Low.
    print(f"[CONGEST:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    sandbox_level = {
        "+99999991000": "High",
        "+99999991001": "Low",
        "+99999991002": "Medium",
    }.get(msisdn)
    # No documented simulator behaviour: report UNKNOWN rather than guessing Low,
    # which is contextually honest and (as congestion never flips a clean verdict)
    # risk-neutral.
    if sandbox_level is None:
        sandbox_level = "Unknown"
        print(
            f"[CONGEST:SANDBOX FALLBACK] {msisdn} has no documented simulator "
            "behaviour — reporting Unknown, not Low."
        )

    return _safe_json(
        {
            "congestionLevels": [
                {
                    "timeIntervalStart": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
                    "timeIntervalStop": datetime.now(timezone.utc).isoformat(),
                    "congestionLevel": sandbox_level,
                    "confidenceLevel": 95,
                }
            ],
            "maxCongestionLevel": sandbox_level,
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    )


@tool
def check_number_recycling(msisdn: str, specified_date: str = None) -> str:
    """Queries the Nokia NaC CAMARA Number Recycling API to detect if a
    phone number has been recycled (reassigned to a new subscriber).
    Recycled numbers are a fraud risk as the new owner may receive OTPs
    intended for the previous owner."""
    from datetime import datetime, timezone
    print(f"\n[NUM_RECYCLE] --- EXECUTING check_number_recycling TOOL ---")
    print(f"[NUM_RECYCLE] Target MSISDN: {msisdn}")

    if specified_date is None:
        specified_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Primary Method: Official Nokia NaC Python SDK
    client = _get_nac_client()
    if client and hasattr(client, "number_recycling"):
        try:
            from datetime import date as _date

            sdk_date = _date.fromisoformat(specified_date)
            recycle_result = client.number_recycling.check(
                phone_number=msisdn, specified_date=sdk_date
            )
            recycled = getattr(recycle_result, "phoneNumberRecycled", False)
            res = {
                "phoneNumberRecycled": recycled,
                "status_code": 200,
                "source": "Nokia NaC SDK",
            }
            print(f"[NUM_RECYCLE:SDK SUCCESS] {res}")
            return _safe_json(res)
        except Exception as e:
            print(f"[NUM_RECYCLE:SDK ERROR] Nokia NaC SDK Number Recycling check failed: {e}")

    # 2. Fallback Method: Direct CAMARA REST API Call
    # Same caveat as the other tools: the REST passthrough below is unverified,
    # so the SDK-first call above is the trusted tier.
    url = f"{NOKIA_BASE_URL}/number-recycling/number-recycling/v0.2/check"
    payload = {"phoneNumber": msisdn, "specifiedDate": specified_date}
    try:
        response = requests.post(url, headers=_get_headers(), json=payload, timeout=5)
        if response.status_code == 200:
            data = response.json()
            res = {
                "phoneNumberRecycled": data.get("phoneNumberRecycled", False),
                "status_code": 200,
                "source": "Nokia NaC REST API",
            }
            print(f"[NUM_RECYCLE:REST SUCCESS] {res}")
            return _safe_json(res)
    except Exception as e:
        print(f"[NUM_RECYCLE:REST ERROR] Nokia NaC REST API Number Recycling failed: {e}")

    # 3. Fallback Method: Simulated Sandbox Data
    # Documented: fraud subscriber (+99999991000) shows recycled, the clean
    # subscriber (+99999991001) is not. Undocumented numbers report None —
    # "not recycled" is a clean claim we must not assert without evidence.
    print(f"[NUM_RECYCLE:SANDBOX FALLBACK] Executing local sandbox evaluation for {msisdn}")
    if msisdn == "+99999991000":
        recycled = True
    elif msisdn == "+99999991001":
        recycled = False
    else:
        recycled = None
        print(
            f"[NUM_RECYCLE:SANDBOX FALLBACK] {msisdn} has no documented simulator behavior — "
            "reporting UNKNOWN, not recycled. Use +99999991000 (recycled) or "
            "+99999991001 (not recycled) for reliable sandbox results."
        )

    return _safe_json(
        {
            "phoneNumberRecycled": recycled,
            "status_code": 200,
            "source": "Nokia CAMARA Sandbox (local fallback)",
        }
    )
