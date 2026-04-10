"""
ML trainer for route personalization.
Trains on:
  1. Location reviews (primary signal)
  2. Car route profile selections (secondary signal — which profile user chose)
"""

from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from data_access import RouteDataAccess


# Valid car route profiles (matches Spring Boot CarRoutingRepository)
VALID_PROFILES = {"fastest", "safest", "simplest", "scenic", "balanced"}


class RouteMLTrainer:
    """Online learning from location reviews and route profile selections."""

    def __init__(self, data_access: RouteDataAccess):
        self.db = data_access
        self.user_profiles: Dict[int, Dict] = {}
        # Global category weights — affect location ranking in nearby results
        self.global_category_weights: Dict[str, float] = defaultdict(lambda: 1.0)

    # ─── Primary signal: location review ─────────────────────────────────────

    def train_on_review(self, user_id: int, location_id: int, rating: float):
        """
        User rated a specific location.
        Updates per-category weights both globally and per-user.
        """
        location = self.db.get_location_by_id(location_id)
        if not location or not location.category:
            return

        category = location.category

        # Update global category weights
        if rating > 3.5:
            self.global_category_weights[category] *= 1.15
        elif rating < 2.5:
            self.global_category_weights[category] *= 0.85
        self.global_category_weights[category] = max(
            0.5, min(2.0, self.global_category_weights[category]))

        # Update per-user profile
        profile = self._get_or_init_profile(user_id)
        profile['category_ratings'][category].append(rating)
        profile['category_preferences'][category] = (
            sum(profile['category_ratings'][category]) /
            len(profile['category_ratings'][category])
        )
        profile['total_reviews'] += 1

    # ─── Secondary signal: car route profile selection ────────────────────────

    def train_on_profile_selection(self, user_id: int, profile_name: str,
                                   rating: float):
        """
        User selected a specific car route profile (fastest/safest/etc.)
        and optionally rated it.
        Updates profile preference weights for this user.
        """
        if profile_name not in VALID_PROFILES:
            return

        profile = self._get_or_init_profile(user_id)

        history = profile['profile_history']
        if profile_name not in history:
            history[profile_name] = []
        history[profile_name].append(rating)

        # Compute rolling average preference per profile
        profile['profile_preferences'][profile_name] = (
            sum(history[profile_name]) / len(history[profile_name])
        )
        profile['total_profile_selections'] += 1

        # Persist to DB so training survives restarts
        self.db.save_profile_selection(user_id, profile_name, rating)

    # ─── Insights ─────────────────────────────────────────────────────────────

    def get_user_insights(self, user_id: int) -> Dict:
        """Return ML-derived analytics for a user."""
        if user_id not in self.user_profiles:
            return {"status": "new_user"}

        p = self.user_profiles[user_id]
        top_cats = sorted(
            p['category_preferences'].items(), key=lambda x: x[1], reverse=True
        )[:5]

        # Preferred car profile = highest average rating
        preferred_profile = None
        if p['profile_preferences']:
            preferred_profile = max(
                p['profile_preferences'], key=p['profile_preferences'].get
            )

        return {
            "total_reviews": p['total_reviews'],
            "total_profile_selections": p['total_profile_selections'],
            "top_categories": [
                {"category": c, "preference": round(r, 2)}
                for c, r in top_cats
            ],
            "profile_preferences": {
                k: round(v, 2) for k, v in p['profile_preferences'].items()
            },
            "preferred_car_profile": preferred_profile,
            "exploration_score": min(
                100,
                p['total_profile_selections'] * 3 + p['total_reviews'] * 10
            )
        }

    def get_recommended_profile(self, user_id: int) -> str:
        """
        Return the car route profile most likely to satisfy this user.
        Falls back to 'balanced' for new users.
        """
        if user_id not in self.user_profiles:
            return "balanced"

        prefs = self.user_profiles[user_id].get('profile_preferences', {})
        if not prefs:
            return "balanced"

        return max(prefs, key=prefs.get)

    # ─── Global category weights (used by Spring for location ranking) ─────────

    def get_global_weights(self) -> Dict[str, float]:
        return dict(self.global_category_weights)

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _get_or_init_profile(self, user_id: int) -> Dict:
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = {
                'category_ratings': defaultdict(list),
                'category_preferences': {},
                'profile_history': {},          # profile_name -> [ratings]
                'profile_preferences': {},       # profile_name -> avg rating
                'total_reviews': 0,
                'total_profile_selections': 0,
            }
        return self.user_profiles[user_id]