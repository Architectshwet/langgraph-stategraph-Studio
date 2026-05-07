import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from langchain_core.messages import HumanMessage
from jose import jwt, JWTError

from src.utils.logger import get_logger
from src.auth import SECRET_KEY, ALGORITHM, COOKIE_NAME

logger = get_logger(__name__)
ws_router = APIRouter()

@ws_router.websocket("/ws/chat")
async def websocket_chat_endpoint(
    websocket: WebSocket,
    token: str | None = Query(None)
):
    # Import locally to avoid circular import issues
    from src.server import (
        global_agent, 
        db_service, 
        normalize_input,
        _parse_stream_event,
        _extract_stream_progress,
        _extract_last_message_from_chain_output,
        DMR_PREFIX,
        SSL_PREFIX
    )
    from src.services.langfuse_service import langfuse_service
    from src.state.store import get_session, get_store, set_session

    # --- Authentication ---
    # Check cookie first (for UI), then query parameter (for external clients)
    auth_token = websocket.cookies.get(COOKIE_NAME) or token
    
    if not auth_token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")
        return

    try:
        payload = jwt.decode(auth_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
            return
    except JWTError as e:
        logger.warning(f"WebSocket JWT decode failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    # Accept the connection
    await websocket.accept()
    architecture = "StateGraph-WebSocket"
    
    try:
        while True:
            # Wait for user message
            raw_data = await websocket.receive_text()
            try:
                request_data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON payload"})
                continue
            
            # --- 1. Parse Input & Log User Message ---
            input_dict = request_data.get("input", {})
            conversation_id = request_data.get("thread_id") or f"seagate-thread-{str(uuid.uuid4())[:8]}"
            normalized_input = normalize_input(input_dict)
            
            # --- Session Data ---
            store = get_store()
            session_data = await get_session(store, conversation_id)
            session_data["channel"] = input_dict.get("channel", "web")
            session_data["architecture"] = architecture
            session_data["auth_user"] = {"username": username, "role": payload.get("role", "user")}
            await set_session(store, conversation_id, session_data)
            
            # Extract text to log user message (same as your POST endpoint)
            user_message_content = ""
            for msg in normalized_input.get("messages", []):
                if isinstance(msg, HumanMessage):
                    user_message_content = msg.content
                elif isinstance(msg, dict) and msg.get("role") == "user":
                    user_message_content = msg.get("content", "")
            
            if user_message_content:
                logger.info("[user_message] thread_id=%s content=%s", conversation_id, user_message_content)
            
            await db_service.append_conversation_message(
                conversation_id=conversation_id,
                role="user",
                content=user_message_content,
                agent_name="User",
            )

            # --- 2. Setup Configuration ---
            config: dict[str, Any] = {"configurable": {"thread_id": conversation_id}}
            langfuse_handler = langfuse_service.get_handler()
            if langfuse_handler:
                config["callbacks"] = [langfuse_handler]

            # Yield initial session info
            await websocket.send_json({
                "thread_id": conversation_id, 
                "type": "session", 
                "architecture": architecture, 
                "timestamp": time.time()
            })

            # --- 3. Stream Agent Execution ---
            assistant_response = ""
            streamed_token = False
            agent_name = "SeagateAgentAssistant"

            try:
                graph_input = {
                    **normalized_input,
                    "loop_count": 0,
                }

                # EXACT MATCH of your astream loop
                async for chunk in global_agent.astream(
                    graph_input,
                    config=config,
                    stream_mode=["custom", "updates"],
                    version="v2",
                ):
                    chunk_type, chunk_data = _parse_stream_event(chunk)
                    
                    if chunk_type == "custom":
                        progress_text = _extract_stream_progress(chunk_data)
                        if progress_text:
                            await websocket.send_json({
                                "thread_id": conversation_id, 
                                "type": "progress", 
                                "content": progress_text, 
                                "timestamp": time.time()
                            })

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
                                    # Send the final text as a token
                                    await websocket.send_json({
                                        "thread_id": conversation_id, 
                                        "type": "token", 
                                        "content": assistant_response, 
                                        "timestamp": time.time()
                                    })
                                    streamed_token = True

                # --- 4. Final Cleanup & DB Append ---
                if not assistant_response.strip():
                    assistant_response = "I completed the request, but no final text response was generated."
                
                # If no token was streamed during the loop, send a final one now
                if not streamed_token:
                    await websocket.send_json({
                        "thread_id": conversation_id, 
                        "type": "token", 
                        "content": assistant_response, 
                        "timestamp": time.time()
                    })
                    streamed_token = True

                logger.info(
                    "[assistant_response] thread_id=%s agent=%s content=%s",
                    conversation_id,
                    agent_name,
                    assistant_response,
                )
                
                # Log assistant response to background task to avoid UI lag
                asyncio.create_task(db_service.append_conversation_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_response,
                    agent_name=agent_name,
                ))

            except Exception as exc:
                logger.error("Error in streaming: %s", exc)
                await websocket.send_json({
                    "thread_id": conversation_id, 
                    "error": str(exc), 
                    "type": type(exc).__name__
                })

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
