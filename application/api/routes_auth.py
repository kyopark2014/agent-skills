from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/session", tags=["session"])

SESSION_COOKIE = "agent_user_id"


class SessionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    user_id: str


@router.post("", response_model=SessionResponse)
def set_session(body: SessionRequest, response: Response) -> SessionResponse:
    user_id = body.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=user_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return SessionResponse(user_id=user_id)


@router.get("", response_model=SessionResponse | None)
def get_session(request: Request) -> SessionResponse | None:
    user_id = (request.cookies.get(SESSION_COOKIE) or "").strip()
    if not user_id:
        return None
    return SessionResponse(user_id=user_id)


@router.delete("", status_code=204)
def clear_session(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, samesite="lax")


def require_user_id(request: Request) -> str:
    user_id = request.cookies.get(SESSION_COOKIE)
    if not user_id:
        raise HTTPException(status_code=401, detail="User session required")
    return user_id
