"""
Migration script for the ecommerce-pipeline project.

Creates Postgres tables based on ORM models and ensures MongoDB indexes.

Usage:
    python -m scripts.migrate
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _pg_url() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "ecommerce")
    user = os.environ.get("POSTGRES_USER", "postgres")
    pwd = os.environ.get("POSTGRES_PASSWORD", "postgres")
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"


def _mongo_db():
    from pymongo import MongoClient

    host = os.environ.get("MONGO_HOST", "localhost")
    port = int(os.environ.get("MONGO_PORT", "27017"))
    db = os.environ.get("MONGO_DB", "ecommerce")
    return MongoClient(host, port)[db]


def _neo4j_driver():
    from neo4j import GraphDatabase

    host = os.environ.get("NEO4J_HOST", "localhost")
    port = os.environ.get("NEO4J_BOLT_PORT", "7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    return GraphDatabase.driver(f"bolt://{host}:{port}", auth=(user, pwd))


def migrate() -> None:
    """Create Postgres tables and MongoDB indexes for Phase 1."""
    from sqlalchemy import create_engine

    # Postgres setup
    from ecommerce_pipeline.postgres_models import Base

    pg_url = _pg_url()
    engine = create_engine(pg_url, echo=False)
    Base.metadata.create_all(engine)
    print("Postgres: created tables")

    # MongoDB setup
    mongo_db = _mongo_db()

    product_catalog = mongo_db["product_catalog"]
    product_catalog.create_index([("id", 1)], unique=True)

    order_snapshots = mongo_db["order_snapshots"]
    order_snapshots.create_index([("order_id", 1)], unique=True)

    print("MongoDB: created indexes")

    # Neo4j setup
    neo4j_driver = _neo4j_driver()
    try:
        with neo4j_driver.session() as session:
            session.run(
                "CREATE CONSTRAINT product_id IF NOT EXISTS "
                "FOR (p:Product) REQUIRE p.id IS UNIQUE"
            )
        print("Neo4j: created product_id uniqueness constraint")
    finally:
        neo4j_driver.close()


if __name__ == "__main__":
    migrate()