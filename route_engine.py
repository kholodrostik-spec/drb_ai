"""
route_engine.py — retained only for backward compatibility and ML helpers.

Car routing (fastest/safest/simplest/scenic/balanced) is now fully
handled by Spring Boot via CarRoutingRepository + CarRoutingService.

This file is no longer responsible for building any routes.
It is kept only because app.py imports RouteEngine for location loading
used by the ML endpoints. It can be removed entirely once app.py
is updated to load locations directly from RouteDataAccess.
"""

from typing import Dict, Optional
import math

from models import Location, TransportMode
from data_access import RouteDataAccess


class RouteEngine:
    """
    Minimal route engine — only loads locations for ML use.
    All routing logic has been moved to Spring Boot.
    """

    def __init__(self, db_access: RouteDataAccess):
        self.db = db_access
        self.locations: Dict[int, Location] = {}
        self._load_data()

    def _load_data(self):
        """Load locations from DB for ML reference."""
        self.locations = self.db.get_all_locations()

    def _haversine_distance(self, lat1: float, lon1: float,
                            lat2: float, lon2: float) -> float:
        """Haversine distance in meters — kept for potential ML use."""
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def get_location(self, location_id: int) -> Optional[Location]:
        return self.locations.get(location_id)