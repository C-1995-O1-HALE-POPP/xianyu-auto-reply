"""订单自动改价执行记录模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class OrderPriceAdjustRecord(Base):
    """记录订单是否已执行过自动改价。"""

    __tablename__ = "xy_order_price_adjust_records"
    __table_args__ = (
        UniqueConstraint("account_id", "order_no", name="uq_order_price_adjust_account_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_item_price: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_post_fee: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    original_item_price: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    original_post_fee: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    original_total_price: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    result_status: Mapped[str] = mapped_column(String(20), nullable=False, default="failed")
    result_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
