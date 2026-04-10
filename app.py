"""
FastAPI application — ML layer only.
Car routing is handled entirely by Spring Boot (/api/map/ai-route).
Walk routing is also handled by Spring Boot (/api/map/route).
Python handles: ML training on reviews, user insights.
"""

from fastapi import FastAPI, Query, HTTPException, Depends
from typing import Optional
from contextlib import asynccontextmanager

from models import TransportMode, RouteType
from data_access import RouteDataAccess
from ml_trainer import RouteMLTrainer

DB_CONNECTION = "postgresql://postgres:rost@localhost/drbdb"

db = None
ml_trainer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, ml_trainer
    db = RouteDataAccess(DB_CONNECTION)
    ml_trainer = RouteMLTrainer(db)
    yield
    if db:
        db.conn.close()


app = FastAPI(title="DRB ML API", version="3.0.0", lifespan=lifespan)


def get_db() -> RouteDataAccess:
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return db


def get_ml() -> RouteMLTrainer:
    if ml_trainer is None:
        raise HTTPException(status_code=503, detail="ML not ready")
    return ml_trainer


# ─── ML: train on review ──────────────────────────────────────────────────────

@app.post("/api/reviews/train")
async def train_on_review(
    location_id: int,
    user_id: int,
    rating: float = Query(..., ge=1.0, le=5.0),
    dba: RouteDataAccess = Depends(get_db),
    ml: RouteMLTrainer = Depends(get_ml)
):
    """Train ML model when a user submits a location review."""
    location = dba.get_location_by_id(location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    ml.train_on_review(user_id, location_id, rating)

    return {
        "status": "trained",
        "updated_weights": dict(ml.global_category_weights)
    }


# ─── ML: user insights ────────────────────────────────────────────────────────

@app.get("/api/users/{user_id}/insights")
async def user_insights(
    user_id: int,
    ml: RouteMLTrainer = Depends(get_ml)
):
    """Get ML-derived user preference analytics."""
    return {"user_id": user_id, **ml.get_user_insights(user_id)}


# ─── ML: route feedback ───────────────────────────────────────────────────────

@app.post("/api/routes/feedback")
async def route_feedback(
    user_id: int,
    profile: str = Query(..., description="Profile that was selected: fastest/safest/simplest/scenic/balanced"),
    rating: float = Query(..., ge=1.0, le=5.0),
    ml: RouteMLTrainer = Depends(get_ml)
):
    """
    Record which route profile the user chose and how they rated it.
    Used to improve future personalization.
    """
    ml.train_on_profile_selection(user_id, profile, rating)
    return {"status": "recorded", "insights": ml.get_user_insights(user_id)}


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "components": {
            "db": "connected" if db else "disconnected",
            "ml": "ready" if ml_trainer else "not_ready"
        }
    }