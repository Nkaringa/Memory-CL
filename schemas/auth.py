from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: str


class UserView(BaseModel):
    user_id: str
    email: str
    display_name: str
    org_id: str
    roles: list[str]


class MeResponse(BaseModel):
    authenticated: bool
    user: UserView | None = None
