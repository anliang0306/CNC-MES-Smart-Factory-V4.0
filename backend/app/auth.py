"""Small self-contained HMAC bearer-token auth for the private-network MVP.
For production, place this behind the company IdP/SSO and rotate TOKEN_SECRET.
"""
import base64, hashlib, hmac, json, os, time
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .db import get_db
from . import models_v2 as m2

bearer = HTTPBearer(auto_error=False)

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return "pbkdf2_sha256$310000$%s$%s" % (_b64(salt), _b64(digest))

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(rounds))
        return hmac.compare_digest(actual, _unb64(expected))
    except Exception:
        return False

# 登录限流：单进程内存计数（私有网段 MVP 足够；多副本部署时换成 Redis 等共享存储）
_login_failures: dict[str, list[float]] = {}
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 900

def check_login_rate_limit(username: str) -> None:
    now = time.time()
    hits = [t for t in _login_failures.get(username, []) if now - t < LOGIN_LOCK_SECONDS]
    if hits:
        _login_failures[username] = hits
    if len(hits) >= LOGIN_MAX_FAILURES:
        raise HTTPException(429, f"登录失败次数过多，请 {LOGIN_LOCK_SECONDS // 60} 分钟后再试")

def record_login_failure(username: str) -> None:
    now = time.time()
    hits = [t for t in _login_failures.get(username, []) if now - t < LOGIN_LOCK_SECONDS]
    hits.append(now)
    _login_failures[username] = hits

def clear_login_failures(username: str) -> None:
    _login_failures.pop(username, None)

def issue_token(user: m2.User) -> str:
    payload = {"sub": user.username, "role": user.role, "exp": int(time.time()) + int(os.getenv("TOKEN_TTL_SECONDS", "28800"))}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    secret = os.getenv("TOKEN_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "TOKEN_SECRET 未配置为至少32位强随机值")
    signature = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature

def decode_token(token: str) -> dict:
    try:
        body, signature = token.split(".", 1)
        secret = os.getenv("TOKEN_SECRET", "")
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if len(secret) < 32 or not hmac.compare_digest(signature, expected): raise ValueError("signature")
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < int(time.time()): raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(401, "登录凭证无效或已过期")

def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    if not credentials: raise HTTPException(401, "需要 Bearer Token")
    payload = decode_token(credentials.credentials)
    user = db.query(m2.User).filter_by(username=payload.get("sub"), active=True).first()
    if not user: raise HTTPException(401, "用户不存在或已停用")
    return user

def require_roles(*roles: str):
    def checker(user: m2.User = Depends(get_current_user)):
        if user.role not in roles: raise HTTPException(403, "当前角色无权执行此操作")
        return user
    return checker

def write_audit(db: Session, user: m2.User | None, action: str, entity_type: str, entity_id: str = "", detail: str = ""):
    db.add(m2.AuditLog(username=user.username if user else "system", action=action, entity_type=entity_type, entity_id=str(entity_id), detail=detail[:4000]))
