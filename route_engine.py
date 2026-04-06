"""
Core routing using pgRouting pgr_dijkstra instead of NetworkX.
No Python graph building - all routing done in PostgreSQL.
"""

from typing import List, Dict, Optional, Tuple
import math

from models import *
from data_access import RouteDataAccess


class RouteEngine:
    """
    Route engine using pgRouting for pathfinding.
    Uses SQL pgr_dijkstra with highway filters per transport mode.
    """
    
    # Average speeds km/h for travel time estimation
    SPEEDS = {
        TransportMode.WALK: 5,
        TransportMode.BIKE: 15,
        TransportMode.CAR: 40
    }
    
    def __init__(self, db_access: RouteDataAccess):
        self.db = db_access
        self.locations: Dict[int, Location] = {}
        self._load_data()
    
    def _load_data(self):
        """Load locations. No graph building - use pgRouting in SQL."""
        self.locations = self.db.get_all_locations()
    
    def _get_highway_filter(self, transport: TransportMode) -> str:
        """
        SQL filter for road_network.highway based on transport mode.
        Used in pgr_dijkstra queries.
        """
        if transport == TransportMode.CAR:
            return """highway NOT IN ('footway', 'cycleway', 'path', 'steps',
                      'pedestrian', 'track', 'bridleway', 'corridor',
                      'platform', 'construction', 'proposed', 'raceway',
                      'escape', 'ladder', 'bus_stop')"""
        elif transport == TransportMode.BIKE:
            return """highway IN ('primary', 'secondary', 'tertiary', 
                      'residential', 'cycleway', 'path', 'unclassified')"""
        else:  # WALK
            return """highway NOT IN ('motorway', 'motorway_link', 
                      'trunk', 'trunk_link')"""
    
    def _haversine_distance(self, lat1: float, lon1: float, 
                           lat2: float, lon2: float) -> float:
        """Calculate great circle distance in meters."""
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_phi / 2) ** 2 + 
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    
    def _get_route_via_pgrouting(self, start_node: int, end_node: int,
                                  transport: TransportMode) -> Optional[Tuple[List[int], float, float]]:
        """
        Execute pgr_dijkstra in database and return path with metrics.
        
        Returns: (list of edge IDs, total cost/distance, total time) or None
        """
        cursor = self.db.conn.cursor()
        highway_filter = self._get_highway_filter(transport)
        speed_ms = (self.SPEEDS[transport] * 1000) / 3600  # km/h to m/s
        
        cursor.execute(f"""
            SELECT 
                array_agg(r.edge ORDER BY r.seq) as edges,
                SUM(r.cost) as total_distance,
                SUM(r.cost) / %s as travel_time_seconds
            FROM pgr_dijkstra(
                'SELECT rid AS id, source, target, cost, reverse_cost 
                 FROM road_network 
                 WHERE {highway_filter}',
                %s, %s, true
            ) r
            WHERE r.edge != -1
        """, (speed_ms, start_node, end_node))
        
        row = cursor.fetchone()
        if not row or row[0] is None:
            return None
        
        edges = row[0]
        distance = row[1]
        time_seconds = row[2]
        
        return (edges, distance, time_seconds / 60)  # Convert to minutes
    
    def find_fastest_route(self, start_id: int, end_id: int,
                          transport: TransportMode) -> Optional[Route]:
        """
        Find shortest path using pgr_dijkstra.
        """
        start_loc = self.locations.get(start_id)
        end_loc = self.locations.get(end_id)
        
        if not start_loc or not end_loc:
            return None
        
        # Find nearest routable nodes
        start_node = self.db.find_nearest_routable_node(
            start_loc.latitude, start_loc.longitude)
        end_node = self.db.find_nearest_routable_node(
            end_loc.latitude, end_loc.longitude)
        
        if start_node is None or end_node is None:
            # Fallback to direct line
            return self._create_direct_route(start_loc, end_loc, transport)
        
        # Get route from pgRouting
        result = self._get_route_via_pgrouting(start_node, end_node, transport)
        if not result:
            return self._create_direct_route(start_loc, end_loc, transport)
        
        edges, distance, travel_time = result
        
        # Build simple route (direct, no intermediate stops)
        segment = RouteSegment(
            from_location=start_loc,
            to_location=end_loc,
            transport=transport,
            travel_time=travel_time,
            distance=distance,
            street_name=None,
            highway_type=None
        )
        
        return Route(
            route_type=RouteType.FAST,
            segments=[segment],
            locations=[start_loc, end_loc],
            total_time=travel_time,
            travel_time_only=travel_time,
            total_distance=distance,
            total_rating=0,
            interestingness_score=0,
            efficiency_score=0,
            visit_time_total=0
        )
    
    def _create_direct_route(self, start: Location, end: Location,
                            transport: TransportMode) -> Route:
        """Fallback direct route when no graph path exists."""
        distance = self._haversine_distance(
            start.latitude, start.longitude,
            end.latitude, end.longitude)
        speed = self.SPEEDS[transport]
        travel_time = (distance / 1000) / speed * 60  # minutes
        
        segment = RouteSegment(
            from_location=start, to_location=end,
            transport=transport, travel_time=travel_time,
            distance=distance, street_name=None, highway_type='direct'
        )
        
        return Route(
            route_type=RouteType.FAST,
            segments=[segment],
            locations=[start, end],
            total_time=travel_time,
            travel_time_only=travel_time,
            total_distance=distance,
            total_rating=0,
            interestingness_score=0,
            efficiency_score=0,
            visit_time_total=0
        )
    
    def find_three_routes(self, start_id: int, end_id: int,
                         transport: TransportMode) -> Dict[RouteType, Route]:
        """
        Find three route variants using different strategies.
        """
        base_route = self.find_fastest_route(start_id, end_id, transport)
        if not base_route:
            return {}
        
        base_time = base_route.travel_time_only
        
        # Find candidate locations using PostGIS
        nearby_ids = self.db.find_nearby_locations_postgis(
            self.locations[start_id], self.locations[end_id], 2000)
        
        nearby = [self.locations[i] for i in nearby_ids 
                  if i in self.locations and self.locations[i].rating]
        
        routes = {}
        routes[RouteType.FAST] = self._find_fast_variant(
            start_id, end_id, transport, base_time, nearby[:2])
        
        routes[RouteType.BALANCED] = self._find_balanced_variant(
            start_id, end_id, transport, base_time, 
            [l for l in nearby if l.rating and l.rating >= 3.5][:5])
        
        routes[RouteType.SCENIC] = self._find_scenic_variant(
            start_id, end_id, transport, base_time,
            [l for l in nearby if l.rating and l.rating >= 4.0][:8])
        
        return routes
    
    def _find_fast_variant(self, start_id: int, end_id: int,
                           transport: TransportMode,
                           base_time: float,
                           candidates: List[Location]) -> Route:
        """Fast: max 10% time increase, minimal stops."""
        max_time = base_time * 1.1
        
        # Try with at most 1 high-rated stop
        good_candidates = [c for c in candidates if c.rating and c.rating >= 4.0][:1]
        
        if good_candidates:
            route = self._build_route_with_stops(
                start_id, end_id, transport, good_candidates, max_time, RouteType.FAST)
            if route:
                return route
        
        return self.find_fastest_route(start_id, end_id, transport)
    
    def _find_balanced_variant(self, start_id: int, end_id: int,
                               transport: TransportMode,
                               base_time: float,
                               candidates: List[Location]) -> Route:
        """Balanced: up to 40% time increase, 2-3 stops."""
        max_time = base_time * 1.4
        
        for num_stops in [3, 2, 1]:
            stops = candidates[:num_stops]
            route = self._build_route_with_stops(
                start_id, end_id, transport, stops, max_time, RouteType.BALANCED)
            if route:
                return route
        
        return self.find_fastest_route(start_id, end_id, transport)
    
    def _find_scenic_variant(self, start_id: int, end_id: int,
                            transport: TransportMode,
                            base_time: float,
                            candidates: List[Location]) -> Route:
        """Scenic: up to 100% time increase, maximize interesting stops."""
        max_time = base_time * 2.0
        
        for num_stops in range(min(6, len(candidates)), 0, -1):
            stops = candidates[:num_stops]
            route = self._build_route_with_stops(
                start_id, end_id, transport, stops, max_time, RouteType.SCENIC)
            if route and route.interestingness_score > 50:
                return route
        
        return self.find_fastest_route(start_id, end_id, transport)
    
    def _build_route_with_stops(self, start_id: int, end_id: int,
                                 transport: TransportMode,
                                 stops: List[Location],
                                 max_time: float,
                                 route_type: RouteType) -> Optional[Route]:
        """Build route visiting stops in optimal order."""
        if not stops:
            return self.find_fastest_route(start_id, end_id, transport)
        
        # Order stops by greedy nearest neighbor
        ordered = self._order_stops_greedy(
            self.locations[start_id], self.locations[end_id], stops)
        
        # Build multi-segment route
        all_locs = [self.locations[start_id]] + ordered + [self.locations[end_id]]
        segments = []
        total_distance = 0
        travel_time = 0
        visit_time = 0
        
        for i in range(len(all_locs) - 1):
            from_loc, to_loc = all_locs[i], all_locs[i + 1]
            
            # Get segment via pgRouting
            from_node = self.db.find_nearest_routable_node(
                from_loc.latitude, from_loc.longitude)
            to_node = self.db.find_nearest_routable_node(
                to_loc.latitude, to_loc.longitude)
            
            if not from_node or not to_node:
                return None
            
            result = self._get_route_via_pgrouting(from_node, to_node, transport)
            if not result:
                return None
            
            _, dist, time_min = result
            
            segment = RouteSegment(
                from_location=from_loc, to_location=to_loc,
                transport=transport, travel_time=time_min,
                distance=dist, street_name=None, highway_type=None
            )
            segments.append(segment)
            total_distance += dist
            travel_time += time_min
            
            # Add visit time for intermediate stops
            if i < len(all_locs) - 2:
                visit_time += (45 if to_loc.category == 'museum' else
                              30 if to_loc.category == 'park' else 15)
        
        total_time = travel_time + visit_time
        if total_time > max_time:
            return None
        
        total_rating = sum(loc.rating or 0 for loc in ordered)
        interestingness = min(100, (total_rating / max(len(ordered), 1)) * 20 +
                             len(set(loc.category for loc in ordered if loc.category)) * 5)
        
        return Route(
            route_type=route_type,
            segments=segments,
            locations=all_locs,
            total_time=total_time,
            travel_time_only=travel_time,
            total_distance=total_distance,
            total_rating=total_rating,
            interestingness_score=interestingness,
            efficiency_score=total_rating / (total_time + 0.001),
            visit_time_total=visit_time
        )
    
    def _order_stops_greedy(self, start: Location, end: Location,
                           stops: List[Location]) -> List[Location]:
        """Order stops using greedy nearest neighbor."""
        if not stops:
            return []
        
        ordered = []
        current = start
        remaining = stops.copy()
        
        while remaining:
            nearest = min(remaining,
                         key=lambda loc: self._haversine_distance(
                             current.latitude, current.longitude,
                             loc.latitude, loc.longitude))
            ordered.append(nearest)
            remaining.remove(nearest)
            current = nearest
        
        return ordered