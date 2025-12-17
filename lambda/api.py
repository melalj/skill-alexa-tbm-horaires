# -*- coding: utf-8 -*-
"""
TBM API Client (synchronous version for AWS Lambda)
Uses SIRI-Lite API from Bordeaux Métropole
"""

import re
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

# French number words to digits
FRENCH_NUMBERS = {
    "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3",
    "quatre": "4", "cinq": "5", "six": "6", "sept": "7", "huit": "8",
    "neuf": "9", "dix": "10", "onze": "11", "douze": "12", "treize": "13",
    "quatorze": "14", "quinze": "15", "seize": "16", "dix-sept": "17",
    "dix-huit": "18", "dix-neuf": "19", "vingt": "20", "trente": "30",
    "quarante": "40", "cinquante": "50", "soixante": "60",
}


def normalize_text(text: str) -> str:
    """Normalize text for comparison (remove accents, lowercase)."""
    if not text:
        return ""
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    result = ascii_text.lower().strip()
    
    # Convert French number words to digits
    for word, digit in FRENCH_NUMBERS.items():
        result = re.sub(rf'\b{word}\b', digit, result)
    
    return result


def extract_keywords(text: str) -> List[str]:
    """Extract meaningful keywords from text for fuzzy matching."""
    if not text:
        return []
    normalized = normalize_text(text)
    # Split on spaces and punctuation, filter short words
    words = re.split(r'[\s\-\_\,\.]+', normalized)
    # Keep words with 2+ chars, ignore common articles
    stopwords = {'le', 'la', 'les', 'de', 'du', 'des', 'a', 'au', 'aux', 'et', 'en'}
    return [w for w in words if len(w) >= 2 and w not in stopwords]


def fuzzy_match(query: str, target: str) -> float:
    """
    Calculate fuzzy match score between query and target.
    Returns score 0.0-1.0 (1.0 = perfect match).
    """
    query_norm = normalize_text(query)
    target_norm = normalize_text(target)
    
    # Exact match
    if query_norm == target_norm:
        return 1.0
    
    # Query contained in target
    if query_norm in target_norm:
        return 0.9
    
    # Target contained in query
    if target_norm in query_norm:
        return 0.8
    
    # Keyword matching
    query_words = extract_keywords(query)
    target_words = extract_keywords(target)
    
    if not query_words or not target_words:
        return 0.0
    
    # Count matching words
    matches = sum(1 for qw in query_words if any(qw in tw or tw in qw for tw in target_words))
    score = matches / len(query_words)
    
    return score * 0.7  # Max 0.7 for keyword match


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
        Search for stops matching the query with fuzzy matching.
        Handles: "40 journaux" = "quarante journaux", "pyrénées" matches any direction with pyrénées.
        """
        results = []
        lines = self.get_lines()

        # Filter lines by line query and/or destination query
        matching_lines = []
        for key, line_info in lines.items():
            line_name = line_info.get("line_name", "")
            line_code = line_info.get("line_code", "")
            dest_name = line_info.get("dest_name", "")

            # If line query specified, filter by line (fuzzy)
            if line_query:
                line_score = max(
                    fuzzy_match(line_query, line_name),
                    fuzzy_match(line_query, line_code)
                )
                if line_score < 0.5:
                    continue

            # If destination query specified, filter by destination (fuzzy)
            if dest_query:
                dest_score = fuzzy_match(dest_query, dest_name)
                if dest_score < 0.3:  # Lower threshold - "pyrénées" should match
                    continue

            matching_lines.append(line_info)

        # If no stop query, return lines only (no stop info)
        if not stop_query:
            if matching_lines:
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

        # Search stops for matching lines with fuzzy matching
        scored_results = []
        for line_info in matching_lines[:5]:  # Limit to avoid too many API calls
            line_ref = line_info.get("line_ref")
            direction_ref = line_info.get("direction_ref", -1)

            stops = self.get_stops_for_line(line_ref, direction_ref)
            
            for stop in stops:
                stop_name = stop.get("stop_name", "")
                
                # Fuzzy match stop name
                score = fuzzy_match(stop_query, stop_name)
                
                if score >= 0.3:  # Threshold for match
                    scored_results.append({
                        "score": score,
                        "stop_point_ref": stop.get("stop_point_ref"),
                        "stop_name": stop_name,
                        "line_ref": line_ref,
                        "line_name": line_info.get("line_name"),
                        "line_code": line_info.get("line_code"),
                        "direction_ref": direction_ref,
                        "dest_name": line_info.get("dest_name"),
                    })

        # Sort by score (best match first)
        scored_results.sort(key=lambda x: -x["score"])
        
        # Remove score from results
        for r in scored_results:
            r.pop("score", None)
            results.append(r)

        return results[:10]  # Return top 10 matches

    def find_line_by_query(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a line by name or code with fuzzy matching."""
        lines = self.get_lines()
        best_match = None
        best_score = 0.0

        for key, line_info in lines.items():
            line_name = line_info.get("line_name", "")
            line_code = line_info.get("line_code", "")

            score = max(
                fuzzy_match(query, line_name),
                fuzzy_match(query, line_code)
            )
            
            if score > best_score:
                best_score = score
                best_match = line_info

        return best_match if best_score >= 0.5 else None

