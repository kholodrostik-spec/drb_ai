"""
Data models for AI Route System using pgRouting.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum


class TransportMode(Enum):
    """Transport modes with different road accessibility."""
    WALK = "walk"
    BIKE = "bike"
    CAR = "car"


class RouteType(Enum):
    """Three routing strategies."""
    FAST = "fast"
    BALANCED = "balanced"
    SCENIC = "scenic"


@dataclass
class Location:
    id: int
    name: str
    description: Optional[str]
    category: Optional[str]
    latitude: float
    longitude: float
    nearest_road_name: Optional[str]
    nearest_road_highway: Optional[str]
    nearest_road_distance: Optional[float]
    is_default: bool
    rating: Optional[float]


@dataclass
class Street:
    osm_id: int
    name: Optional[str]
    highway: str
    length_m: float
    region: Optional[str]
    from_node: Optional[int]
    to_node: Optional[int]


@dataclass
class Review:
    """
    User rating for a location.
    Maps to: reviews(location_id, user_id, rating, comment, created_at)
    """
    location_id: int
    user_id: int
    rating: float
    comment: Optional[str]
    created_at: Optional[str]


@dataclass
class RouteSegment:
    """Single segment between two locations."""
    from_location: Location
    to_location: Location
    transport: TransportMode
    travel_time: float  # minutes
    distance: float  # meters
    street_name: Optional[str]
    highway_type: Optional[str]


@dataclass
class Route:
    """Complete route with all metrics."""
    route_type: RouteType
    segments: List[RouteSegment]
    locations: List[Location]  # All stops including start/end
    total_time: float  # minutes including visits
    travel_time_only: float  # minutes of actual travel
    total_distance: float  # meters
    total_rating: float  # sum of intermediate location ratings
    interestingness_score: float  # 0-100
    efficiency_score: float  # rating per minute
    visit_time_total: float  # minutes spent visiting

@dataclass
class ApiLog:
    id: Optional[int]
    user_email: Optional[str]  # НЕ user_id — у тебе email
    method: str
    path: str
    query_params: Optional[str]
    request_body: Optional[str]
    status: Optional[int]
    duration_ms: Optional[int]
    created_at: Optional[str]