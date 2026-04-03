"""
SQLAlchemy ORM models.

Define your database tables here using the SQLAlchemy 2.0 declarative API.
Every class you define here that inherits from Base will become a table
when `Base.metadata.create_all(engine)` is called at startup.

Useful imports are already provided below. Add more as needed.

Documentation:
    https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
"""

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from typing import List, Optional
from datetime import datetime
from enum import Enum


class Base(DeclarativeBase):
    pass


class OrderStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    
    # קשר להזמנות - עוזר בשליפות ORM
    orders: Mapped[List["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # אילוץ קריטי: המלאי לעולם לא יכול להיות שלילי ברמת ה-DB
    stock_quantity: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("stock_quantity >= 0", name="check_stock_not_negative"),
        nullable=False
    )

    order_items: Mapped[List["OrderItem"]] = relationship(back_populates="product")


class ProductElectronics(Base):
    __tablename__ = "product_electronics"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    cpu: Mapped[Optional[str]] = mapped_column(String(100))
    ram_gb: Mapped[Optional[int]] = mapped_column(Integer)
    storage_gb: Mapped[Optional[int]] = mapped_column(Integer)
    screen_inches: Mapped[Optional[float]] = mapped_column(Numeric(4, 2))


class ProductClothing(Base):
    __tablename__ = "product_clothing"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    material: Mapped[Optional[str]] = mapped_column(String(100))
    
    sizes: Mapped[List["ClothingSize"]] = relationship(back_populates="clothing", cascade="all, delete-orphan")
    colors: Mapped[List["ClothingColor"]] = relationship(back_populates="clothing", cascade="all, delete-orphan")


class ClothingSize(Base):
    __tablename__ = "clothing_sizes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clothing_id: Mapped[int] = mapped_column(ForeignKey("product_clothing.product_id"), nullable=False)
    size: Mapped[str] = mapped_column(String(20), nullable=False)
    
    clothing: Mapped["ProductClothing"] = relationship(back_populates="sizes")


class ClothingColor(Base):
    __tablename__ = "clothing_colors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clothing_id: Mapped[int] = mapped_column(ForeignKey("product_clothing.product_id"), nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    
    clothing: Mapped["ProductClothing"] = relationship(back_populates="colors")


class ProductBooks(Base):
    __tablename__ = "product_books"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    isbn: Mapped[Optional[str]] = mapped_column(String(20))
    author: Mapped[Optional[str]] = mapped_column(String(255))
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    genre: Mapped[Optional[str]] = mapped_column(String(100))


class ProductFood(Base):
    __tablename__ = "product_food"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    weight_g: Mapped[Optional[int]] = mapped_column(Integer)
    organic: Mapped[Optional[bool]] = mapped_column(Boolean)
    allergens: Mapped[List["FoodAllergen"]] = relationship(back_populates="food", cascade="all, delete-orphan")


class FoodAllergen(Base):
    __tablename__ = "food_allergens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("product_food.product_id"), nullable=False)
    allergen: Mapped[str] = mapped_column(String(100), nullable=False)

    food: Mapped["ProductFood"] = relationship(back_populates="allergens")


class ProductHome(Base):
    __tablename__ = "product_home"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    dimensions: Mapped[Optional[str]] = mapped_column(String(100))
    material: Mapped[Optional[str]] = mapped_column(String(100))
    assembly_required: Mapped[Optional[bool]] = mapped_column(Boolean)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    
    # שימוש ב-Server Default מבטיח שהזמן ייקבע ע"י בסיס הנתונים ולא האפליקציה
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status"),
        default=OrderStatus.PENDING,
        nullable=False,
    )

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[List["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # שמירת המחיר ההיסטורי ברמת השורה - קריטי ל-Data Integrity
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="order_items")


