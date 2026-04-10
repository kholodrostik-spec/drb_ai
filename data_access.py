"""
Database access layer — ML and location data only.
Car routing is handled by Spring Boot.
"""

import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional
from models import Location, Review


class RouteDataAccess:
    """Handles database operations needed for ML training and location data."""

    def __init__(self, db_connection_string: str):
        self.conn = psycopg2.connect(db_connection_string)

    # ─── Locations ────────────────────────────────────────────────────────────

    def get_all_locations(self) -> Dict[int, Location]:
        """Load all locations with average rating."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT
                l.id, l.name, l.description, l.category,
                l.latitude, l.longitude,
                l.nearest_road_name, l.nearest_road_highway,
                l.nearest_road_distance, l.is_default,
                COALESCE(AVG(r.rating), 0) AS avg_rating
            FROM locations l
            LEFT JOIN reviews r ON l.id = r.location_id
            GROUP BY l.id, l.name, l.description, l.category,
                     l.latitude, l.longitude, l.nearest_road_name,
                     l.nearest_road_highway, l.nearest_road_distance,
                     l.is_default
        """)

        locations = {}
        for row in cursor.fetchall():
            loc = Location(
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
                rating=round(float(row['avg_rating']), 2) if row['avg_rating'] else 0.0
            )
            locations[loc.id] = loc
        return locations

    def get_location_by_id(self, location_id: int) -> Optional[Location]:
        """Fetch single location by ID with rating."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT l.*, COALESCE(AVG(r.rating), 0) AS avg_rating
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
            is_default=row['is_default'],
            rating=round(float(row['avg_rating']), 2)
        )

    def get_user_email_by_id(self, user_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    # ─── Reviews ──────────────────────────────────────────────────────────────

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
        """Get average rating per category for a user — used for ML cold start."""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT l.category, AVG(r.rating) AS avg_rating
            FROM reviews r
            JOIN locations l ON r.location_id = l.id
            WHERE r.user_id = %s AND l.category IS NOT NULL
            GROUP BY l.category
        """, (user_id,))
        return {
            row['category']: round(float(row['avg_rating']), 2)
            for row in cursor.fetchall() if row['category']
        }

    # ─── Route profile history (for ML training on car profiles) ─────────────

    def save_profile_selection(self, user_id: int, profile: str, rating: float):
        """
        Persist which car route profile the user selected and rated.
        Used for ML training persistence across restarts.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO api_logs
                (user_email, method, path, query_params, request_body, status, duration_ms, created_at)
            VALUES (
                (SELECT email FROM users WHERE id = %s),
                'POST', '/api/routes/feedback',
                %s, %s, 200, 0, NOW()
            )
        """, (
            user_id,
            f'profile={profile}',
            f'{{"profile":"{profile}","rating":{rating},"user_id":{user_id}}}'
        ))
        self.conn.commit()