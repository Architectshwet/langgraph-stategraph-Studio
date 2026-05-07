import asyncio
import os
import uuid
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager, CallbackManager
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig

from src.db import postgres_repository
from src.db.db_singleton import DatabaseSingleton
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_callbacks(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (AsyncCallbackManager, CallbackManager)):
        return list(getattr(value, "handlers", []) or [])
    if isinstance(value, BaseCallbackHandler):
        return [value]
    if hasattr(value, "handlers"):
        handlers = getattr(value, "handlers", None)
        if isinstance(handlers, (list, tuple)):
            return list(handlers)
    return [value]


def _normalize_usage_from_message(usage: Any) -> dict[str, Any]:
    usage_dict = _as_mapping(usage)
    input_token_details = _as_mapping(usage_dict.get("input_token_details"))
    output_token_details = _as_mapping(usage_dict.get("output_token_details"))

    input_tokens = _safe_int(usage_dict.get("input_tokens") or usage_dict.get("prompt_tokens"))
    output_tokens = _safe_int(usage_dict.get("output_tokens") or usage_dict.get("completion_tokens"))
    total_tokens = _safe_int(usage_dict.get("total_tokens") or (input_tokens + output_tokens))
    cached_tokens = _safe_int(
        input_token_details.get("cache_read")
        or usage_dict.get("cached_tokens")
        or usage_dict.get("cache_read")
    )

    return {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "details": {
            "source": "message_usage_metadata",
            "input_token_details": input_token_details,
            "output_token_details": output_token_details,
            "raw": usage_dict,
        },
    }


def _normalize_usage_from_llm_output(llm_output: Any) -> dict[str, Any]:
    output = _as_mapping(llm_output)
    token_usage = _as_mapping(output.get("token_usage"))
    prompt_tokens_details = _as_mapping(token_usage.get("prompt_tokens_details") or token_usage.get("input_token_details"))
    output_tokens_details = _as_mapping(token_usage.get("completion_tokens_details") or token_usage.get("output_token_details"))

    input_tokens = _safe_int(
        token_usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
        or output.get("prompt_tokens")
        or output.get("input_tokens")
    )
    output_tokens = _safe_int(
        token_usage.get("completion_tokens")
        or token_usage.get("output_tokens")
        or output.get("completion_tokens")
        or output.get("output_tokens")
    )
    total_tokens = _safe_int(token_usage.get("total_tokens") or output.get("total_tokens") or (input_tokens + output_tokens))
    cached_tokens = _safe_int(
        prompt_tokens_details.get("cache_read")
        or token_usage.get("cached_tokens")
        or output.get("cached_tokens")
    )

    return {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "details": {
            "source": "llm_output",
            "token_usage": token_usage,
            "prompt_tokens_details": prompt_tokens_details,
            "output_tokens_details": output_tokens_details,
            "raw": output,
        },
    }


def _extract_usage(result: LLMResult) -> dict[str, Any]:
    for generation_list in result.generations or []:
        for generation in generation_list or []:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return _normalize_usage_from_message(usage)

    if result.llm_output:
        return _normalize_usage_from_llm_output(result.llm_output)

    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "details": {"source": "missing"},
    }


class LLMUsageService:
    def __init__(self):
        self._initialized = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def initialize(self):
        if self._initialized:
            return
        self._loop = asyncio.get_running_loop()
        await DatabaseSingleton.get_pool()
        await postgres_repository.initialize_llm_usage_table()
        self._initialized = True
        logger.info("LLM usage service initialized")

    async def record_usage(
        self,
        *,
        session_id: str,
        thread_id: str,
        node_name: str,
        model_name: str,
        run_id: str,
        parent_run_id: str = "",
        input_tokens: int = 0,
        cached_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._initialized:
            logger.warning("LLM usage service not initialized, skipping usage save")
            return {"success": False, "error": "Service not initialized"}

        try:
            return await postgres_repository.insert_llm_usage_record(
                session_id=session_id or "unknown-session",
                thread_id=thread_id or session_id or "unknown-thread",
                node_name=node_name or "unknown-node",
                model_name=model_name or "",
                run_id=run_id or str(uuid.uuid4()),
                parent_run_id=parent_run_id or "",
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                details=details or {},
            )
        except Exception as exc:
            logger.error("Failed to record LLM usage for session %s: %s", session_id, exc)
            return {"success": False, "error": str(exc)}

    def build_config(
        self,
        config: RunnableConfig | None,
        node_name: str,
        model_name: str | None = None,
        thread_id_override: str | None = None,
    ) -> dict[str, Any]:
        current = dict(config or {})
        configurable = dict(current.get("configurable", {}) or {})
        session_id = str(configurable.get("parent_thread_id") or configurable.get("thread_id") or "").strip()
        thread_id = str(thread_id_override or configurable.get("thread_id") or session_id).strip()
        metadata = dict(current.get("metadata", {}) or {})
        metadata.update(
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "node_name": node_name,
                "model_name": model_name or os.getenv("OPENAI_MODEL", "gpt-5.1"),
            }
        )
        callbacks = _normalize_callbacks(current.get("callbacks"))
        callbacks.append(
            LLMUsageCallbackHandler(
                service=self,
                default_session_id=session_id,
                default_thread_id=thread_id,
                default_node_name=node_name,
                default_model_name=metadata["model_name"],
            )
        )
        current["callbacks"] = callbacks
        current["metadata"] = metadata
        return current


class LLMUsageCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        *,
        service: LLMUsageService,
        default_session_id: str,
        default_thread_id: str,
        default_node_name: str,
        default_model_name: str,
    ):
        self._service = service
        self._default_session_id = default_session_id
        self._default_thread_id = default_thread_id
        self._default_node_name = default_node_name
        self._default_model_name = default_model_name
        self._runs: dict[str, dict[str, str]] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs: Any,
    ) -> Any:
        run_key = str(run_id)
        runtime_metadata = _as_mapping(metadata)
        model_name = str(
            runtime_metadata.get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("name")
            or self._default_model_name
        )
        self._runs[run_key] = {
            "session_id": str(runtime_metadata.get("session_id") or self._default_session_id),
            "thread_id": str(runtime_metadata.get("thread_id") or self._default_thread_id),
            "node_name": str(runtime_metadata.get("node_name") or self._default_node_name),
            "model_name": model_name,
            "parent_run_id": str(parent_run_id) if parent_run_id else "",
        }

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        **kwargs: Any,
    ) -> Any:
        run_key = str(run_id)
        run_info = self._runs.pop(run_key, {})
        usage = _extract_usage(response)
        parent_id = str(parent_run_id) if parent_run_id else run_info.get("parent_run_id", "")
        coro = self._service.record_usage(
            session_id=run_info.get("session_id", self._default_session_id),
            thread_id=run_info.get("thread_id", self._default_thread_id),
            node_name=run_info.get("node_name", self._default_node_name),
            model_name=run_info.get("model_name", self._default_model_name),
            run_id=run_key,
            parent_run_id=parent_id,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            cached_tokens=int(usage.get("cached_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            details=dict(usage.get("details", {})),
        )
        service_loop = self._service._loop
        if not service_loop or not service_loop.is_running():
            logger.warning("LLM usage loop is not available, skipping usage write for run %s", run_key)
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is service_loop:
            service_loop.create_task(coro)
            return

        future = asyncio.run_coroutine_threadsafe(coro, service_loop)

        def _log_future_result(done_future):
            try:
                done_future.result()
            except Exception as exc:
                logger.error("LLM usage write failed for run %s: %s", run_key, exc)

        future.add_done_callback(_log_future_result)


llm_usage_service = LLMUsageService()
