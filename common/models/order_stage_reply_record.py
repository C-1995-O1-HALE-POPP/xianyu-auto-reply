"""订单阶段自动回复发送记录。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class OrderStageReplyRecord(Base):
    """记录订单阶段回复是否已成功发送。"""

    __tablename__ = "xy_order_stage_reply_records"
    __table_args__ = (
        UniqueConstraint("account_id", "stage", "order_no", name="uq_order_stage_reply_account_stage_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    send_status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    send_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
