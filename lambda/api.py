# -*- coding: utf-8 -*-
"""
TBM API Client (synchronous version for AWS Lambda)
Uses SIRI-Lite API from Bordeaux Métropole
"""

import requests
import unicodedata
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# API Configuration
API_BASE = "https://bdx.mecatran.com/utw/ws/siri/2.0/bordeaux"
API_KEY = "opendata-bordeaux-metropole-flux-gtfs-rt"

# Bounding box for Bordeaux area (W, N, E, S)
BBOX = (-0.81, 45.10, -0.35, 44.70)

# Preview interval for departures
DEFAULT_PREVIEW = "PT90M"


def normalize_text(text: str) -> str:
    """Normalize text for comparison (remove accents, lowercase)."""
    if not text:
        return ""
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_text.lower().strip()


def to_int(value: Any, default: int = -1) -> int:
    """Safe conversion to int."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def get_value(v: Any) -> str:
    """Extract text value from SIRI field (can be list/dict/str)."""
    if isinstance(v, list):
        if not v:
            return ""
        x = v[0]
        if isinstance(x, dict):
            return x.get("value") or x.get("Value") or ""
        if isinstance(x, str):
            return x
        return ""
    if isinstance(v, dict):
        return v.get("value") or v.get("Value") or ""
    if isinstance(v, str):
        return v
    return ""


class TBMClient:
    """Client for TBM SIRI-Lite API."""

    def __init__(self, api_base: str = API_BASE, api_key: str = API_KEY):
        self._base = api_base
        self._key = api_key
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._lines_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._stops_cache: Dict[str, List[Dict[str, Any]]] = {}

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make GET request to API."""
        params["AccountKey"] = self._key
        url = f"{self._base}/{endpoint}"
        response = self._session.get(url, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def get_lines(self) -> Dict[str, Dict[str, Any]]:
        """Get all available lines with their destinations."""
        if self._lines_cache:
            return self._lines_cache

        data = self._get("lines-discovery.json", {})
        items = (
            data.get("Siri", {})
            .get("LinesDelivery", {})
            .get("AnnotatedLineRef", [])
        )

        found_lines: Dict[str, Dict[str, Any]] = {}
        for it in items or []:
            line_ref = get_value(it.get("LineRef"))
            line_name = get_value(it.get("LineName"))

            if line_ref:
                line_code = get_value(it.get("LineCode"))
                destinations = it.get("Destinations") or []
                
                for d in destinations:
                    direction_ref = to_int(get_value(d.get("DirectionRef")))
                    place_name = get_value(d.get("PlaceName"))
                    key = f"{line_ref}-{direction_ref}"

                    found_lines[key] = {
                        "line_ref": line_ref,
                        "line_name": line_name,
                        "line_code": line_code,
                        "dest_name": place_name,
                        "direction_ref": direction_ref,
                    }

        # Sort by line name
        self._lines_cache = dict(
            sorted(found_lines.items(), key=lambda kv: kv[1].get("line_name", ""))
        )
        return self._lines_cache

    def get_stops_for_line(
        self, line_ref: str, direction_ref: int, bbox: Tuple[float, float, float, float] = BBOX
    ) -> List[Dict[str, Any]]:
        """Get all stops for a specific line and direction."""
        cache_key = f"{line_ref}-{direction_ref}"
        if cache_key in self._stops_cache:
            return self._stops_cache[cache_key]

        W, N, E, S = bbox
        params = {
            "BoundingBox.UpperLeft.longitude": W,
            "BoundingBox.UpperLeft.latitude": N,
            "BoundingBox.LowerRight.longitude": E,
            "BoundingBox.LowerRight.latitude": S,
        }

        data = self._get("stoppoints-discovery.json", params)
        items = (
            data.get("Siri", {})
            .get("StopPointsDelivery", {})
            .get("AnnotatedStopPointRef", [])
        )

        results = []
        for it in items or []:
            stop_name = get_value(it.get("StopName"))
            stop_point_ref = get_value(it.get("StopPointRef"))
            lines = it.get("Lines") or []

            for l in lines:
                if get_value(l) == line_ref:
                    # Check if this stop has departures in the right direction
                    departures = self.get_departures(
                        stop_point_ref, line_ref, direction_ref, max_visits=1
                    )
                    if departures:
                        results.append({
                            "stop_name": stop_name,
                            "stop_point_ref": stop_point_ref,
                            "direction_ref": direction_ref,
                        })
                    break

        # Sort by stop name
        results.sort(key=lambda x: normalize_text(x.get("stop_name", "")))
        self._stops_cache[cache_key] = results
        return results

    def get_departures(
        self,
        stop_point_ref: str,
        line_ref: Optional[str] = None,
        direction_ref: int = -1,
        preview: str = DEFAULT_PREVIEW,
        max_visits: int = 4,
    ) -> List[Dict[str, Any]]:
        """Get upcoming departures for a stop."""
        params: Dict[str, Any] = {
            "MonitoringRef": stop_point_ref,
            "PreviewInterval": preview,
            "MaximumStopVisits": max_visits,
        }
        if line_ref:
            params["LineRef"] = line_ref
        if direction_ref != -1:
            params["DirectionRef"] = direction_ref

        data = self._get("stop-monitoring.json", params)
        deliveries = (
            data.get("Siri", {})
            .get("ServiceDelivery", {})
            .get("StopMonitoringDelivery", [])
        )

        visits: List[Dict[str, Any]] = []
        for d in deliveries:
            for v in d.get("MonitoredStopVisit") or []:
                mvj = v.get("MonitoredVehicleJourney") or {}
                call = mvj.get("MonitoredCall") or {}

                aimed = call.get("AimedDepartureTime") or call.get("AimedArrivalTime")
                expected = call.get("ExpectedDepartureTime") or call.get("ExpectedArrivalTime")
                destination = get_value(mvj.get("DestinationName")) or get_value(mvj.get("DirectionName"))

                visits.append({
                    "line_ref": get_value(mvj.get("LineRef")),
                    "direction_ref": to_int(get_value(mvj.get("DirectionRef"))),
                    "destination": destination,
                    "aimed": aimed,
                    "expected": expected,
                    "realtime": bool(expected and aimed and expected != aimed),
                })

        # Sort by expected/aimed time
        visits.sort(key=lambda x: x.get("expected") or x.get("aimed") or "")
        return visits

    def search_stop(
        self,
        stop_query: Optional[str] = None,
        line_query: Optional[str] = None,
        dest_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for stops matching the query.
        Returns list of matching stops with their line info.
        """
        results = []
        lines = self.get_lines()

        # Normalize queries
        stop_norm = normalize_text(stop_query) if stop_query else None
        line_norm = normalize_text(line_query) if line_query else None
        dest_norm = normalize_text(dest_query) if dest_query else None

        # Filter lines
        matching_lines = []
        for key, line_info in lines.items():
            line_name_norm = normalize_text(line_info.get("line_name", ""))
            line_code_norm = normalize_text(line_info.get("line_code", ""))
            dest_name_norm = normalize_text(line_info.get("dest_name", ""))

            # If line query specified, filter by line
            if line_norm:
                if line_norm not in line_name_norm and line_norm not in line_code_norm:
                    continue

            # If destination query specified, filter by destination
            if dest_norm:
                if dest_norm not in dest_name_norm:
                    continue

            matching_lines.append(line_info)

        # If no stop query, return lines only (no stop info)
        if not stop_norm:
            if matching_lines:
                # Return first matching line info without stop
                line = matching_lines[0]
                return [{
                    "line_ref": line.get("line_ref"),
                    "line_name": line.get("line_name"),
                    "line_code": line.get("line_code"),
                    "direction_ref": line.get("direction_ref"),
                    "dest_name": line.get("dest_name"),
                    "stop_point_ref": None,
                    "stop_name": None,
                }]
            return []

        # Search stops for matching lines
        for line_info in matching_lines[:5]:  # Limit to avoid too many API calls
            line_ref = line_info.get("line_ref")
            direction_ref = line_info.get("direction_ref", -1)

            stops = self.get_stops_for_line(line_ref, direction_ref)
            
            for stop in stops:
                stop_name = stop.get("stop_name", "")
                stop_name_norm = normalize_text(stop_name)

                # Check if stop name matches query
                if stop_norm in stop_name_norm:
                    results.append({
                        "stop_point_ref": stop.get("stop_point_ref"),
                        "stop_name": stop_name,
                        "line_ref": line_ref,
                        "line_name": line_info.get("line_name"),
                        "line_code": line_info.get("line_code"),
                        "direction_ref": direction_ref,
                        "dest_name": line_info.get("dest_name"),
                    })

        # Sort by relevance (exact match first, then by name)
        def sort_key(x):
            name_norm = normalize_text(x.get("stop_name", ""))
            # Exact match gets priority
            if name_norm == stop_norm:
                return (0, name_norm)
            # Starts with gets second priority
            if name_norm.startswith(stop_norm):
                return (1, name_norm)
            return (2, name_norm)

        results.sort(key=sort_key)
        return results[:10]  # Return top 10 matches

    def find_line_by_query(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a line by name or code."""
        query_norm = normalize_text(query)
        lines = self.get_lines()

        for key, line_info in lines.items():
            line_name_norm = normalize_text(line_info.get("line_name", ""))
            line_code_norm = normalize_text(line_info.get("line_code", ""))

            if query_norm in line_name_norm or query_norm == line_code_norm:
                return line_info

        return None

