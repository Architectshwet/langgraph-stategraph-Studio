import json
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Configuration
AUTH_USERS_RAW = os.getenv(
    "SEAGATE_AUTH_USERS_RAW",
    '{"admin":{"password":"admin","role":"admin"}}',
)
SECRET_KEY = os.getenv("SEAGATE_SESSION_SECRET_KEY", "dev-seagate-jwt-secret-keep-it-safe")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("SEAGATE_SESSION_MAX_AGE_SECONDS", "1800")) // 60
COOKIE_NAME = "seagate_token"

try:
    AUTH_USERS = json.loads(AUTH_USERS_RAW)
except json.JSONDecodeError:
    logger.error("Failed to parse WAFER_AUTH_USERS_RAW, using default admin")
    AUTH_USERS = {"admin": {"password": "admin", "role": "admin"}}

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)

auth_router = APIRouter(tags=["auth"])

class Token(BaseModel):
    access_token: str
    token_type: str

class User(BaseModel):
    username: str
    role: str

def verify_password(plain_password, hashed_password):
    if plain_password == hashed_password:
        return True
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(request: Request, token: Optional[str] = Depends(oauth2_scheme)) -> User:
    """Robust OAuth2 dependency that checks both Authorization header and Cookie."""
    # 1. Fallback to cookie if oauth2_scheme (Header) didn't find anything
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    
    if not token:
        # We don't log this at INFO level to avoid noise on the login page
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role", "user")
        if username is None:
            logger.warning("Token payload missing 'sub' claim")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return User(username=username, role=role)
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

def require_role(required_role: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != required_role:
            logger.warning(f"User {user.username} tried to access {required_role} resource with role {user.role}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return dependency

def configure_auth(app) -> None:
    pass

@auth_router.post("/auth/token", response_model=Token)
async def login_for_access_token(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    """Standard OAuth2 /token endpoint."""
    user_record = AUTH_USERS.get(form_data.username)
    if not user_record or not verify_password(form_data.password, user_record.get("password")):
        logger.info(f"Failed login attempt for user: {form_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"Successful login for user: {form_data.username}")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username, "role": user_record.get("role", "user")},
        expires_delta=access_token_expires
    )
    
    # Set cookie for browser UI convenience with path="/" and explicit Lax samesite
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        path="/",
        secure=os.getenv("SEAGATE_SESSION_SECURE_COOKIE", "false").lower() == "true"
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@auth_router.post("/auth/login")
async def auth_login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    return await login_for_access_token(response, form_data)

@auth_router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}

@auth_router.get("/auth/me", response_model=User)
async def auth_me(current_user: User = Depends(get_current_user)):
    return current_user

@auth_router.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE_HTML

LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Seagate Sign In</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --forest: #0f5132;
      --forest-2: #156247;
      --paper: #f4fbf6;
      --ink: #0f172a;
      --muted: #5b6b63;
      --border: #d5e2d8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "Manrope", sans-serif;
      background:
        radial-gradient(circle at top, rgba(159,227,191,0.35), transparent 34%),
        linear-gradient(145deg, #0b2f24 0%, #114d39 45%, #1f6b4f 100%);
      color: white;
      padding: 24px;
    }
    .card {
      width: min(100%, 520px);
      background: rgba(255,255,255,0.96);
      color: var(--ink);
      border-radius: 28px;
      box-shadow: 0 30px 80px rgba(8, 30, 23, 0.28);
      padding: 34px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 24px;
    }
    .logo {
      width: 58px;
      height: 58px;
      border-radius: 18px;
      background: linear-gradient(145deg, #9fe3bf, #c9a24d);
      color: var(--forest);
      display: grid;
      place-items: center;
      font-weight: 800;
      letter-spacing: 0.5px;
    }
    h1 { margin: 0; font-size: 28px; }
    p { margin: 0; color: var(--muted); line-height: 1.5; }
    .form {
      display: grid;
      gap: 14px;
      margin-top: 22px;
    }
    input {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 14px 16px;
      font: inherit;
      background: white;
    }
    button {
      border: none;
      border-radius: 18px;
      padding: 14px 18px;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }
    .primary {
      background: linear-gradient(135deg, var(--forest-2), #2d7c5d);
      color: white;
    }
    .status {
      margin-top: 14px;
      color: #b91c1c;
      font-weight: 700;
      min-height: 22px;
    }
  </style>
</head>
<body>
  <main class="card">
    <div class="brand">
      <div class="logo">SAA</div>
      <div>
        <h1>Sign in</h1>
        <p>Welcome to your Seagate Agent Assistant.</p>
      </div>
    </div>
    <form id="loginForm" class="form">
      <input id="username" type="text" autocomplete="username" placeholder="Username" required />
      <input id="password" type="password" autocomplete="current-password" placeholder="Password" required />
      <button class="primary" type="submit">Sign in</button>
    </form>
    <div id="status" class="status"></div>
  </main>
  <script>
    const form = document.getElementById('loginForm');
    const statusBox = document.getElementById('status');
    
    // Check if already logged in (silent check to avoid console noise)
    fetch('/auth/me')
      .then(r => { if(r.ok) window.location.href='/web'; })
      .catch(() => {});

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      statusBox.style.color = '#0f5132';
      statusBox.textContent = 'Authenticating...';
      
      const params = new URLSearchParams();
      params.append('username', document.getElementById('username').value.trim());
      params.append('password', document.getElementById('password').value);

      try {
        const response = await fetch('/auth/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: params,
        });
        
        if (!response.ok) throw new Error('Auth failed');
        
        window.location.href = '/web';
      } catch (error) {
        statusBox.style.color = '#b91c1c';
        statusBox.textContent = 'Authentication failed. Please check your credentials.';
      }
    });
  </script>
</body>
</html>
"""
