"""
Machine learning for route personalization.
Trains on reviews (location_id, user_id, rating) not routes.
"""

from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from models import Route, RouteType, TransportMode, Location
from data_access import RouteDataAccess


class RouteMLTrainer:
    """Online learning from location reviews."""
    
    def __init__(self, data_access: RouteDataAccess):
        self.db = data_access
        self.user_profiles: Dict[int, Dict] = {}
        self.global_category_weights: Dict[str, float] = defaultdict(lambda: 1.0)
    
    def train_on_review(self, user_id: int, location_id: int, rating: float):
        """
        Primary training method: user rates a specific location.
        Updates category weights based on location category.
        """
        location = self.db.get_location_by_id(location_id)
        if not location or not location.category:
            return
        
        category = location.category
        
        # Update global weights
        if rating > 3.5:
            self.global_category_weights[category] *= 1.15
        elif rating < 2.5:
            self.global_category_weights[category] *= 0.85
        
        self.global_category_weights[category] = max(
            0.5, min(2.0, self.global_category_weights[category]))
        
        # Update user profile
        if user_id not in self.user_profiles:
            self._init_user_profile(user_id)
        
        profile = self.user_profiles[user_id]
        
        if category not in profile['category_ratings']:
            profile['category_ratings'][category] = []
        profile['category_ratings'][category].append(rating)
        
        profile['category_preferences'][category] = (
            sum(profile['category_ratings'][category]) / 
            len(profile['category_ratings'][category]))
        
        profile['total_reviews'] += 1
    
    def train_on_route_selection(self, user_id: int, route: Route,
                                    explicit_rating: Optional[float] = None):
        """Secondary: learn from which route type was selected."""
        if user_id not in self.user_profiles:
            self._init_user_profile(user_id)
        
        profile = self.user_profiles[user_id]
        route_type = route.route_type.value
        
        if route_type not in profile['route_type_history']:
            profile['route_type_history'][route_type] = []
        
        inferred = explicit_rating or self._infer_satisfaction(route)
        profile['route_type_history'][route_type].append(inferred)
        
        profile['route_type_preference'][route_type] = (
            sum(profile['route_type_history'][route_type]) /
            len(profile['route_type_history'][route_type]))
        
        # Implicit positive signal from visited locations
        for loc in route.locations[1:-1]:
            if loc.category:
                current = profile['category_preferences'].get(loc.category, 3.0)
                profile['category_preferences'][loc.category] = current * 0.9 + 3.5 * 0.1
        
        profile['total_routes'] += 1
    
    def _init_user_profile(self, user_id: int):
        """Create empty profile."""
        self.user_profiles[user_id] = {
            'category_ratings': defaultdict(list),
            'category_preferences': {},
            'route_type_history': {},
            'route_type_preference': {},
            'preferred_time_range': (30, 60),
            'total_reviews': 0,
            'total_routes': 0
        }
    
    def _infer_satisfaction(self, route: Route) -> float:
        """Infer rating from route efficiency."""
        return min(5.0, max(1.0, 3.0 + route.efficiency_score * 4))
    
    def get_personalized_scores(self, user_id: int,
                                routes: Dict[RouteType, Route]) -> Dict[RouteType, float]:
        """Predict route type preference."""
        if user_id not in self.user_profiles:
            return {rt: 1.0/3 for rt in routes.keys()}
        
        profile = self.user_profiles[user_id]
        scores = {}
        
        for route_type, route in routes.items():
            score = 0
            
            # Route type preference
            type_pref = profile['route_type_preference'].get(route_type.value, 3.0)
            score += (type_pref - 3) * 10
            
            # Category preferences
            for loc in route.locations[1:-1]:
                if loc.category:
                    cat_pref = profile['category_preferences'].get(loc.category, 3.0)
                    score += (cat_pref - 3) * 2
            
            # Time fit
            pmin, pmax = profile['preferred_time_range']
            if route.total_time < pmin:
                score -= (pmin - route.total_time) / 10
            elif route.total_time > pmax:
                score -= (route.total_time - pmax) / 5
            
            # Global weights
            for loc in route.locations[1:-1]:
                if loc.category:
                    score += (self.global_category_weights[loc.category] - 1.0) * 5
            
            scores[route_type] = max(0, score + 50)
        
        total = sum(scores.values())
        return {k: v/total for k, v in scores.items()} if total > 0 else {k: 1/3 for k in scores}
    
    def get_recommendations(self, user_id: int, routes: Dict[RouteType, Route]
                           ) -> List[Tuple[RouteType, float, str]]:
        """Get ranked recommendations with explanations."""
        scores = self.get_personalized_scores(user_id, routes)
        
        recommendations = []
        for route_type, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            explanation = self._generate_explanation(user_id, route_type, routes[route_type])
            recommendations.append((route_type, score, explanation))
        
        return recommendations
    
    def _generate_explanation(self, user_id: int, route_type: RouteType,
                              route: Route) -> str:
        """Generate human-readable recommendation reason."""
        if user_id not in self.user_profiles:
            return "Default recommendation"
        
        profile = self.user_profiles[user_id]
        reasons = []
        
        # Route type preference
        type_pref = profile['route_type_preference'].get(route_type.value, 0)
        if isinstance(type_pref, (int, float)) and type_pref > 4:
            reasons.append(f"You prefer {route_type.value} routes")
        
        # Categories
        liked = []
        for loc in route.locations[1:-1]:
            if loc.category:
                pref = profile['category_preferences'].get(loc.category, 3.0)
                if pref > 4:
                    liked.append(loc.category)
        
        if liked:
            reasons.append(f"Includes your favorites: {', '.join(list(set(liked))[:3])}")
        
        # Time fit
        pmin, pmax = profile['preferred_time_range']
        if pmin <= route.total_time <= pmax:
            reasons.append(f"Matches your {pmin:.0f}-{pmax:.0f} min preference")
        
        return "; ".join(reasons) if reasons else "Balanced option"
    
    def get_user_insights(self, user_id: int) -> Dict:
        """Get user analytics."""
        if user_id not in self.user_profiles:
            return {"status": "new_user"}
        
        p = self.user_profiles[user_id]
        top_cats = sorted(p['category_preferences'].items(), 
                         key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "total_reviews": p['total_reviews'],
            "total_routes": p['total_routes'],
            "top_categories": [{"category": c, "preference": round(r, 2)} 
                              for c, r in top_cats],
            "exploration_score": min(100, p['total_routes'] * 5 + p['total_reviews'] * 10)
        }