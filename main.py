import math
import random

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

app = FastAPI(title="Walk Route Generator")
templates = Jinja2Templates(directory="templates")

WALKING_SPEED_KMH = 5.0
OSRM_BASE = "https://router.project-osrm.org/route/v1/foot"


class RouteRequest(BaseModel):
    lat: float
    lon: float
    duration_minutes: int

    @field_validator("duration_minutes")
    @classmethod
    def valid_duration(cls, v: int) -> int:
        if v not in (15, 30, 45, 60, 90):
            raise ValueError("duration_minutes must be one of 15, 30, 45, 60, 90")
        return v

    @field_validator("lat")
    @classmethod
    def valid_lat(cls, v: float) -> float:
        if not (-90 <= v <= 90):
            raise ValueError("Invalid latitude")
        return v

    @field_validator("lon")
    @classmethod
    def valid_lon(cls, v: float) -> float:
        if not (-180 <= v <= 180):
            raise ValueError("Invalid longitude")
        return v


def _generate_waypoints(
    lat: float, lon: float, target_km: float, n: int
) -> list[tuple[float, float]]:
    """Generate n random waypoints around (lat, lon) forming a rough circle."""
    km_per_lat = 111.0
    km_per_lon = 111.0 * math.cos(math.radians(lat))

    # Radius so the full loop ≈ target_km
    radius_km = target_km / (2 * math.pi) * 1.4
    radius_km = max(0.2, min(radius_km, 4.0))

    points: list[tuple[float, float]] = []
    base_angle = random.uniform(0, 360)

    for i in range(n):
        angle_deg = base_angle + (360 / n) * i + random.uniform(-20, 20)
        angle_rad = math.radians(angle_deg)
        dist = radius_km * random.uniform(0.75, 1.0)

        d_lat = (dist * math.cos(angle_rad)) / km_per_lat
        d_lon = (dist * math.sin(angle_rad)) / km_per_lon

        points.append((lat + d_lat, lon + d_lon))

    return points


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate-route")
async def generate_route(req: RouteRequest) -> dict:
    target_km = (req.duration_minutes / 60) * WALKING_SPEED_KMH

    # More waypoints for longer walks
    n_waypoints = 3 if req.duration_minutes <= 20 else 4 if req.duration_minutes <= 45 else 5

    waypoints = _generate_waypoints(req.lat, req.lon, target_km, n_waypoints)

    # Build OSRM coordinate string: lon,lat pairs, close the loop back to start
    coords = [(req.lon, req.lat)] + [(lon, lat) for lat, lon in waypoints] + [(req.lon, req.lat)]
    coords_str = ";".join(f"{lon},{lat}" for lon, lat in coords)

    url = f"{OSRM_BASE}/{coords_str}?overview=full&geometries=geojson"

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Сервис маршрутов не отвечает — попробуйте ещё раз")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"OSRM error: {e.response.status_code}")

    if data.get("code") != "Ok" or not data.get("routes"):
        raise HTTPException(status_code=500, detail="Не удалось построить маршрут")

    route = data["routes"][0]
    actual_km = route["distance"] / 1000
    actual_min = route["duration"] / 60

    return {
        "geometry": route["geometry"],
        "distance_km": round(actual_km, 2),
        "duration_min": round(actual_min),
        "waypoints": [{"lat": lat, "lon": lon} for lat, lon in waypoints],
    }


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
