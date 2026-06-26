"""订单阶段自动回复配置模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class PurchaseAction(Base):
    """商品维度的订单阶段回复配置。

    stage:
    - purchase: 买家拍下，待付款
    - paid: 买家付款，待发货
    """

    __tablename__ = "xy_purchase_actions"
    __table_args__ = (
        UniqueConstraint("account_id", "item_id", "stage", name="uq_purchase_action_account_item_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="purchase", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    action_type: Mapped[str] = mapped_column(String(16), default="message")
    message_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reply_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    api_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    api_method: Mapped[str] = mapped_column(String(8), default="POST")
    api_timeout: Mapped[int] = mapped_column(Integer, default=10)
    api_params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
