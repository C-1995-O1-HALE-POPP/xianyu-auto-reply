#!/usr/bin/env python3
"""Idempotent migration for order-stage auto replies."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from common.db.session import async_engine


async def table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return bool(result.scalar())


async def column_exists(conn, table_name: str, column_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return bool(result.scalar())


async def index_exists(conn, table_name: str, index_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return bool(result.scalar())


async def ensure_column(conn, table_name: str, column_name: str, ddl: str) -> None:
    if not await column_exists(conn, table_name, column_name):
        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


async def ensure_index(conn, table_name: str, index_name: str, ddl: str) -> None:
    if not await index_exists(conn, table_name, index_name):
        await conn.execute(text(ddl))


async def main() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS xy_purchase_actions (
                    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '配置ID',
                    owner_id INT NOT NULL DEFAULT 0 COMMENT '所属系统用户ID',
                    account_id VARCHAR(80) NOT NULL COMMENT '账号标识',
                    item_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '商品ID',
                    stage VARCHAR(16) NOT NULL DEFAULT 'purchase' COMMENT '订单阶段：purchase-拍下，paid-付款',
                    enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否启用',
                    action_type VARCHAR(16) NOT NULL DEFAULT 'message' COMMENT '动作类型：message-文本，api-接口',
                    message_template TEXT COMMENT '回复内容模板',
                    reply_image VARCHAR(512) COMMENT '回复图片URL',
                    api_url VARCHAR(1024) COMMENT 'API地址',
                    api_method VARCHAR(8) NOT NULL DEFAULT 'POST' COMMENT 'API请求方法',
                    api_timeout INT NOT NULL DEFAULT 80 COMMENT 'API超时时间(秒)',
                    api_params TEXT COMMENT '兼容旧版API参数(JSON)',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    INDEX idx_pa_account_stage (account_id, stage),
                    INDEX idx_pa_account_item_stage (account_id, item_id, stage),
                    UNIQUE KEY uq_purchase_action_account_item_stage (account_id, item_id, stage)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单阶段自动回复配置表'
                """
            )
        )

        await ensure_column(
            conn,
            "xy_purchase_actions",
            "stage",
            "VARCHAR(16) NOT NULL DEFAULT 'purchase' COMMENT '订单阶段：purchase-拍下，paid-付款' AFTER item_id",
        )
        await ensure_column(
            conn,
            "xy_purchase_actions",
            "reply_image",
            "VARCHAR(512) DEFAULT NULL COMMENT '回复图片URL' AFTER message_template",
        )

        await conn.execute(text("UPDATE xy_purchase_actions SET item_id = '' WHERE item_id IS NULL"))
        await conn.execute(text("UPDATE xy_purchase_actions SET stage = 'purchase' WHERE stage IS NULL OR stage = ''"))
        await conn.execute(text("UPDATE xy_purchase_actions SET api_method = 'POST' WHERE api_method IS NULL OR api_method = ''"))
        await conn.execute(text("UPDATE xy_purchase_actions SET api_timeout = 80 WHERE api_timeout IS NULL OR api_timeout <= 0"))

        await ensure_index(
            conn,
            "xy_purchase_actions",
            "idx_pa_account_stage",
            "ALTER TABLE xy_purchase_actions ADD INDEX idx_pa_account_stage (account_id, stage)",
        )
        await ensure_index(
            conn,
            "xy_purchase_actions",
            "idx_pa_account_item_stage",
            "ALTER TABLE xy_purchase_actions ADD INDEX idx_pa_account_item_stage (account_id, item_id, stage)",
        )
        await ensure_index(
            conn,
            "xy_purchase_actions",
            "uq_purchase_action_account_item_stage",
            "ALTER TABLE xy_purchase_actions ADD UNIQUE KEY uq_purchase_action_account_item_stage (account_id, item_id, stage)",
        )

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS xy_order_stage_reply_records (
                    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '记录ID',
                    owner_id INT NOT NULL DEFAULT 0 COMMENT '所属系统用户ID',
                    account_id VARCHAR(80) NOT NULL COMMENT '账号标识',
                    item_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '商品ID',
                    stage VARCHAR(16) NOT NULL COMMENT '订单阶段：purchase-拍下，paid-付款',
                    order_no VARCHAR(64) NOT NULL COMMENT '订单号',
                    buyer_id VARCHAR(64) DEFAULT NULL COMMENT '买家ID',
                    chat_id VARCHAR(64) DEFAULT NULL COMMENT '聊天会话ID',
                    reply_text TEXT COMMENT '发送文本',
                    reply_image VARCHAR(512) COMMENT '发送图片',
                    send_status VARCHAR(20) NOT NULL DEFAULT 'success' COMMENT '发送状态',
                    send_result TEXT COMMENT '发送结果快照',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    INDEX idx_osrr_account_stage (account_id, stage),
                    INDEX idx_osrr_order_no (order_no),
                    UNIQUE KEY uq_order_stage_reply_account_stage_order (account_id, stage, order_no)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单阶段自动回复发送记录表'
                """
            )
        )

        if await table_exists(conn, "xy_order_stage_reply_records"):
            await ensure_index(
                conn,
                "xy_order_stage_reply_records",
                "idx_osrr_account_stage",
                "ALTER TABLE xy_order_stage_reply_records ADD INDEX idx_osrr_account_stage (account_id, stage)",
            )
            await ensure_index(
                conn,
                "xy_order_stage_reply_records",
                "idx_osrr_order_no",
                "ALTER TABLE xy_order_stage_reply_records ADD INDEX idx_osrr_order_no (order_no)",
            )
            await ensure_index(
                conn,
                "xy_order_stage_reply_records",
                "uq_order_stage_reply_account_stage_order",
                "ALTER TABLE xy_order_stage_reply_records ADD UNIQUE KEY uq_order_stage_reply_account_stage_order (account_id, stage, order_no)",
            )

    print("stage reply migration complete")


if __name__ == "__main__":
    asyncio.run(main())
