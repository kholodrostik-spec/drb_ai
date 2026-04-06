"""
FastAPI application for AI Route System with pgRouting.
"""

from fastapi import FastAPI, Query, HTTPException, Depends
from typing import Optional
import time
from contextlib import asynccontextmanager

from models import TransportMode, RouteType
from data_access import RouteDataAccess
from route_engine import RouteEngine
from ml_trainer import RouteMLTrainer

DB_CONNECTION = "postgresql://postgres:rost@localhost/drbdb"

db = None
engine = None
ml_trainer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, engine, ml_trainer
    db = RouteDataAccess(DB_CONNECTION)
    engine = RouteEngine(db)
    ml_trainer = RouteMLTrainer(db)
    yield
    if db:
        db.conn.close()

app = FastAPI(title="AI Route API (pgRouting)", version="2.0.0", lifespan=lifespan)

def get_db():
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return db

def get_engine():
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    return engine

def get_ml():
    if ml_trainer is None:
        raise HTTPException(status_code=503, detail="ML not ready")
    return ml_trainer

@app.get("/api/routes/find")
async def find_routes(
    start_id: int = Query(..., description="ID з таблиці locations"),
    end_id: int = Query(..., description="ID з таблиці locations"),
    transport: str = Query("car"),
    user_id: Optional[int] = Query(None),
    db_access: RouteDataAccess = Depends(get_db),
    route_engine: RouteEngine = Depends(get_engine),
    ml: RouteMLTrainer = Depends(get_ml)
):
    start_time = time.time()
    
    try:
        tm = TransportMode(transport)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid transport: {transport}")
    
    if start_id not in route_engine.locations:
        raise HTTPException(status_code=404, detail=f"Start {start_id} not found")
    if end_id not in route_engine.locations:
        raise HTTPException(status_code=404, detail=f"End {end_id} not found")
    
    routes = route_engine.find_three_routes(start_id, end_id, tm)
    if not routes:
        raise HTTPException(status_code=404, detail="No routes found")
    
    # Build response
    response = {
        "start": {"id": start_id, "name": route_engine.locations[start_id].name,
                  "coords": {"lat": route_engine.locations[start_id].latitude,
                            "lon": route_engine.locations[start_id].longitude}},
        "end": {"id": end_id, "name": route_engine.locations[end_id].name,
                "coords": {"lat": route_engine.locations[end_id].latitude,
                          "lon": route_engine.locations[end_id].longitude}},
        "transport": transport,
        "routes": {}
    }
    
    for rt, route in routes.items():
        response["routes"][rt.value] = {
            "type": rt.value,
            "summary": {
                "total_time_min": round(route.total_time, 1),
                "travel_time_min": round(route.travel_time_only, 1),
                "visit_time_min": round(route.visit_time_total, 1),
                "distance_km": round(route.total_distance / 1000, 2),
                "interestingness_score": round(route.interestingness_score, 1)
            },
            "stops": [{"id": loc.id, "name": loc.name, "category": loc.category,
                      "rating": loc.rating} for loc in route.locations],
            "segments_count": len(route.segments)
        }
    
    user_email = None
    if user_id:
        user_email = db_access.get_user_email_by_id(user_id)
    
    duration_ms = int((time.time() - start_time) * 1000)
    db_access.save_route_to_api_logs(
        user_email=user_email,  # email а не id
        route=routes[RouteType.BALANCED],
        start_id=start_id,
        end_id=end_id,
        duration_ms=duration_ms
    )
    
    return response

@app.post("/api/reviews")
async def submit_review(
    location_id: int,
    user_id: int,
    rating: float = Query(..., ge=1.0, le=5.0),
    comment: Optional[str] = None,
    dba: RouteDataAccess = Depends(get_db),
    ml: RouteMLTrainer = Depends(get_ml)
):
    """Submit location review - primary ML training signal."""
    if not dba.get_location_by_id(location_id):
        raise HTTPException(status_code=404, detail="Location not found")
    
    dba.save_review(location_id, user_id, rating, comment)
    ml.train_on_review(user_id, location_id, rating)
    
    return {
        "status": "success",
        "updated_weights": dict(ml.global_category_weights)
    }

@app.post("/api/routes/feedback")
async def route_feedback(
    user_id: int,
    route_type: str,
    transport: str,
    start_id: int,
    end_id: int,
    rating: float = Query(..., ge=1.0, le=5.0),
    eng: RouteEngine = Depends(get_engine),
    ml: RouteMLTrainer = Depends(get_ml)
):
    """Submit route feedback for secondary training."""
    try:
        rt = RouteType(route_type)
        tm = TransportMode(transport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    routes = eng.find_three_routes(start_id, end_id, tm)
    if rt not in routes:
        raise HTTPException(status_code=404, detail="Route type not found")
    
    ml.train_on_route_selection(user_id, routes[rt], rating)
    
    return {"status": "success", "preferences": ml.get_user_insights(user_id)}

@app.get("/api/routes/fastest")
async def fastest_route(
    start_id: int, end_id: int, transport: str = "car",
    eng: RouteEngine = Depends(get_engine)
):
    """Legacy endpoint for backward compatibility."""
    try:
        tm = TransportMode(transport)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid transport")
    
    route = eng.find_fastest_route(start_id, end_id, tm)
    if not route:
        raise HTTPException(status_code=404, detail="No route")
    
    return {
        "path": [loc.id for loc in route.locations],
        "time_min": route.total_time,
        "distance_m": route.total_distance
    }

@app.get("/api/users/{user_id}/insights")
async def user_insights(user_id: int, ml: RouteMLTrainer = Depends(get_ml)):
    """Get user preference analytics."""
    return {"user_id": user_id, **ml.get_user_insights(user_id)}

@app.get("/health")
async def health():
    return {"status": "ok", "components": {
        "db": "connected" if db else "disconnected",
        "engine": "ready" if engine else "not_ready",
        "ml": "ready" if ml_trainer else "not_ready"
    }}