"""Chatbot routes — staff widget endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.chat import ChatMessage
from app.permissions import require_permission
from app.services.chatbot import get_or_create_conversation, respond
from app.services.feature_flags import newtowt_agent_enabled
from app.templating import templates

router = APIRouter(prefix="/chat", tags=["chat"])


async def _assert_agent_enabled(db: AsyncSession) -> None:
    """403 si le Newtowt Agent est désactivé dans la configuration (/admin)."""
    if not await newtowt_agent_enabled(db):
        raise HTTPException(status_code=403, detail="Newtowt Agent désactivé")


@router.get("", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("chat", "C")),
) -> HTMLResponse:
    await _assert_agent_enabled(db)
    conv = await get_or_create_conversation(db, user.id)
    msgs = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conv.id)
                .order_by(ChatMessage.created_at)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/chat/index.html",
        {"request": request, "user": user, "conversation": conv, "messages": msgs},
    )


@router.post("/messages")
async def chat_send(
    request: Request,
    text: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("chat", "M")),
) -> JSONResponse:
    await _assert_agent_enabled(db)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    conv = await get_or_create_conversation(db, user.id)
    msg = await respond(db, conversation=conv, user_text=text.strip(), user_role=user.role)
    return JSONResponse(
        {
            "role": msg.role,
            "content": msg.content,
            "tokens_in": msg.tokens_in,
            "tokens_out": msg.tokens_out,
            "cost_usd": float(msg.cost_usd) if msg.cost_usd else None,
        }
    )
