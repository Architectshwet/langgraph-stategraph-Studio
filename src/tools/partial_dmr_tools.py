from typing import Literal

from langchain_core.tools import tool
from langgraph.config import get_config, get_stream_writer

from src.services.dmr_partial_release_automation_service import dmr_partial_release_automation_service
from src.state.store import get_store
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _get_thread_context() -> str:
    config = get_config()
    configurable = config.get("configurable", {}) or {}
    return str(configurable.get("thread_id") or "")


def _log_next_action(tool_name: str, result: dict) -> None:
    logger.info("[%s] next_action=%s", tool_name, result.get("next_action", ""))


def _emit_progress(message: str) -> None:
    try:
        writer = get_stream_writer()
        writer({"type": "progress", "content": message})
    except Exception:
        logger.info("[progress_fallback] %s", message)


@tool
async def greeting() -> dict:
    """Introduce Seagate Agent Assistant capabilities and available use cases."""
    thread_id = _get_thread_context()
    logger.info("[greeting] thread_id=%s", thread_id)
    result = {
        "next_action": (
            "Tell the user only this - Hello, I am Seagate Agent Assistant for Seagate operations. "
            "I can help with DMR partial release resolution. "
            "Share your incident ID to begin."
        )
    }
    _log_next_action("greeting", result)
    return result


@tool
async def collect_dmr_partial_release_information() -> dict:
    """Step 1 of the DMR partial release resolution flow. Collects context and information for the partial release."""
    thread_id = _get_thread_context()
    store = get_store()
    logger.info("[collect_dmr_partial_release_information] thread_id=%s", thread_id)
    result = await dmr_partial_release_automation_service.collect_dmr_partial_release_information(
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("collect_dmr_partial_release_information", result)
    return result


@tool
async def triage_dmr_partial_release(
    incident_id: str,
) -> dict:
    """Step 2 of the DMR partial release resolution flow. Verifies if the incident is eligible for a partial release.

    Args:
        incident_id: Partial DMR incident ID to execute against.
    """
    thread_id = _get_thread_context()
    store = get_store()
    logger.info(
        "[triage_dmr_partial_release] thread_id=%s incident_id=%s",
        thread_id,
        incident_id,
    )
    _emit_progress("Verifying the DMR status and hold record flags.")
    result = await dmr_partial_release_automation_service.triage_dmr_partial_release(
        incident_id=incident_id,
        thread_id=thread_id,
        store=store,
    )
    _emit_progress("DMR partial release verification is ready.")
    _log_next_action("triage_dmr_partial_release", result)
    return result


@tool
async def perform_dmr_partial_release_resolution(
    incident_id: str,
    operator_confirmed: bool = False,
) -> dict:
    """Step 3 of the DMR partial release resolution flow. Performs the partial release resolution.

    Args:
        incident_id: Partial DMR incident ID to execute against.
        operator_confirmed: Whether the operator has confirmed execution.
    """
    thread_id = _get_thread_context()
    store = get_store()
    logger.info(
        "[perform_dmr_partial_release_resolution] thread_id=%s incident_id=%s",
        thread_id,
        incident_id,
    )
    _emit_progress("Executing the step-by-step partial release resolution.")
    result = await dmr_partial_release_automation_service.execute_dmr_partial_release(
        incident_id=incident_id,
        thread_id=thread_id,
        store=store,
        operator_confirmed=operator_confirmed,
    )
    _emit_progress("DMR partial release resolution has finished.")
    _log_next_action("perform_dmr_partial_release_resolution", result)
    return result
