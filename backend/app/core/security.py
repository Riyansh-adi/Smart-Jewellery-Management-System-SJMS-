from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pymongo.database import Database
from app.core.config import settings
from app.db.mongo import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def hash_password(value: str) -> str:
    return pwd_context.hash(value)

def verify_password(value: str, hashed: str) -> bool:
    return pwd_context.verify(value, hashed)

def create_access_token(subject: str) -> str:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET must be set in the environment before signing login tokens")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": subject, "exp": expires}, settings.jwt_secret, algorithm="HS256")

def current_user(token: str = Depends(oauth2_scheme), db: Database = Depends(get_db)) -> dict:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        user_id = int(jwt.decode(token, settings.jwt_secret, algorithms=["HS256"]).get("sub", ""))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired login")
    user = db.users.find_one({"id": user_id})
    if not user or not user.get("active", True):
        raise HTTPException(status_code=401, detail="Account is unavailable")
    return user

def require_roles(*roles: str):
    def check(user: dict = Depends(current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission for this action")
        return user
    return check

