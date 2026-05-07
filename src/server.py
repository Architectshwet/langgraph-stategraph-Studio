import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi import UploadFile, File
import shutil
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from src.auth import auth_router, configure_auth, get_current_user, require_role
from src.agents.multi_agent_state_graph import (
    create_seagate_stategraph_agent,
    DMR_PREFIX,
    SSL_PREFIX
)
from src.websocket import ws_router
from src.services.langfuse_service import langfuse_service
from src.services.postgres_db_service import PostgresDBService
from src.services.postgres_service import postgres_service
from src.state.checkpointer import get_checkpointer, get_pool
from src.state.store import get_session, get_store, set_session
from src.utils.logger import get_logger

logger = get_logger(__name__)
db_service = PostgresDBService()
global_agent = None

_cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = (
    [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]
    if _cors_origins_env
    else ["*"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Seagate Agent Assistant API...")

    try:
        langfuse_service.initialize()
    except Exception as exc:
        logger.error("Failed to initialize Langfuse service: %s", exc)

    checkpointer = get_checkpointer()
    if not checkpointer:
        logger.warning("Checkpointer was not initialized. Persistence will be disabled.")

    pool = get_pool()
    if not pool:
        raise RuntimeError("PostgreSQL pool was not initialized for checkpointer.")

    await pool.open(wait=True, timeout=30)
    logger.info("PostgreSQL connection pool opened")
    await checkpointer.setup()
    logger.info("Async PostgreSQL checkpointer initialized")

    await db_service.initialize()
    await postgres_service.initialize()
    seed_result = await postgres_service.seed_demo_data(reset=False)
    if seed_result.get("skipped"):
        logger.info("Demo wafer seed skipped: data already exists")
    else:
        logger.info("Demo wafer data seeded (reset=%s)", seed_result.get("reset"))
    # Initialize Redis Store
    store = get_store()
    await store.setup()
    logger.info("AsyncRedisStore setup complete")

    global global_agent
    global_agent = create_seagate_stategraph_agent(use_memory_checkpointer=False)
    logger.info("Global Seagate agent initialized")

    logger.info("Wafer services initialized successfully")

    yield

    logger.info("Shutting down Seagate Agent Assistant API...")
    try:
        langfuse_service.flush()
    except Exception as exc:
        logger.error("Error flushing Langfuse: %s", exc)

    try:
        await db_service.close()
    except Exception as exc:
        logger.error("Error closing database service: %s", exc)

    pool = get_pool()
    if pool:
        try:
            await pool.close()
        except Exception as exc:
            logger.error("Error closing checkpointer pool: %s", exc)


app = FastAPI(
    title="Seagate Agent Assistant API",
    description="StateGraph Seagate operations assistant with router + automation specialist architecture",
    version="1.0.0",
    lifespan=lifespan,
)

configure_auth(app)
app.include_router(auth_router)
app.include_router(ws_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "seagate-agent"}


ROLE_TO_TYPE = {"user": "human", "assistant": "ai", "system": "system", "tool": "tool"}
ARCH_LANGGRAPH_STATEGRAPH = "Langgraph_stategraph"


def _to_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return " ".join(parts)
    return str(content)


def _to_lc_message(msg: dict[str, Any]) -> BaseMessage:
    msg_type = msg.get("type") or ROLE_TO_TYPE.get(str(msg.get("role", "")).lower())
    content = _to_text_content(msg.get("content"))
    if msg_type == "human":
        return HumanMessage(content=content)
    if msg_type == "ai":
        return AIMessage(content=content)
    if msg_type == "system":
        return SystemMessage(content=content)
    if msg_type == "tool":
        return ToolMessage(content=content, tool_call_id=msg.get("tool_call_id", ""))
    return HumanMessage(content=content)


def normalize_input(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload or {}
    if "input" in data:
        inner = data["input"]
        if isinstance(inner, str):
            return {"messages": [HumanMessage(content=inner)]}
        if isinstance(inner, dict):
            if "messages" in inner and isinstance(inner["messages"], list):
                return {"messages": [_to_lc_message(m) for m in inner["messages"]]}
            if "content" in inner:
                return {"messages": [_to_lc_message(inner)]}
            return inner
    if "messages" in data and isinstance(data["messages"], list):
        return {"messages": [_to_lc_message(m) for m in data["messages"]]}
    return {"messages": [HumanMessage(content=str(data))]}


def _extract_last_message_from_chain_output(output: Any) -> str:
    if not isinstance(output, dict):
        return ""
    messages = output.get("messages") or []
    if not messages:
        return ""
    last_message = messages[-1]
    content = getattr(last_message, "content", "")
    return _to_text_content(content).strip()


def _extract_stream_progress(data: Any) -> str:
    logger.info("[extract_stream_progress] data=%r", data)
    if isinstance(data, dict):
        content = data.get("status") or data.get("content") or data.get("message")
        if isinstance(content, str):
            return content.strip()
    if isinstance(data, str):
        return data.strip()
    return ""


def _extract_stream_token(data: Any) -> str:
    if isinstance(data, tuple) or isinstance(data, list):
        if not data:
            return ""
        return _to_text_content(getattr(data[0], "content", data[0]))
    if hasattr(data, "content"):
        return _to_text_content(getattr(data, "content", ""))
    if isinstance(data, dict):
        return _to_text_content(data.get("content", ""))
    return _to_text_content(data)


def _parse_stream_event(chunk: Any) -> tuple[str, Any]:
    if isinstance(chunk, dict):
        return str(chunk.get("type") or ""), chunk.get("data")

    if isinstance(chunk, (tuple, list)):
        if len(chunk) == 2 and isinstance(chunk[0], str):
            mode, data = chunk
            return mode, data
        if len(chunk) == 3 and isinstance(chunk[1], str):
            _, mode, data = chunk
            return mode, data

    return "", chunk


class ChatMessage(BaseModel):
    role: str
    content: list[dict[str, Any]] | str


class ChatInput(BaseModel):
    messages: list[ChatMessage]
    channel: str = "web"
    architecture: str | None = None


class StreamRequest(BaseModel):
    input: ChatInput
    thread_id: str | None = None


@app.get("/admin/data-summary")
async def data_summary(user=Depends(require_role("admin"))):
    return await postgres_service.get_data_summary()


@app.post("/chat/stream")
async def chat_stream(request: StreamRequest, current_user=Depends(get_current_user)):
    try:
        normalized_input = normalize_input(request.input.model_dump())

        user_message_content = ""
        if normalized_input.get("messages"):
            last_message = normalized_input["messages"][-1]
            if isinstance(last_message, HumanMessage):
                user_message_content = str(last_message.content)

        conversation_id = request.thread_id or f"seagate-thread-{uuid.uuid4()}"
        channel = request.input.channel or "web"
        architecture = ARCH_LANGGRAPH_STATEGRAPH
        if request.input.architecture and request.input.architecture != ARCH_LANGGRAPH_STATEGRAPH:
            logger.warning(
                "Architecture '%s' is no longer supported. Using '%s'.",
                request.input.architecture,
                ARCH_LANGGRAPH_STATEGRAPH,
            )

        store = get_store()
        session_data = await get_session(store, conversation_id)
        session_data["channel"] = channel
        session_data["architecture"] = architecture
        session_data["auth_user"] = current_user.model_dump()
        await set_session(store, conversation_id, session_data)

        if user_message_content:
            logger.info("[user_message] thread_id=%s content=%s", conversation_id, user_message_content)
            await db_service.append_conversation_message(
                conversation_id=conversation_id,
                role="user",
                content=user_message_content,
                agent_name="User",
            )

        config = {"configurable": {"thread_id": conversation_id}}
        langfuse_handler = langfuse_service.get_handler()
        if langfuse_handler:
            config["callbacks"] = [langfuse_handler]

        async def generate_stream():
            assistant_response = ""
            streamed_token = False
            agent_name = "SeagateAgentAssistant"

            yield f"data: {json.dumps({'thread_id': conversation_id, 'type': 'session', 'architecture': architecture, 'timestamp': time.time()})}\n\n"

            try:
                agent = global_agent
                graph_input = normalized_input
                graph_input = {
                    **normalized_input,
                    # Reset per-run routing loop guard for each new user turn.
                    "loop_count": 0,
                }

                async for chunk in agent.astream(
                    graph_input,
                    config=config,
                    stream_mode=["custom", "updates"],
                    version="v2",
                ):
                    chunk_type, chunk_data = _parse_stream_event(chunk)
                    if chunk_type == "custom":
                        progress_text = _extract_stream_progress(chunk_data)
                        if progress_text:
                            yield f"data: {json.dumps({'thread_id': conversation_id, 'type': 'progress', 'content': progress_text, 'timestamp': time.time()})}\n\n"

                    elif chunk_type == "updates" and isinstance(chunk_data, dict):
                        for node_name, node_update in chunk_data.items():
                            final_text = ""
                            if node_name == "router" and str(node_update.get("router_action") or "") == "FINAL_RESPONSE":
                                final_text = _extract_last_message_from_chain_output(
                                    {"messages": node_update.get("messages") or []}
                                )
                            elif node_name in {"partial_normal_dmr_node", "ssl_node"}:
                                raw_text = _extract_last_message_from_chain_output(
                                    {"messages": node_update.get("messages") or []}
                                )
                                # Strip internal routing prefixes if present
                                final_text = raw_text.replace(DMR_PREFIX, "").replace(SSL_PREFIX, "").strip()
                            
                            if final_text:
                                assistant_response = final_text
                                if not streamed_token:
                                    yield f"data: {json.dumps({'thread_id': conversation_id, 'type': 'token', 'content': assistant_response, 'timestamp': time.time()})}\n\n"
                                    streamed_token = True

                if not assistant_response.strip():
                    assistant_response = "I completed the request, but no final text response was generated."
                if not streamed_token:
                    yield f"data: {json.dumps({'thread_id': conversation_id, 'type': 'token', 'content': assistant_response, 'timestamp': time.time()})}\n\n"
                    streamed_token = True

                logger.info(
                    "[assistant_response] thread_id=%s agent=%s content=%s",
                    conversation_id,
                    agent_name,
                    assistant_response,
                )
                await db_service.append_conversation_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_response,
                    agent_name=agent_name,
                )

                yield f"data: {json.dumps({'thread_id': conversation_id, 'type': 'end_of_response', 'content': assistant_response, 'timestamp': time.time()})}\n\n"
            except Exception as exc:
                logger.error("Error in streaming: %s", exc)
                yield f"data: {json.dumps({'thread_id': conversation_id, 'error': str(exc), 'type': type(exc).__name__})}\n\n"

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream; charset=utf-8",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/upload-csv")
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    thread_id = request.query_params.get("thread_id")
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id is required")

    os.makedirs("uploads", exist_ok=True)
    file_path = os.path.join("uploads", f"{thread_id}_dmr.csv")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    logger.info("[upload_csv] thread_id=%s file=%s", thread_id, file.filename)
    return {"filename": file.filename, "thread_id": thread_id, "status": "uploaded"}

@app.get("/web", response_class=HTMLResponse)
def web_interface(request: Request):
    # Check for professional JWT token in cookies instead of legacy session
    if not request.cookies.get("seagate_token"):
        return RedirectResponse("/login", status_code=303)
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Seagate Agent Assistant</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --ink: #0f172a;
            --forest: #0f5132;
            --mint: #9fe3bf;
            --gold: #c9a24d;
            --paper: #f6fbf7;
            --panel: #ffffff;
            --muted: #64748b;
            --border: #dfe7e2;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Manrope', sans-serif;
            background: radial-gradient(circle at top left, rgba(201,162,77,0.35), transparent 28%),
                        linear-gradient(145deg, #0b2f24 0%, #114d39 45%, #1f6b4f 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .app-shell {
            width: min(100%, 980px);
            display: flex;
            flex-direction: column;
            gap: 14px;
        }
        .top-controls {
            position: fixed;
            top: 16px;
            left: 16px;
            right: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            pointer-events: none;
            z-index: 20;
        }
        .top-chip {
            pointer-events: auto;
            border: none;
            border-radius: 999px;
            padding: 8px 12px;
            font: inherit;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            background: rgba(255, 255, 255, 0.92);
            color: #143d2d;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 8px 22px rgba(8, 30, 23, 0.18);
        }
        .chat-container {
            min-width: 0;
            height: calc(100vh - 120px);
            background: var(--panel);
            border-radius: 24px;
            box-shadow: 0 30px 80px rgba(8, 30, 23, 0.3);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .chat-header {
            background: linear-gradient(135deg, var(--forest), #156247);
            color: white;
            padding: 22px 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .session-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 999px;
            background: white;
            border: 1px solid var(--border);
            color: var(--forest);
            font-weight: 700;
            font-size: 14px;
        }
        .header-info { display: flex; align-items: center; gap: 16px; }
        .logo {
            width: 52px; height: 52px; border-radius: 16px;
            background: linear-gradient(145deg, var(--mint), var(--gold));
            color: var(--forest); display: flex; align-items: center; justify-content: center;
            font-weight: 800;
        }
        .chat-messages {
            flex: 1; overflow-y: auto; padding: 28px;
            background: linear-gradient(180deg, var(--paper), #fbfbfb);
        }
        .message { margin-bottom: 18px; display: flex; gap: 12px; }
        .message.user { flex-direction: row-reverse; }
        .message-avatar {
            width: 40px; height: 40px; border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; flex-shrink: 0;
        }
        .message.user .message-avatar { background: #156247; color: white; }
        .message.assistant .message-avatar { background: #e7f6eb; color: #0f5132; }
        .message-content {
            max-width: 74%; padding: 16px 18px; line-height: 1.6;
            border-radius: 18px; white-space: pre-wrap;
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
        }
        .message.user .message-content { background: #156247; color: white; }
        .message.assistant .message-content { background: white; color: var(--ink); border: 1px solid var(--border); }
        .message.assistant.progress-note .message-content {
            background: #f3f8f5;
            color: #36524a;
            font-style: italic;
            box-shadow: none;
        }
        .message.assistant.final-response .message-content strong { font-weight: 800; }
        .message.assistant.final-response .message-content em { font-style: italic; }
        .typing-indicator { display: none; padding: 14px 18px; background: white; border-radius: 16px; width: fit-content; }
        .typing-indicator.active { display: flex; gap: 6px; }
        .typing-dot { width: 8px; height: 8px; background: #156247; border-radius: 50%; animation: typing 1.4s infinite; }
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing { 0%,60%,100%{transform:translateY(0);opacity:0.4} 30%{transform:translateY(-7px);opacity:1} }
        .chat-input-container { padding: 22px 28px; background: white; border-top: 1px solid var(--border); }
        .chat-input-wrapper { display: flex; gap: 14px; }
        #userInput {
            flex: 1; border: 2px solid var(--border); border-radius: 28px;
            padding: 16px 20px; outline: none; font: inherit; background: #f8fcf9;
        }
        #sendButton {
            width: 54px; height: 54px; border: none; border-radius: 50%;
            background: linear-gradient(135deg, #156247, #2d7c5d);
            color: white; cursor: pointer; font-size: 20px;
        }
        .welcome-message { text-align: center; padding: 48px 24px; color: var(--muted); }
        .welcome-icon {
            width: 84px; height: 84px; margin: 0 auto 22px; border-radius: 26px;
            background: linear-gradient(145deg, #156247, #2d7c5d);
            color: white; display: flex; align-items: center; justify-content: center; font-size: 34px;
        }
        .upload-icon-btn {
            background: none;
            border: none;
            color: #156247;
            cursor: pointer;
            font-size: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0 8px;
            transition: all 0.2s;
            position: relative;
        }
        .upload-icon-btn:hover { color: #2d7c5d; transform: scale(1.1); }
        .spinner {
            display: none;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(21, 98, 71, 0.1);
            border-top: 3px solid #156247;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 8px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        @media (max-width: 980px) {
            body {
                padding: 14px;
            }
            .app-shell {
                flex-direction: column;
            }
            .chat-container {
                height: auto;
                min-height: 76vh;
            }
            .chat-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
            }
            .message-content {
                max-width: 88%;
            }
        }
    </style>
</head>
<body>
    <div class="top-controls">
        <a class="top-chip" href="/login">Sign in as admin</a>
        <button class="top-chip" id="logoutButton" type="button">Sign out</button>
    </div>
    <div class="app-shell">
        <div class="chat-container">
            <div class="chat-header">
                <div class="header-info">
                    <div class="logo">SAA</div>
                    <div>
                        <h1>Seagate Agent Assistant</h1>
                        <p style="font-size: 13px; opacity: 0.88;">Session: <span id="sessionIdDisplay">-</span></p>
                    </div>
                </div>
                <div style="font-size: 13px; opacity: 0.9; text-align: right;">
                    <div>DMR Partial Release & SSL Management</div>
                </div>
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="welcome-message">
                    <div class="welcome-icon">SAA</div>
                    <h2 style="margin-bottom: 10px; color: #0f5132;">Seagate Agent Assistant</h2>
                    <p>Try: triage DMR-INC-7003 for partial release or renew SSL certificate for woodsfswd.</p>
                </div>
                <div class="typing-indicator" id="typingIndicator">
                    <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
                </div>
            </div>
            <div class="chat-input-container">
                <div class="chat-input-wrapper">
                    <input type="file" id="csvUpload" style="display: none;" accept=".csv" onchange="handleFileUpload(this)">
                    <button class="upload-icon-btn" onclick="document.getElementById('csvUpload').click()" title="Upload DMR CSV">
                        📎
                        <div id="fileUploadCircle" style="display:none; position:absolute; right: -4px; top: 50%; transform: translateY(-50%); width: 8px; height: 8px; border-radius: 50%; border: 2px solid transparent; background-color: white;" title="CSV Uploaded"></div>
                    </button>
                    <input type="text" id="userInput" placeholder="Ask about DMR or SSL management..." autocomplete="off"/>
                    <button id="sendButton" type="button">></button>
                </div>
            </div>
        </div>
    </div>
    <script>
        const chatMessages = document.getElementById('chatMessages');
        const userInput = document.getElementById('userInput');
        const sendButton = document.getElementById('sendButton');
        const typingIndicator = document.getElementById('typingIndicator');
        const logoutButton = document.getElementById('logoutButton');
        const sessionSuffix = (typeof crypto !== 'undefined' && crypto.randomUUID)
            ? crypto.randomUUID()
            : Date.now().toString(36);
        let threadId = `seagate-thread-${sessionSuffix}`;
        document.getElementById('sessionIdDisplay').textContent = threadId;
        let isProcessing = false;
        let isChatReady = false;

        function setInputState(enabled, placeholder = null, keepInputEnabled = false) {
            userInput.disabled = keepInputEnabled ? false : !enabled;
            sendButton.disabled = !enabled;
            sendButton.style.pointerEvents = enabled ? 'auto' : 'none';
            sendButton.style.opacity = enabled ? '1' : '0.7';
            if (placeholder !== null) {
                userInput.placeholder = placeholder;
            }
        }

        function setSessionState(user) {
            const signedIn = Boolean(user && user.username);
            logoutButton.style.display = signedIn ? 'flex' : 'none';
            setInputState(signedIn, signedIn ? "Ask about DMR partial release resolution..." : "Sign in to begin...");
        }

        async function loadCurrentUser() {
            try {
                const response = await fetch('/auth/me');
                if (!response.ok) {
                    window.location.href = '/login';
                    return null;
                }
                const user = await response.json();
                setSessionState(user);
                return user;
            } catch (error) {
                window.location.href = '/login';
                return null;
            }
        }

        async function signOut() {
            try {
                await fetch('/auth/logout', { method: 'POST' });
            } finally {
                window.location.href = '/login';
            }
        }

        function escapeHtml(text) {
            return String(text ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function renderAssistantContent(text) {
            let html = escapeHtml(text);
            html = html.replace(/\\*\\*(.+?)\\*\\*/gs, '<strong>$1</strong>');
            html = html.replace(/\\*(.+?)\\*/gs, '<em>$1</em>');
            return html.replace(/\\n/g, '<br>');
        }

        function addMessage(role, content, extraClass = '') {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}${extraClass ? ` ${extraClass}` : ''}`;
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.textContent = role === 'user' ? 'U' : 'W';
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            if (role === 'assistant') {
                contentDiv.innerHTML = renderAssistantContent(content);
            } else {
                contentDiv.textContent = content;
            }
            messageDiv.appendChild(avatar);
            messageDiv.appendChild(contentDiv);
            chatMessages.insertBefore(messageDiv, typingIndicator);
            chatMessages.scrollTop = chatMessages.scrollHeight;
            return { messageDiv, contentDiv };
        }

        function appendProgressMessage(content) {
            return addMessage('assistant', content, 'progress-note');
        }

        function appendFinalResponse(content) {
            return addMessage('assistant', content, 'final-response');
        }

        function showTyping() { typingIndicator.classList.add('active'); }
        function hideTyping() { typingIndicator.classList.remove('active'); }

        let chatSocket = null;
        let finalAssistantBubble = null;
        let assistantMessage = '';

        function connectWebSocket() {
            return new Promise((resolve, reject) => {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                chatSocket = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);

                chatSocket.onopen = function(e) {
                    console.log("WebSocket connected.");
                    resolve();
                };

                chatSocket.onmessage = function(event) {
                    const parsed = JSON.parse(event.data);
                    
                    if (parsed.thread_id) {
                        threadId = parsed.thread_id;
                        document.getElementById('sessionIdDisplay').textContent = threadId;
                    }
                    if (parsed.type === 'progress') {
                        appendProgressMessage(parsed.content || parsed.message || '');
                        hideTyping();
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                    }
                    if (parsed.type === 'token') {
                        assistantMessage += parsed.content || '';
                        if (!finalAssistantBubble) {
                            finalAssistantBubble = appendFinalResponse(assistantMessage || 'No response received.');
                        } else {
                            finalAssistantBubble.contentDiv.innerHTML = renderAssistantContent(assistantMessage || 'No response received.');
                        }
                        hideTyping();
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                        if (isProcessing) {
                            isProcessing = false;
                            if (isChatReady) {
                                setInputState(true, "Ask about DMR or SSL management...");
                                userInput.focus();
                            }
                        }
                    }
                    if (parsed.type === 'end_of_response') {
                        assistantMessage = parsed.content || '';
                        if (!finalAssistantBubble) {
                            finalAssistantBubble = appendFinalResponse(assistantMessage || 'No response received.');
                        } else {
                            finalAssistantBubble.contentDiv.innerHTML = renderAssistantContent(assistantMessage || 'No response received.');
                        }
                        
                        if (isProcessing) {
                            isProcessing = false;
                            if (isChatReady) {
                                setInputState(true, "Ask about DMR or SSL management...");
                                userInput.focus();
                            }
                        }
                    }
                    if (parsed.error) {
                        hideTyping();
                        appendFinalResponse(`Sorry, there was an error: ${parsed.error}`);
                        if (isProcessing) {
                            isProcessing = false;
                            if (isChatReady) {
                                setInputState(true, "Ask about DMR partial release resolution...");
                                userInput.focus();
                            }
                        }
                    }
                };

                chatSocket.onclose = function(e) {
                    console.log("WebSocket closed.");
                };

                chatSocket.onerror = function(err) {
                    console.error("WebSocket error:", err);
                    reject(err);
                };
            });
        }

        async function sendMessage(messageOverride = null, showUserMessage = true, lockUi = true) {
            if (!messageOverride && !isChatReady) return;
            const message = (messageOverride ?? userInput.value).trim();
            if (!message || (lockUi && isProcessing)) return;
            if (lockUi) {
                isProcessing = true;
                setInputState(false, null, true);
            }
            if (!messageOverride) {
                userInput.value = '';
            }
            if (showUserMessage) {
                addMessage('user', message);
            }
            showTyping();
            const welcomeMsg = document.querySelector('.welcome-message');
            if (welcomeMsg) welcomeMsg.remove();
            
            finalAssistantBubble = null;
            assistantMessage = '';

            try {
                if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
                    await connectWebSocket();
                }
                
                /*
                // OLD HTTP POST LOGIC (Commented as requested)
                const response = await fetch('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        input: {
                            messages: [{ role: 'user', content: message }],
                            channel: 'web'
                        },
                        thread_id: threadId
                    })
                });
                */

                chatSocket.send(JSON.stringify({
                    input: {
                        messages: [{ role: 'user', content: message }],
                        channel: 'web'
                    },
                    thread_id: threadId
                }));
            } catch (error) {
                hideTyping();
                appendFinalResponse(`Sorry, there was an error: ${(error && error.message) ? error.message : 'Unable to connect to WebSocket server.'}`);
                if (lockUi) {
                    isProcessing = false;
                    if (isChatReady) {
                        setInputState(true, "Ask about DMR partial release resolution...");
                        userInput.focus();
                    } else {
                        setInputState(false, "Initializing assistant...");
                    }
                }
            }
        }

        userInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!isChatReady || isProcessing) return;
                sendMessage();
            }
        });

        async function initializeGreeting() {
            isChatReady = true;
            setInputState(true, "Ask about DMR partial release resolution...");
            userInput.focus();
            await sendMessage('hi', false, true);
        }



        async function handleFileUpload(input) {
            if (!input.files || input.files.length === 0) return;
            const file = input.files[0];
            const circle = document.getElementById('fileUploadCircle');
            
            circle.style.display = 'none';

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch(`/upload-csv?thread_id=${threadId}`, {
                    method: 'POST',
                    body: formData
                });
                if (response.ok) {
                    circle.style.display = 'block';
                    circle.style.borderColor = '#156247';
                    circle.style.backgroundColor = 'white';
                    circle.title = `${file.name} uploaded`;
                } else {
                    circle.style.display = 'block';
                    circle.style.borderColor = '#ff6b6b';
                    circle.style.backgroundColor = 'white';
                    circle.title = `Upload failed`;
                }
            } catch (err) {
                circle.style.display = 'block';
                circle.style.borderColor = '#ff6b6b';
                circle.style.backgroundColor = 'white';
                circle.title = `Error: ${err.message}`;
            }
        }

        function startChatBootstrap() {
            setInputState(false, "Checking session...");
            logoutButton.style.display = 'none';
            logoutButton.addEventListener('click', function() {
                void signOut();
            });
            sendButton.addEventListener('click', function() {
                if (!isChatReady || isProcessing) return;
                void sendMessage();
            });

            void loadCurrentUser().then((user) => {
                if (user) {
                    void initializeGreeting();
                }
            });
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', startChatBootstrap, { once: true });
        } else {
            startChatBootstrap();
        }
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
