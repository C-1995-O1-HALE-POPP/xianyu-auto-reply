"""订单拍下后自动改价配置模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class OrderPriceAdjustment(Base):
    """商品维度的拍下后改价配置。"""

    __tablename__ = "xy_order_price_adjustments"
    __table_args__ = (
        UniqueConstraint("account_id", "item_id", name="uq_order_price_adjust_account_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    target_item_price: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    target_post_fee: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    override_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    override_timeout: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
