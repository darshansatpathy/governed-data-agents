"""HTTP API.

Identity comes from a bearer token (GDA_API_TOKENS="token=user,...") or, behind an
authenticating proxy, from a trusted header (GDA_TRUSTED_USER_HEADER, e.g. X-Forwarded-User).
With neither configured every request is rejected.
"""

import hmac
import os
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from gda.access import UnknownUser
from gda.catalog import visible_catalog
from gda.service import MAX_QUESTION, Service


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    planner: str | None = None


def create_app(service: Service, env: Mapping[str, str] | None = None) -> FastAPI:
    env = dict(os.environ if env is None else env)
    tokens = dict(pair.split("=", 1) for pair in env.get("GDA_API_TOKENS", "").split(",") if "=" in pair)
    header = env.get("GDA_TRUSTED_USER_HEADER", "")
    app = FastAPI(title="Governed Data Agents", version="0.1.1")

    def user(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            given = auth[7:]
            for token, user_id in tokens.items():
                if hmac.compare_digest(token, given):
                    return user_id
        if header and request.headers.get(header):
            return request.headers[header]
        raise HTTPException(401, "authentication required")

    User = Annotated[str, Depends(user)]

    def known(user_id: str) -> None:
        try:
            service.principal(user_id)
        except UnknownUser:
            raise HTTPException(403, "no access policy for this user") from None

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ask")
    def ask(body: Ask, user_id: User) -> dict[str, Any]:
        known(user_id)
        if body.planner and body.planner not in service.planners:
            raise HTTPException(400, f"unknown planner; available: {', '.join(service.planners)}")
        return service.ask(body.question, user_id, body.planner).to_dict()

    @app.get("/catalog")
    def catalog(user_id: User) -> dict[str, Any]:
        known(user_id)
        return visible_catalog(service.model, service.principal(user_id))

    @app.get("/audit")
    def audit(user_id: User, limit: int = 20) -> list[dict[str, Any]]:
        known(user_id)
        recent = getattr(service.audit, "recent", None)
        if recent is None:
            raise HTTPException(501, "this audit log cannot be read back")
        rows: list[dict[str, Any]] = recent(min(max(limit, 1), 200), user_id)
        return rows

    return app


def main_app() -> FastAPI:  # pragma: no cover - used by uvicorn --factory
    from gda.app import build

    return create_app(build())
