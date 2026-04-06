"""
Database access layer using pgRouting and PostGIS.
"""

import psycopg2
import psycopg2.extras
import json
from typing import List, Dict, Optional, Tuple
from models import Location, Street, Review, Route, RouteType, TransportMode


class RouteDataAccess:
    """Handles all database operations for routing and ML."""
    
    def __init__(self, db_connection_string: str):
        self.conn = psycopg2.connect(db_connection_string)
    
    def get_all_locations(self) -> Dict[int, Location]:
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
        SELECT
            l.id, l.name, l.description, l.category,
            l.latitude, l.longitude,
            l.nearest_road_name, l.nearest_road_highway,
            l.nearest_road_distance, l.is_default,
            COALESCE(AVG(r.rating), 0) as avg_rating
        FROM locations l
        LEFT JOIN reviews r ON l.id = r.location_id
        GROUP BY l.id, l.name, l.description, l.category,
                 l.latitude, l.longitude, l.nearest_road_name,
                 l.nearest_road_highway, l.nearest_road_distance,
                 l.is_default
    """)
        
        locations = {}
        for row in cursor.fetchall():
            location = Location(
                id=row['id'],
                name=row['name'],
                description=row['description'],
                category=row['category'],
                latitude=row['latitude'],
                longitude=row['longitude'],
                nearest_road_name=row['nearest_road_name'],
                nearest_road_highway=row['nearest_road_highway'],
                nearest_road_distance=row['nearest_road_distance'],
                is_default=row['is_default'],
                rating=round(row['avg_rating'], 2) if row['avg_rating'] else 0
            )
            locations[location.id] = location
        
        return locations
    
    def get_streets_for_transport(self, transport: TransportMode) -> List[Street]:
        """
        Load road network from road_network table with pgRouting topology.
        Uses source/target from road_network directly (no planet_osm_ways needed).
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Highway filters by transport mode
        if transport == TransportMode.WALK:
            highway_filter = """highway NOT IN ('motorway', 'motorway_link', 
                                   'trunk', 'trunk_link')"""
        elif transport == TransportMode.BIKE:
            highway_filter = """highway IN ('primary', 'secondary', 'tertiary', 
                                   'residential', 'cycleway', 'path', 'unclassified')"""
        else:  # CAR
            highway_filter = """highway IN ('motorway', 'motorway_link', 'trunk', 
                                   'trunk_link', 'primary', 'secondary', 'tertiary', 
                                   'residential', 'unclassified', 'service')
                                   AND highway NOT IN ('footway', 'cycleway', 'path', 
                                   'steps', 'pedestrian', 'track')"""
        
        cursor.execute(f"""
            SELECT 
                rid as osm_id, name, highway, cost as length_m,
                source as from_node, target as to_node
            FROM road_network
            WHERE {highway_filter}
        """)
        
        streets = []
        for row in cursor.fetchall():
            street = Street(
                osm_id=row['osm_id'],
                name=row['name'],
                highway=row['highway'],
                length_m=row['length_m'],
                from_node=row['from_node'],
                to_node=row['to_node']
            )
            streets.append(street)
        
        return streets
    
    def find_nearest_routable_node(self, lat: float, lon: float) -> Optional[int]:
        """
        Find nearest routable node using routable_vertices table.
        This table contains only main road vertices (excludes footway, service, etc.).
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            WITH closest_edge AS (
                SELECT rn.source, rn.target, rn.geom
                FROM road_network rn
                WHERE rn.highway IN (
                    'primary', 'primary_link', 'secondary', 'secondary_link',
                    'tertiary', 'tertiary_link', 'unclassified', 'residential'
                )
                ORDER BY rn.geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 20
            ),
            best_edge AS (
                SELECT source, target
                FROM closest_edge
                ORDER BY ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                LIMIT 1
            )
            SELECT 
                CASE 
                    WHEN ST_Distance(
                        (SELECT geom FROM routable_vertices WHERE id = be.source),
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    ) < ST_Distance(
                        (SELECT geom FROM routable_vertices WHERE id = be.target),
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    )
                    THEN be.source
                    ELSE be.target
                END AS id
            FROM best_edge be
        """, (lon, lat, lon, lat, lon, lat, lon, lat))
        
        row = cursor.fetchone()
        return row[0] if row else None
    
    def get_location_by_id(self, location_id: int) -> Optional[Location]:
        """Fetch single location by ID with rating."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT l.*, COALESCE(AVG(r.rating), 0) as avg_rating
            FROM locations l
            LEFT JOIN reviews r ON l.id = r.location_id
            WHERE l.id = %s
            GROUP BY l.id
        """, (location_id,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        return Location(
            id=row['id'], name=row['name'], description=row['description'],
            category=row['category'], latitude=row['latitude'],
            longitude=row['longitude'], nearest_road_name=row['nearest_road_name'],
            nearest_road_highway=row['nearest_road_highway'],
            nearest_road_distance=row['nearest_road_distance'],
            is_default=row['is_default'], rating=round(row['avg_rating'], 2)
        )
    
    def save_review(self, location_id: int, user_id: int, 
                rating: float, comment: Optional[str] = None):
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO reviews (location_id, user_id, rating, comment, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (location_id, user_id)
        DO UPDATE SET rating = EXCLUDED.rating,
                      comment = EXCLUDED.comment,
                      created_at = NOW()
        """, (location_id, user_id, rating, comment))
        self.conn.commit()
    
    def get_user_reviews(self, user_id: int) -> List[Review]:
        """Get all reviews by a specific user."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT location_id, user_id, rating, comment, created_at
            FROM reviews WHERE user_id = %s ORDER BY created_at DESC
        """, (user_id,))
        
        return [
            Review(
                location_id=row['location_id'], user_id=row['user_id'],
                rating=row['rating'], comment=row['comment'],
                created_at=row['created_at'].isoformat() if row['created_at'] else None
            )
            for row in cursor.fetchall()
        ]
    
    def get_category_ratings_for_user(self, user_id: int) -> Dict[str, float]:
        """Get average rating per category for a user."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT l.category, AVG(r.rating) as avg_rating, COUNT(*) as review_count
            FROM reviews r
            JOIN locations l ON r.location_id = l.id
            WHERE r.user_id = %s AND l.category IS NOT NULL
            GROUP BY l.category
        """, (user_id,))
        
        return {
            row['category']: round(row['avg_rating'], 2)
            for row in cursor.fetchall() if row['category']
        }
    
    def save_route_to_api_logs(self, user_email: Optional[str],
                            route: Route, start_id: int, end_id: int,
                            duration_ms: int, status: int = 200):
        route_data = {
            "route_type": route.route_type.value,
            "start_id": start_id,
            "end_id": end_id,
            "transport": route.segments[0].transport.value if route.segments else None,
            "total_time": route.total_time,
            "total_distance": route.total_distance,
            "location_ids": [loc.id for loc in route.locations]
        }
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            INSERT INTO api_logs
            (user_email, method, path, query_params, request_body, 
            status, duration_ms, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            user_email,
            'GET',
            '/api/routes/find',
            f'start={start_id}&end={end_id}&type={route.route_type.value}',
            json.dumps(route_data),
            status,
            duration_ms
        ))
        self.conn.commit()

    def get_user_route_history(self, user_email: str, 
                                limit: int = 50) -> List[Dict]:
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT request_body, created_at, duration_ms
            FROM api_logs
            WHERE user_email = %s
            AND path = '/api/routes/find'
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_email, limit))
        
        history = []
        for row in cursor.fetchall():
            try:
                route_data = json.loads(row['request_body']) if row['request_body'] else {}
                route_data['timestamp'] = row['created_at'].isoformat() if row['created_at'] else None
                route_data['duration_ms'] = row['duration_ms']
                history.append(route_data)
            except json.JSONDecodeError:
                continue
        return history
    
    def find_nearby_locations_postgis(self, start: Location, end: Location,
                                      buffer_meters: float = 2000) -> List[int]:
        """
        Find locations within buffer distance of line between start and end.
        Uses PostGIS ST_DWithin for efficient spatial query.
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT l.id
            FROM locations l
            WHERE ST_DWithin(
                ST_SetSRID(ST_MakePoint(l.longitude, l.latitude), 4326)::geography,
                ST_MakeLine(
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                )::geography,
                %s
            )
            AND l.id NOT IN (%s, %s)
            ORDER BY COALESCE((
                SELECT AVG(r.rating) FROM reviews r WHERE r.location_id = l.id
            ), 0) DESC
        """, (start.longitude, start.latitude, 
              end.longitude, end.latitude,
              buffer_meters, start.id, end.id))
        
        return [row[0] for row in cursor.fetchall()]
    
    def get_user_email_by_id(self, user_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT email FROM users WHERE id = %s
        """, (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None