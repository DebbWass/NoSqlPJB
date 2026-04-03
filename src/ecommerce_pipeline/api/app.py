"""
FastAPI application entry point.

Start the server:
    uv run serve-api

Open API docs at: http://localhost:8000/docs
"""

import os

from fastapi import FastAPI
import uvicorn

from ecommerce_pipeline.db import create_tables
from ecommerce_pipeline.api.routes import products, orders, customers, analytics

app = FastAPI(
    title="E-Commerce Polyglot Data Pipeline",
    description=(
        "Capstone project API. Implements polyglot persistence across "
        "PostgreSQL, MongoDB, Redis, and Neo4j."
    ),
    version="0.1.0",
)

app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(customers.router, prefix="/customers", tags=["customers"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])


@app.on_event("startup")
def startup() -> None:
    """Create Postgres tables on startup (safe to run repeatedly)."""
    create_tables()


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


def main() -> None:
    """Run the API using the project's installed entrypoint."""
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    reload_enabled = os.environ.get("API_RELOAD", "true").lower() in {"1", "true", "yes", "on"}
    uvicorn.run("ecommerce_pipeline.api.app:app", host=host, port=port, reload=reload_enabled)
