"""拍下后自动改价配置管理路由。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.services.account_service import AccountService
from common.models.order_price_adjustment import OrderPriceAdjustment
from common.models.user import User
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
