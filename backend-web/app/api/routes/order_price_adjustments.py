"""拍下后自动改价配置管理路由。"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import get_settings
from app.services.account_service import AccountService
from common.models.order_price_adjust_record import OrderPriceAdjustRecord
from common.models.order_price_adjustment import OrderPriceAdjustment
from common.models.user import User
from common.models.xy_order import XYOrder
from common.models.xy_catalog_item import XYCatalogItem
from common.utils.auth_scope import resolve_owner_scope
from common.utils.default_reply_api import normalize_api_timeout, validate_api_url

router = APIRouter(tags=["订单自动改价"])


class PriceAdjustmentUpdate(BaseModel):
    enabled: bool = False
    target_item_price: str = "0.00"
    target_post_fee: str = "0.00"
    override_url: str = ""
    override_timeout: int = 10


class DirectOrderPriceAdjustRequest(BaseModel):
    item_id: str = Field("", description="商品 ID；为空时使用订单记录中的商品 ID")
    target_item_price: str = Field(..., description="改后商品总价，单位元，必须大于 0，保存为两位小数")
    target_post_fee: str = Field("0.00", description="改后邮费，单位元，允许为 0")
    initial_delay_seconds: int = Field(0, ge=0, le=120, description="本次调用前等待秒数，范围 0-120")


def _normalize_item_id(item_id: str | None) -> str:
    return (item_id or "").strip()


def _normalize_money(value: Any, *, allow_zero: bool = False, field_name: str = "金额") -> str:
    raw = str(value if value is not None else "").strip()
    if not raw:
        raw = "0"
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail=f"{field_name}必须为有效金额")
    if amount < 0 or (amount == 0 and not allow_zero):
        raise HTTPException(status_code=400, detail=f"{field_name}必须{'大于等于0' if allow_zero else '大于0'}")
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _serialize_record(record: OrderPriceAdjustment | None, item_id: str) -> dict[str, Any]:
    if record is None:
        return {
            "success": True,
            "item_id": item_id,
            "enabled": False,
            "has_config": False,
            "target_item_price": "0.00",
            "target_post_fee": "0.00",
            "override_url": "",
            "override_timeout": 10,
        }
    return {
        "success": True,
        "item_id": record.item_id or item_id,
        "enabled": bool(record.enabled),
        "has_config": True,
        "target_item_price": record.target_item_price or "0.00",
        "target_post_fee": record.target_post_fee or "0.00",
        "override_url": record.override_url or "",
        "override_timeout": record.override_timeout or 10,
    }


async def _load_account(
    account_id: str,
    current_user: User,
    account_service: AccountService,
) -> tuple[Any, int | None, int]:
    owner_id, _ = resolve_owner_scope(current_user)
    account = await account_service.get_account_for_user(owner_id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    record_owner_id = owner_id if owner_id is not None else (getattr(account, "owner_id", 0) or 0)
    return account, owner_id, int(record_owner_id)


async def _ensure_item_for_account(
    session: AsyncSession,
    account: Any,
    item_id: str,
    owner_id: int | None,
) -> None:
    if not item_id:
        return
    stmt = select(XYCatalogItem).where(
        XYCatalogItem.account_pk == account.id,
        XYCatalogItem.item_id == item_id,
    )
    if owner_id is not None:
        stmt = stmt.where(XYCatalogItem.owner_id == owner_id)
    result = await session.execute(stmt)
    if result.scalars().first() is None:
        raise HTTPException(status_code=404, detail="商品不存在或不属于当前账号")


async def _get_record(
    session: AsyncSession,
    account_id: str,
    item_id: str,
    owner_id: int | None,
) -> OrderPriceAdjustment | None:
    stmt = select(OrderPriceAdjustment).where(
        OrderPriceAdjustment.account_id == account_id,
        OrderPriceAdjustment.item_id == item_id,
    )
    if owner_id is not None:
        stmt = stmt.where(OrderPriceAdjustment.owner_id == owner_id)
    result = await session.execute(stmt)
    return result.scalars().first()


def _extract_response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else {}
    return data if isinstance(data, dict) else {}


async def _record_direct_adjust_result(
    session: AsyncSession,
    *,
    order: XYOrder,
    record_owner_id: int,
    target_item_price: str,
    target_post_fee: str,
    result: dict[str, Any],
) -> None:
    result_data = _extract_response_data(result)
    original = result_data.get("original") if isinstance(result_data.get("original"), dict) else {}
    existing_result = await session.execute(
        select(OrderPriceAdjustRecord).where(
            OrderPriceAdjustRecord.account_id == order.account_id,
            OrderPriceAdjustRecord.order_no == order.order_no,
        )
    )
    record = existing_result.scalars().first()
    if record is None:
        record = OrderPriceAdjustRecord(
            owner_id=record_owner_id,
            account_id=order.account_id,
            item_id=order.item_id or "",
            order_no=order.order_no,
        )
        session.add(record)

    record.owner_id = record_owner_id
    record.item_id = order.item_id or ""
    record.buyer_id = order.buyer_id or None
    record.chat_id = order.chat_id or None
    record.target_item_price = result_data.get("target_item_price") or target_item_price
    record.target_post_fee = result_data.get("target_post_fee") or target_post_fee
    record.original_item_price = original.get("original_item_price") or None
    record.original_post_fee = original.get("original_post_fee") or None
    record.original_total_price = original.get("original_total_price") or None
    record.result_status = "success" if result.get("success") else "failed"
    record.result_message = (result.get("message") or "")[:2000]
    record.ret = json.dumps(result_data.get("ret") or [], ensure_ascii=False, default=str)[:2000]
    await session.commit()


@router.get("/{account_id}/statuses")
async def get_price_adjustment_statuses(
    account_id: str,
    item_ids: str = Query(""),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """批量获取商品的拍下改价状态。"""
    _, owner_id, _ = await _load_account(account_id, current_user, account_service)
    ids = [item.strip() for item in item_ids.split(",") if item.strip()]
    if not ids:
        return {"success": True, "data": {}}

    stmt = select(OrderPriceAdjustment).where(
        OrderPriceAdjustment.account_id == account_id,
        OrderPriceAdjustment.item_id.in_(ids),
    )
    if owner_id is not None:
        stmt = stmt.where(OrderPriceAdjustment.owner_id == owner_id)
    result = await session.execute(stmt)

    data: dict[str, dict[str, Any]] = {
        item_id: {"enabled": False, "has_config": False}
        for item_id in ids
    }
    for record in result.scalars().all():
        if record.item_id not in data:
            continue
        data[record.item_id] = {
            "enabled": bool(record.enabled),
            "has_config": True,
        }
    return {"success": True, "data": data}


@router.post(
    "/{account_id}/orders/{order_no}/adjust",
    summary="外部按订单号直接修改待付款订单价格",
    description=(
        "校验当前登录用户、账号归属、订单状态和商品 ID 后，转发到 websocket 服务复用当前账号 "
        "Cookie 执行卖家端 mtop 改价请求，并写入 xy_order_price_adjust_records。"
    ),
)
async def adjust_pending_order_price(
    account_id: str,
    order_no: str,
    data: DirectOrderPriceAdjustRequest,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """外部按订单号直接修改待付款订单价格。"""
    account, owner_id, record_owner_id = await _load_account(account_id, current_user, account_service)

    target_item_price = _normalize_money(data.target_item_price, field_name="改后商品总价")
    target_post_fee = _normalize_money(data.target_post_fee, allow_zero=True, field_name="改后邮费")
    initial_delay_seconds = max(0, min(int(data.initial_delay_seconds or 0), 120))

    stmt = select(XYOrder).where(
        XYOrder.account_id == account_id,
        XYOrder.order_no == order_no,
    )
    if owner_id is not None:
        stmt = stmt.where(XYOrder.owner_id == owner_id)
    order_result = await session.execute(stmt)
    order = order_result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在或不属于当前账号")

    pending_payment_statuses = {"pending_payment", "pending_pay", "wait_buyer_pay", "WAIT_BUYER_PAY"}
    if (order.status or "").strip() not in pending_payment_statuses:
        raise HTTPException(status_code=400, detail=f"订单当前状态不是待付款: {order.status}")

    item_id = _normalize_item_id(data.item_id) or (order.item_id or "")
    if not item_id:
        raise HTTPException(status_code=400, detail="缺少商品ID")
    if order.item_id and item_id != order.item_id:
        raise HTTPException(status_code=400, detail="商品ID与订单不匹配")

    settings = get_settings()
    base_url = settings.websocket_service_url.rstrip("/")
    payload = {
        "account_id": account_id,
        "order_no": order_no,
        "item_id": item_id,
        "target_item_price": target_item_price,
        "target_post_fee": target_post_fee,
        "initial_delay_seconds": initial_delay_seconds,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as client:
            async with client.post(f"{base_url}/internal/orders/adjust-price", json=payload) as resp:
                try:
                    ws_result = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    ws_result = {"success": False, "message": text, "data": None}
    except Exception as exc:
        ws_result = {"success": False, "message": f"调用 websocket 改价失败: {exc}", "data": None}

    await _record_direct_adjust_result(
        session,
        order=order,
        record_owner_id=record_owner_id,
        target_item_price=target_item_price,
        target_post_fee=target_post_fee,
        result=ws_result,
    )
    return {
        "success": bool(ws_result.get("success")),
        "message": ws_result.get("message") or ("订单改价成功" if ws_result.get("success") else "订单改价失败"),
        "data": {
            "account_id": account_id,
            "order_no": order_no,
            "item_id": item_id,
            "target_item_price": target_item_price,
            "target_post_fee": target_post_fee,
            "websocket_result": ws_result,
        },
    }


@router.get("/{account_id}/{item_id}")
async def get_price_adjustment(
    account_id: str,
    item_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """获取单个商品拍下改价配置。"""
    normalized_item_id = _normalize_item_id(item_id)
    account, owner_id, _ = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)
    record = await _get_record(session, account_id, normalized_item_id, owner_id)
    return _serialize_record(record, normalized_item_id)


@router.put("/{account_id}/{item_id}")
async def update_price_adjustment(
    account_id: str,
    item_id: str,
    data: PriceAdjustmentUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """保存单个商品拍下改价配置。"""
    normalized_item_id = _normalize_item_id(item_id)
    account, owner_id, record_owner_id = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)

    target_item_price = _normalize_money(data.target_item_price, field_name="改后商品总价")
    target_post_fee = _normalize_money(data.target_post_fee, allow_zero=True, field_name="改后邮费")
    override_url = (data.override_url or "").strip()
    override_timeout = normalize_api_timeout(data.override_timeout or 10)
    if override_url:
        if override_url.startswith(("ws://", "wss://")):
            pass
        else:
            valid, err = validate_api_url(override_url)
            if not valid:
                return {"success": False, "message": err}

    record = await _get_record(session, account_id, normalized_item_id, owner_id)
    if record is None:
        record = OrderPriceAdjustment(
            owner_id=record_owner_id,
            account_id=account_id,
            item_id=normalized_item_id,
        )
        session.add(record)

    record.enabled = bool(data.enabled)
    record.item_id = normalized_item_id
    record.target_item_price = target_item_price
    record.target_post_fee = target_post_fee
    record.override_url = override_url or None
    record.override_timeout = override_timeout

    await session.commit()
    await session.refresh(record)
    return {
        "success": True,
        "message": "拍下改价配置已保存",
        "data": _serialize_record(record, normalized_item_id),
    }


@router.delete("/{account_id}/{item_id}")
async def delete_price_adjustment(
    account_id: str,
    item_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """删除单个商品拍下改价配置。"""
    normalized_item_id = _normalize_item_id(item_id)
    _, owner_id, _ = await _load_account(account_id, current_user, account_service)

    stmt = delete(OrderPriceAdjustment).where(
        OrderPriceAdjustment.account_id == account_id,
        OrderPriceAdjustment.item_id == normalized_item_id,
    )
    if owner_id is not None:
        stmt = stmt.where(OrderPriceAdjustment.owner_id == owner_id)
    result = await session.execute(stmt)
    await session.commit()
    return {
        "success": True,
        "message": "拍下改价配置已删除",
        "deleted": result.rowcount > 0,
    }
