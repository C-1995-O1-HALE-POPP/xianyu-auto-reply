"""订单阶段自动回复管理路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.paths import STATIC_ROOT
from app.services.account_service import AccountService
from common.models.purchase_action import PurchaseAction
from common.models.user import User
from common.models.xy_catalog_item import XYCatalogItem
from common.utils.auth_scope import resolve_owner_scope
from common.utils.default_reply_api import normalize_api_timeout, validate_api_url
from common.utils.local_image_upload import ImageUploadError, save_uploaded_image

router = APIRouter(tags=["订单阶段回复"])

VALID_STAGES = {"purchase", "paid"}
STAGE_LABELS = {
    "purchase": "拍下回复",
    "paid": "付款回复",
}

UPLOAD_DIR = STATIC_ROOT / "uploads" / "stage_reply"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class StageReplyUpdate(BaseModel):
    enabled: bool = False
    reply_type: str = "text"
    reply_content: str = ""
    reply_image: str = ""
    api_url: str = ""
    api_timeout: int = 80


class LegacyPurchaseActionUpdate(BaseModel):
    item_id: str = ""
    enabled: bool = False
    action_type: str = "message"
    message_template: str = ""
    api_url: str = ""
    api_method: str = "POST"
    api_timeout: int = 10
    api_params: str = ""


def _normalize_stage(stage: str) -> str:
    value = (stage or "").strip()
    if value not in VALID_STAGES:
        raise HTTPException(status_code=400, detail="stage 必须为 purchase 或 paid")
    return value


def _normalize_item_id(item_id: str | None) -> str:
    return (item_id or "").strip()


def _serialize_record(record: PurchaseAction | None, item_id: str, stage: str) -> dict[str, Any]:
    if record is None:
        return {
            "success": True,
            "item_id": item_id,
            "stage": stage,
            "enabled": False,
            "has_config": False,
            "reply_type": "text",
            "reply_content": "",
            "reply_image": "",
            "api_url": "",
            "api_timeout": 80,
        }
    return {
        "success": True,
        "item_id": record.item_id or item_id,
        "stage": record.stage or stage,
        "enabled": bool(record.enabled),
        "has_config": True,
        "reply_type": "api" if record.action_type == "api" else "text",
        "reply_content": record.message_template or "",
        "reply_image": record.reply_image or "",
        "api_url": record.api_url or "",
        "api_timeout": record.api_timeout or 80,
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
    stage: str,
    owner_id: int | None,
) -> PurchaseAction | None:
    stmt = select(PurchaseAction).where(
        PurchaseAction.account_id == account_id,
        PurchaseAction.item_id == item_id,
        PurchaseAction.stage == stage,
    )
    if owner_id is not None:
        stmt = stmt.where(PurchaseAction.owner_id == owner_id)
    result = await session.execute(stmt)
    return result.scalars().first()


@router.get("/{account_id}/statuses")
async def get_stage_reply_statuses(
    account_id: str,
    item_ids: str = Query(""),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """批量获取商品的拍下/付款回复状态。"""
    _, owner_id, _ = await _load_account(account_id, current_user, account_service)
    ids = [item.strip() for item in item_ids.split(",") if item.strip()]
    if not ids:
        return {"success": True, "data": {}}

    stmt = select(PurchaseAction).where(
        PurchaseAction.account_id == account_id,
        PurchaseAction.item_id.in_(ids),
        PurchaseAction.stage.in_(tuple(VALID_STAGES)),
    )
    if owner_id is not None:
        stmt = stmt.where(PurchaseAction.owner_id == owner_id)
    result = await session.execute(stmt)

    data: dict[str, dict[str, dict[str, Any]]] = {
        item_id: {
            "purchase": {"enabled": False, "has_config": False},
            "paid": {"enabled": False, "has_config": False},
        }
        for item_id in ids
    }
    for record in result.scalars().all():
        if record.item_id not in data or record.stage not in VALID_STAGES:
            continue
        data[record.item_id][record.stage] = {
            "enabled": bool(record.enabled),
            "has_config": True,
        }
    return {"success": True, "data": data}


@router.get("/{account_id}/{item_id}")
async def get_stage_reply(
    account_id: str,
    item_id: str,
    stage: str = Query("purchase"),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """获取单个商品某个订单阶段的自动回复配置。"""
    normalized_stage = _normalize_stage(stage)
    normalized_item_id = _normalize_item_id(item_id)
    account, owner_id, _ = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)
    record = await _get_record(session, account_id, normalized_item_id, normalized_stage, owner_id)
    return _serialize_record(record, normalized_item_id, normalized_stage)


@router.put("/{account_id}/{item_id}")
async def update_stage_reply(
    account_id: str,
    item_id: str,
    data: StageReplyUpdate,
    stage: str = Query("purchase"),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """保存单个商品某个订单阶段的自动回复配置。"""
    normalized_stage = _normalize_stage(stage)
    normalized_item_id = _normalize_item_id(item_id)
    account, owner_id, record_owner_id = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)

    reply_type = (data.reply_type or "text").strip()
    if reply_type not in {"text", "api"}:
        raise HTTPException(status_code=400, detail="reply_type 必须为 text 或 api")

    api_timeout = normalize_api_timeout(data.api_timeout)
    if reply_type == "api":
        valid, err = validate_api_url(data.api_url)
        if not valid:
            return {"success": False, "message": err}

    record = await _get_record(session, account_id, normalized_item_id, normalized_stage, owner_id)
    if record is None:
        record = PurchaseAction(
            owner_id=record_owner_id,
            account_id=account_id,
            item_id=normalized_item_id,
            stage=normalized_stage,
        )
        session.add(record)

    record.enabled = bool(data.enabled)
    record.stage = normalized_stage
    record.item_id = normalized_item_id
    record.action_type = "api" if reply_type == "api" else "message"
    record.message_template = data.reply_content or None
    record.reply_image = data.reply_image or None
    record.api_url = data.api_url or None
    record.api_method = "POST"
    record.api_timeout = api_timeout
    record.api_params = None

    await session.commit()
    await session.refresh(record)
    return {
        "success": True,
        "message": f"{STAGE_LABELS[normalized_stage]}配置已保存",
        "data": _serialize_record(record, normalized_item_id, normalized_stage),
    }


@router.delete("/{account_id}/{item_id}")
async def delete_stage_reply(
    account_id: str,
    item_id: str,
    stage: str = Query("purchase"),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """删除单个商品某个订单阶段的自动回复配置。"""
    normalized_stage = _normalize_stage(stage)
    normalized_item_id = _normalize_item_id(item_id)
    _, owner_id, _ = await _load_account(account_id, current_user, account_service)

    stmt = delete(PurchaseAction).where(
        PurchaseAction.account_id == account_id,
        PurchaseAction.item_id == normalized_item_id,
        PurchaseAction.stage == normalized_stage,
    )
    if owner_id is not None:
        stmt = stmt.where(PurchaseAction.owner_id == owner_id)
    result = await session.execute(stmt)
    await session.commit()
    return {
        "success": True,
        "message": f"{STAGE_LABELS[normalized_stage]}配置已删除",
        "deleted": result.rowcount > 0,
    }


@router.post("/{account_id}/{item_id}/upload-image")
async def upload_stage_reply_image(
    account_id: str,
    item_id: str,
    stage: str = Query("purchase"),
    image: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    """上传拍下/付款回复图片。"""
    normalized_stage = _normalize_stage(stage)
    normalized_item_id = _normalize_item_id(item_id)
    account, owner_id, _ = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)

    try:
        _, filename, _ = await save_uploaded_image(
            image,
            UPLOAD_DIR,
            filename_prefix=f"{account_id}_{normalized_item_id}_{normalized_stage}",
            validate_size=False,
        )
    except ImageUploadError as exc:
        return {"success": False, "message": exc.message}

    return {"success": True, "image_url": f"/static/uploads/stage_reply/{filename}"}


# 兼容旧注入脚本的一段式接口，固定映射到 purchase 阶段。
@router.get("/{account_id}")
async def get_purchase_action_legacy(
    account_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    _, owner_id, _ = await _load_account(account_id, current_user, account_service)
    stmt = select(PurchaseAction).where(
        PurchaseAction.account_id == account_id,
        PurchaseAction.stage == "purchase",
    )
    if owner_id is not None:
        stmt = stmt.where(PurchaseAction.owner_id == owner_id)
    result = await session.execute(stmt)
    record = result.scalars().first()
    if record is None:
        return {
            "enabled": False,
            "item_id": "",
            "action_type": "message",
            "message_template": "",
            "api_url": "",
            "api_method": "POST",
            "api_timeout": 10,
            "api_params": "",
        }
    return {
        "enabled": bool(record.enabled),
        "item_id": record.item_id or "",
        "action_type": record.action_type or "message",
        "message_template": record.message_template or "",
        "api_url": record.api_url or "",
        "api_method": record.api_method or "POST",
        "api_timeout": record.api_timeout or 10,
        "api_params": record.api_params or "",
    }


@router.put("/{account_id}")
async def update_purchase_action_legacy(
    account_id: str,
    data: LegacyPurchaseActionUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    account_service: AccountService = Depends(deps.get_account_service),
    session: AsyncSession = Depends(deps.get_db_session),
):
    normalized_item_id = _normalize_item_id(data.item_id)
    account, owner_id, record_owner_id = await _load_account(account_id, current_user, account_service)
    await _ensure_item_for_account(session, account, normalized_item_id, owner_id)

    record = await _get_record(session, account_id, normalized_item_id, "purchase", owner_id)
    if record is None:
        record = PurchaseAction(
            owner_id=record_owner_id,
            account_id=account_id,
            item_id=normalized_item_id,
            stage="purchase",
        )
        session.add(record)

    record.enabled = bool(data.enabled)
    record.action_type = "api" if data.action_type == "api" else "message"
    record.message_template = data.message_template or None
    record.api_url = data.api_url or None
    record.api_method = "POST"
    record.api_timeout = normalize_api_timeout(data.api_timeout)
    record.api_params = data.api_params or None
    await session.commit()
    await session.refresh(record)
    return {
        "success": True,
        "message": "拍下动作配置更新成功",
        "enabled": bool(record.enabled),
        "action_type": record.action_type,
    }
