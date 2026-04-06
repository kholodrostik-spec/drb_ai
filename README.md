# DRB AI Route API
 
![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-brightgreen)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue)
![pgRouting](https://img.shields.io/badge/pgRouting-3.0+-orange)
![PostGIS](https://img.shields.io/badge/PostGIS-3.0+-green)
![License](https://img.shields.io/badge/license-MIT-green)
 
## About the project
 
**DRB AI Route API** is a Python FastAPI backend for AI-powered route planning across Ireland. It uses **pgRouting** and **PostGIS** for real road network pathfinding, and includes an ML layer that learns from user reviews to personalise route recommendations.
 
The API works alongside the existing Spring Boot backend (DrbMVP) — they share the same PostgreSQL database.
 
---
 
## Table of Contents
 
1. [Tech Stack](#tech-stack)
2. [Requirements](#requirements)
3. [How to run](#how-to-run)
4. [Database Setup](#database-setup)
5. [Configuration](#configuration)
6. [API Overview](#api-overview)
7. [Route Types](#route-types)
8. [Swagger UI](#swagger-ui)
 
---
 
## Tech Stack
 
- **Python 3.11+**
- **FastAPI** — async REST API
- **psycopg2** — PostgreSQL driver
- **pgRouting** — SQL-based graph pathfinding (Dijkstra)
- **PostGIS** — spatial queries (ST_DWithin, ST_MakePoint)
- **Uvicorn** — ASGI server
 
---
 
## Requirements
 
- Python 3.11+
- PostgreSQL 15+ with **PostGIS** and **pgRouting** extensions
- Existing `drbdb` database (shared with DrbMVP Spring Boot backend)
- Road network data loaded into `road_network` table (via osm2pgrouting or similar)
 
---
 
## How to run
 
**1. Clone the repository and navigate to the project folder**
```bash
cd drb_ai
```
 
**2. Install Python dependencies**
```bash
pip install fastapi uvicorn psycopg2-binary
```
 
**3. Configure the database connection in `app.py`**
```python
DB_CONNECTION = "postgresql://YOUR_USER:YOUR_PASSWORD@localhost/drbdb"
```
 
**4. Start the server**
 
On Windows (PowerShell):
```bash
python -m uvicorn app:app --reload --port 8000
```
 
On Linux/macOS:
```bash
uvicorn app:app --reload --port 8000
```
 
**5. Verify the server is running**
```
http://localhost:8000/health
```
 
Expected response:
```json
{
  "status": "ok",
  "components": {
    "db": "connected",
    "engine": "ready",
    "ml": "ready"
  }
}
```
 
---
 
## Database Setup
 
This project shares the `drbdb` PostgreSQL database with the Spring Boot backend. The following extensions and tables must exist.
 
### Enable extensions
 
Run in pgAdmin or psql inside the `drbdb` database:
 
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
```
 
### Required tables
 
The Spring Boot backend (DrbMVP) creates most tables automatically via Liquibase migrations. The following additional tables are needed for the route engine:
 
```sql
-- Road network (populated via osm2pgrouting or manual import)
CREATE TABLE road_network (
    rid SERIAL,
    name TEXT,
    highway TEXT,
    cost FLOAT,
    reverse_cost FLOAT,
    source INT,
    target INT,
    geom GEOMETRY(LineString, 4326)
);
 
-- Routable vertices (generated from road_network)
CREATE TABLE routable_vertices AS
SELECT source AS id,
       ST_StartPoint(geom) AS geom
FROM road_network
GROUP BY source, ST_StartPoint(geom);
```
 
### Verify tables exist
 
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```
 
Expected tables: `locations`, `reviews`, `users`, `api_logs`, `road_network`, `routable_vertices`.
 
---
 
## Configuration
 
All configuration is done in `app.py`:
 
```python
DB_CONNECTION = "postgresql://USER:PASSWORD@HOST/DATABASE"
```
 
| Parameter | Example | Description |
|-----------|---------|-------------|
| `USER` | `postgres` | PostgreSQL username |
| `PASSWORD` | `yourpassword` | PostgreSQL password |
| `HOST` | `localhost` | Database host |
| `DATABASE` | `drbdb` | Database name |
 
> ⚠️ Do not commit real credentials to Git. Use environment variables in production.
 
---
 
## API Overview
 
Base URL: `http://localhost:8000`
 
### Routes
 
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/routes/find` | Find 3 route variants (fast, balanced, scenic) |
| `GET` | `/api/routes/fastest` | Find the single fastest route |
| `POST` | `/api/routes/feedback` | Submit route rating for ML training |
 
### Reviews & ML
 
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/reviews` | Submit a location review (primary ML signal) |
| `GET` | `/api/users/{user_id}/insights` | Get user preference analytics |
 
### System
 
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check for db, engine, and ML |
 
---
 
## Route Types
 
The `/api/routes/find` endpoint always returns three route variants:
 
| Type | Max time increase | Stops | Priority |
|------|------------------|-------|----------|
| `fast` | +10% | up to 1 | Travel time |
| `balanced` | +40% | 2–3 | Mix of time and rating |
| `scenic` | +100% | up to 6 | Interesting locations |
 
### Example request
 
```
GET /api/routes/find?start_id=1&end_id=5&transport=car&user_id=42
```
 
### Transport modes
 
| Value | Description |
|-------|-------------|
| `car` | All roads except footways and cycleways |
| `bike` | Cycleways, residential, paths |
| `walk` | All roads except motorways |
 
---
 
## Swagger UI
 
Interactive API documentation is available at:
 
```
http://localhost:8000/docs
```
 
Use **Try it out** on any endpoint to test requests directly in the browser.
 
---
 
## Project structure
 
```
drb_ai/
├── app.py           # FastAPI application, endpoints, lifespan
├── data_access.py   # All database queries (pgRouting, PostGIS)
├── route_engine.py  # Route finding logic (Dijkstra via pgr_dijkstra)
├── ml_trainer.py    # Online ML from reviews and route selections
├── models.py        # Dataclasses: Location, Route, Review, etc.
└── README.md
```
 
---
 
## Relation to DrbMVP (Spring Boot backend)
 
Both backends connect to the same `drbdb` PostgreSQL database:
 
| Backend | Port | Responsibility |
|---------|------|----------------|
| DrbMVP (Spring Boot) | 8080 | Auth, users, reviews, photos, locations CRUD |
| DRB AI Route (FastAPI) | 8000 | Route planning, ML personalisation |
 
The FastAPI backend reads `locations` and `reviews` written by the Spring Boot backend, and writes route logs to `api_logs`.
 
---
 
## License
 
MIT License — see [LICENSE](LICENSE) for details.
 
