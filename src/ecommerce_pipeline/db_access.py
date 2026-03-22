"""
DBAccess — the data access layer.

This is the only file you need to implement. The web API is already wired up;
every route calls one method on this class. Your job is to replace each
`raise NotImplementedError(...)` with a real implementation.

Work through the phases in order. Read the corresponding lesson file in
materials/project/ before starting each phase.
"""

import json
import logging
from itertools import combinations

from sqlalchemy import select

from ecommerce_pipeline.postgres_models import (
    Customer,
    Product,
    Order,
    OrderItem,
)

logger = logging.getLogger(__name__)


class DBAccess:
    def __init__(
        self,
        pg_session_factory,   # sqlalchemy.orm.sessionmaker bound to Postgres engine
        mongo_db,             # pymongo.database.Database
        redis_client=None,    # redis.Redis | None  (None until Phase 2)
        neo4j_driver=None,    # neo4j.Driver | None (None until Phase 3)
    ) -> None:
        self._pg_session_factory = pg_session_factory
        self._mongo_db = mongo_db
        self._redis = redis_client
        self._neo4j = neo4j_driver

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def create_order(self, customer_id: int, items: list[dict]) -> dict:
        """Place an order atomically.

        items: [{"product_id": int, "quantity": int}, ...]

        Returns a dict with order_id, customer_id, status, total_amount,
        created_at (ISO 8601 string), and a list of items including product_name
        and unit_price.

        Raises ValueError if any product has insufficient stock. When that
        happens, no data is modified in any database.

        After the order is persisted transactionally, a denormalized snapshot
        is saved for read access, and downstream counters and graph edges are
        updated (best-effort, does not roll back the order on failure).
        """
        with self._pg_session_factory() as session:
            try:
                with session.begin():
                    order_items_prepared = []
                    total_amount = 0.0

                    for item in items:
                        p_id = item['product_id']
                        qty = item['quantity']

                        product = session.execute(
                            select(Product).filter_by(id=p_id).with_for_update()
                        ).scalar_one_or_none()

                        if not product:
                            raise ValueError(f"Product {p_id} not found")

                        if product.stock_quantity < qty:
                            raise ValueError(f"Insufficient stock for product: {product.name}")

                        product.stock_quantity -= qty
                        unit_price = float(product.price)
                        total_amount += unit_price * qty

                        order_items_prepared.append({
                            "product_id": p_id,
                            "product_name": product.name,
                            "quantity": qty,
                            "unit_price": unit_price,
                        })

                    new_order = Order(
                        customer_id=customer_id,
                        total_amount=total_amount,
                        status="completed",
                    )
                    session.add(new_order)
                    session.flush()

                    for oi in order_items_prepared:
                        session.add(OrderItem(
                            order_id=new_order.id,
                            product_id=oi["product_id"],
                            quantity=oi["quantity"],
                            unit_price=oi["unit_price"],
                        ))

                # Postgres transaction committed at this point
                customer = session.get(Customer, customer_id)
                created_at_str = new_order.created_at.isoformat()

                # MongoDB snapshot (best-effort)
                self.save_order_snapshot(
                    order_id=new_order.id,
                    customer={"id": customer.id, "name": customer.name, "email": customer.email},
                    items=order_items_prepared,
                    total_amount=total_amount,
                    status=new_order.status,
                    created_at=created_at_str,
                )

                # MongoDB product stock sync (best-effort)
                for oi in order_items_prepared:
                    try:
                        self._mongo_db.product_catalog.update_one(
                            {"id": oi["product_id"]},
                            {"$inc": {"stock_quantity": -oi["quantity"]}}
                        )
                    except Exception as exc:
                        logger.warning(f"Failed to sync stock to MongoDB for product {oi['product_id']}: {exc}")

                # Redis inventory counter update (best-effort)
                if self._redis is not None:
                    for oi in order_items_prepared:
                        key = f"inventory:{oi['product_id']}"
                        try:
                            self._redis.decrby(key, oi['quantity'])
                        except Exception as exc:
                            logger.warning(f"Failed to decrement inventory counter {key}: {exc}")

                        # Invalidate/update product cache in Redis
                        try:
                            self.invalidate_product_cache(oi["product_id"])
                        except Exception as exc:
                            logger.warning(f"Failed to invalidate cache for product {oi['product_id']}: {exc}")

                # Neo4j graph update (best-effort)
                if self._neo4j is not None:
                    try:
                        self.seed_recommendation_graph([
                            {
                                "order_id": new_order.id,
                                "product_ids": [oi["product_id"] for oi in order_items_prepared],
                            }
                        ])
                    except Exception as exc:
                        logger.warning(f"Failed to update recommendation graph: {exc}")

                return {
                    "order_id": new_order.id,
                    "customer_id": customer_id,
                    "status": new_order.status,
                    "total_amount": total_amount,
                    "created_at": created_at_str,
                    "items": order_items_prepared,
                }

            except Exception as e:
                logger.error(f"Order creation failed: {e}")
                raise

    def get_product(self, product_id: int) -> dict | None:
        """Fetch a product by its integer ID.

        Returns a dict with id, name, price, stock_quantity, category,
        description, and category_fields. Returns None if not found.

        The category_fields shape varies by category:
          electronics: {cpu, ram_gb, storage_gb, screen_inches}
          clothing:    {material, sizes, colors}
          books:       {isbn, author, page_count, genre}
          food:        {weight_g, organic, allergens}
          home:        {dimensions, material, assembly_required}
        """
        # Cache-aside in Redis (if available)
        cache_key = f"product:{product_id}"
        if self._redis is not None:
            raw = self._redis.get(cache_key)
            if raw is not None:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    self._redis.delete(cache_key)

        # Query MongoDB for the product
        product = self._mongo_db.product_catalog.find_one({"id": product_id})

        if product is None:
            return None

        payload = {
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "stock_quantity": product["stock_quantity"],
            "category": product["category"],
            "description": product.get("description"),
            "category_fields": product.get("category_fields", {}),
        }

        if self._redis is not None:
            try:
                self._redis.set(cache_key, json.dumps(payload), ex=300)
            except Exception as exc:
                logger.warning(f"Failed to write product cache {cache_key}: {exc}")

        return payload

    def search_products(
        self,
        category: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        """Search the product catalog with optional filters.

        category: exact match on the category field
        q: case-insensitive substring match on the product name
        Both filters are ANDed together. Returns all products if both are None.
        Returns a list of product dicts (same shape as get_product).
        """
        # Build MongoDB query
        query = {}
        filters = []
        
        if category is not None:
            filters.append({"category": category})
        
        if q is not None:
            # Case-insensitive substring search
            filters.append({"name": {"$regex": q, "$options": "i"}})
        
        if filters:
            if len(filters) == 1:
                query = filters[0]
            else:
                query = {"$and": filters}
        
        # Query MongoDB
        products = list(self._mongo_db.product_catalog.find(query))
        
        # Convert to the expected format
        return [{
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "stock_quantity": product["stock_quantity"],
            "category": product["category"],
            "description": product["description"],
            "category_fields": product["category_fields"]
        } for product in products]

    def save_order_snapshot(
        self,
        order_id: int,
        customer: dict,
        items: list[dict],
        total_amount: float,
        status: str,
        created_at: str,
    ) -> str:
        """Save a denormalized order snapshot for fast read access.

        customer: {"id": int, "name": str, "email": str}
        items: [{"product_id": int, "product_name": str, "quantity": int, "unit_price": float}]

        Embeds all customer and product details as they existed at the time
        of the order, so the snapshot remains accurate even if prices or
        names change later.

        Returns a string identifier for the saved document.

        Called internally by create_order after the transactional write
        commits. Not called directly by routes.
        """
        # Create denormalized snapshot document
        snapshot = {
            "order_id": order_id,
            "customer": customer,
            "items": items,
            "total_amount": total_amount,
            "status": status,
            "created_at": created_at
        }
        
        # Insert into MongoDB and return the document ID as string
        result = self._mongo_db["order_snapshots"].insert_one(snapshot)
        return str(result.inserted_id)

    def get_order(self, order_id: int) -> dict | None:
        """Fetch a single order snapshot by order_id.

        Returns the snapshot dict (order_id, customer embed, items list,
        total_amount, status, created_at) or None if not found.
        """
        # Query MongoDB for the order snapshot
        order = self._mongo_db["order_snapshots"].find_one({"order_id": order_id})
        
        # Return None if not found, otherwise return the snapshot
        if order is None:
            return None
        
        # Remove MongoDB's _id field and return the snapshot
        order.pop("_id", None)
        return order

    def get_order_history(self, customer_id: int) -> list[dict]:
        """Fetch all order snapshots for a customer.

        Returns a list of snapshot dicts sorted by created_at descending.
        Returns an empty list if the customer has no orders.
        """
        # Query MongoDB for all orders by this customer, sorted by created_at descending
        orders = list(self._mongo_db["order_snapshots"].find(
            {"customer.id": customer_id}
        ).sort("created_at", -1))  # -1 for descending
        
        # Remove MongoDB's _id field from each order
        for order in orders:
            order.pop("_id", None)
        
        return orders

    def revenue_by_category(self) -> list[dict]:
        """Compute total revenue per product category.

        Returns [{"category": str, "total_revenue": float}, ...] sorted by
        total_revenue descending.
        """
        from sqlalchemy import func
        from ecommerce_pipeline.postgres_models import Product, OrderItem
        
        with self._pg_session_factory() as session:
            # Query: Sum revenue by category
            # revenue = order_item.quantity * order_item.unit_price
            results = session.query(
                Product.category,
                func.sum(OrderItem.quantity * OrderItem.unit_price).label("total_revenue")
            ).join(
                OrderItem, Product.id == OrderItem.product_id
            ).group_by(
                Product.category
            ).order_by(
                func.sum(OrderItem.quantity * OrderItem.unit_price).desc()
            ).all()
            
            # Convert to list of dicts
            return [
                {
                    "category": category,
                    "total_revenue": float(total_revenue) if total_revenue else 0.0
                }
                for category, total_revenue in results
            ]

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def init_inventory_counters(self) -> None:
        """Seed inventory counters from current stock quantities.

        For each product, write its current stock_quantity to the counter
        store. Called at startup and after seeding products.
        """
        if self._redis is None:
            return

        from ecommerce_pipeline.postgres_models import Product

        with self._pg_session_factory() as session:
            products = session.query(Product).all()
            for product in products:
                self._redis.set(f"inventory:{product.id}", str(product.stock_quantity))

    def invalidate_product_cache(self, product_id: int) -> None:
        """Remove a product's cached entry.

        Call this after updating a product's data so the next read fetches
        fresh data from the primary store. No-op if no entry exists.
        """
        if self._redis is None:
            return

        self._redis.delete(f"product:{product_id}")

    def record_product_view(self, customer_id: int, product_id: int) -> None:
        """Record that a customer viewed a product.

        Maintains a bounded, ordered list of the customer's most recently
        viewed products (most recent first, capped at 10 entries).
        """
        if self._redis is None:
            return

        key = f"recently_viewed:{customer_id}"
        self._redis.lpush(key, str(product_id))
        self._redis.ltrim(key, 0, 9)

    def get_recently_viewed(self, customer_id: int) -> list[int]:
        """Return up to 10 recently viewed product IDs for a customer.

        Returns IDs as integers, most recently viewed first.
        Returns an empty list if no views have been recorded.
        """
        if self._redis is None:
            return []

        key = f"recently_viewed:{customer_id}"
        values = self._redis.lrange(key, 0, 9)
        if not values:
            return []

        return [int(v) for v in values]

    # ── Phase 3 ───────────────────────────────────────────────────────────────

    def seed_recommendation_graph(self, orders: list[dict]) -> None:
        """Build the co-purchase recommendation graph from order history.

        orders: [{"order_id": int, "product_ids": [int, ...]}, ...]

        For each unique pair of products in an order, creates or strengthens
        a co-purchase relationship between them. The strength increases by one
        for every order in which the pair appears together.

        Products not found in the catalog are silently skipped.
        """
        if self._neo4j is None:
            return

        # Gather product names from Postgres for node labels / graph readability
        name_by_id = {}
        with self._pg_session_factory() as session:
            for order in orders:
                for pid in set(order.get("product_ids", [])):
                    if pid in name_by_id:
                        continue
                    product = session.get(Product, pid)
                    if product:
                        name_by_id[pid] = product.name

        with self._neo4j.session() as session:
            for order in orders:
                product_ids = sorted(set(order.get("product_ids", [])))
                for pid in product_ids:
                    session.run(
                        "MERGE (p:Product {id: $id}) SET p.name = COALESCE($name, p.name)",
                        id=pid,
                        name=name_by_id.get(pid, f"Product {pid}"),
                    )

                for (a, b) in combinations(product_ids, 2):
                    session.run(
                        ""
                        "MATCH (a:Product {id: $id_a}), (b:Product {id: $id_b})\n"
                        "MERGE (a)-[r:BOUGHT_TOGETHER]-(b)\n"
                        "ON CREATE SET r.weight = 1\n"
                        "ON MATCH SET r.weight = r.weight + 1"
                        "",
                        id_a=a,
                        id_b=b,
                    )

    def get_recommendations(self, product_id: int, limit: int = 5) -> list[dict]:
        """Return product recommendations based on co-purchase patterns.

        Returns [{"product_id": int, "name": str, "score": int}, ...]
        sorted by score (co-purchase strength) descending.

        Returns an empty list if the product has no co-purchase relationships.
        """
        if self._neo4j is None:
            return []

        with self._neo4j.session() as session:
            result = session.run(
                ""
                "MATCH (p:Product {id: $id})-[r:BOUGHT_TOGETHER]-(other:Product)\n"
                "WHERE other.id <> $id\n"
                "RETURN other.id AS product_id, other.name AS name, r.weight AS score\n"
                "ORDER BY r.weight DESC\n"
                "LIMIT $limit"
                "",
                id=product_id,
                limit=limit,
            )
            records = result.data()

        return [
            {"product_id": rec["product_id"], "name": rec["name"], "score": int(rec["score"])}
            for rec in records
        ]
