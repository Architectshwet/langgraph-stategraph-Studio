from typing import Literal

from langchain_core.tools import tool
from langgraph.config import get_config, get_stream_writer

from src.services.soc_automation_service import soc_automation_service
from src.services.image_automation_service import image_automation_service
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
    """Introduce Seagate Agentic Assistant capabilities and available use cases."""
    thread_id = _get_thread_context()
    logger.info("[greeting] thread_id=%s", thread_id)
    result = {
        "next_action": (
            "Tell the user only this - Hello, I am Seagate Agentic Assistant for Seagate operations. "
            "I can help with DMR partial release resolution. "
            "Share your incident ID to begin."
        )
    }
    _log_next_action("greeting", result)
    return result


# --- USE CASE 1: SOC MANUAL INTERVENTION ---

@tool
async def collect_soc_manual_intervention_information() -> dict:
    """Step 1 of the SOC manual intervention flow."""
    thread_id = _get_thread_context()
    logger.info("[collect_soc_manual_intervention_information] thread_id=%s", thread_id)
    result = await soc_automation_service.collect_soc_manual_intervention_information()
    _log_next_action("collect_soc_manual_intervention_information", result)
    return result


@tool
async def triage_soc_manual_intervention(incident_id: str) -> dict:
    """Step 2 of the SOC manual intervention flow.

    Args:
        incident_id: SOC incident ID to review after collect_soc_manual_intervention_information.
    """
    thread_id = _get_thread_context()
    logger.info("[triage_soc_manual_intervention] thread_id=%s incident_id=%s", thread_id, incident_id)
    _emit_progress("Checking the SOC incident details now.")
    result = await soc_automation_service.triage_soc_manual_intervention(incident_id=incident_id)
    _emit_progress("SOC triage is ready.")
    _log_next_action("triage_soc_manual_intervention", result)
    return result


@tool
async def execute_soc_manual_intervention(
    incident_id: str,
    action: Literal["OPERATOR_ABORT", "FORCE_RUN_COMPLETE", "CASSETTE_RESET", "PURGE"] = "FORCE_RUN_COMPLETE",
    operator_confirmed: bool = False,
    approval_ticket: str = "",
) -> dict:
    """Step 3 of the SOC manual intervention flow.

    Args:
        incident_id: SOC incident ID to execute against.
        action: Guarded SOC action to perform.
        operator_confirmed: Whether the operator has confirmed execution.
        approval_ticket: Approval ticket if purge or policy requires one.

    After the tool returns, always ask the user for confirmation only if execution was not completed.
    """
    thread_id = _get_thread_context()
    logger.info(
        "[execute_soc_manual_intervention] thread_id=%s incident_id=%s action=%s operator_confirmed=%s approval_ticket=%s",
        thread_id,
        incident_id,
        action,
        operator_confirmed,
        approval_ticket,
    )
    _emit_progress("Preparing the guarded SOC execution checks.")
    result = await soc_automation_service.execute_soc_manual_intervention(
        incident_id=incident_id,
        action=action,
        operator_confirmed=operator_confirmed,
        approval_ticket=approval_ticket,
    )
    _emit_progress("SOC execution has finished.")
    _log_next_action("execute_soc_manual_intervention", result)
    return result


# --- USE CASE 2: IMAGE UPLOAD FAILURE ---

@tool
async def collect_image_upload_failure_information() -> dict:
    """Step 1 of the image upload failure flow."""
    thread_id = _get_thread_context()
    logger.info("[collect_image_upload_failure_information] thread_id=%s", thread_id)
    result = await image_automation_service.collect_image_upload_failure_information()
    _log_next_action("collect_image_upload_failure_information", result)
    return result


@tool
async def triage_image_upload_failure(incident_id: str) -> dict:
    """Step 2 of the image upload failure flow.

    Args:
        incident_id: Image upload failure incident ID to review after collect_image_upload_failure_information.
    """
    thread_id = _get_thread_context()
    logger.info("[triage_image_upload_failure] thread_id=%s incident_id=%s", thread_id, incident_id)
    _emit_progress("Reviewing the image upload failure now.")
    result = await image_automation_service.triage_image_upload_failure(incident_id=incident_id)
    _emit_progress("Checking the recovery signals and next action.")
    _emit_progress("Image upload failure triage is ready.")
    _log_next_action("triage_image_upload_failure", result)
    return result


@tool
async def execute_image_upload_failure(
    incident_id: str,
    action: Literal[
        "VERIFY_IMAGE_VIEWER",
        "COLLECT_IMAGES",
        "PREPARE_LOTLIST",
        "RUN_PROCESS_LOTLIST_CSV",
        "ESCALATE_TO_TOOL_OWNER",
    ] = "RUN_PROCESS_LOTLIST_CSV",
    operator_confirmed: bool = False,
    approval_ticket: str = "",
) -> dict:
    """Step 3 of the image upload failure flow.

    Args:
        incident_id: Image upload failure incident ID to execute against.
        action: Guarded recovery action to perform.
        operator_confirmed: Whether the operator has confirmed execution.
        approval_ticket: Approval ticket if required by the action.
    """
    thread_id = _get_thread_context()
    logger.info(
        "[execute_image_upload_failure] thread_id=%s incident_id=%s action=%s operator_confirmed=%s approval_ticket=%s",
        thread_id,
        incident_id,
        action,
        operator_confirmed,
        approval_ticket,
    )
    _emit_progress("Preparing the image recovery steps.")
    result = await image_automation_service.execute_image_upload_failure(
        incident_id=incident_id,
        action=action,
        operator_confirmed=operator_confirmed,
        approval_ticket=approval_ticket,
    )
    _emit_progress("Applying the recovery action.")
    _emit_progress("Image recovery action has finished.")
    _log_next_action("execute_image_upload_failure", result)
    return result


# --- USE CASE 3: DMR PARTIAL RELEASE RESOLUTION ---

@tool
async def collect_dmr_partial_release_information() -> dict:
    """Step 1 of the DMR partial release resolution flow (DMR already fully approved but FG still failed packdown)."""
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
    """Step 2 of the DMR partial release resolution flow. Verifies DMR/FG status and hold records.

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
    """Step 3 of the DMR partial release resolution flow. Executes the multi-step script to resolve the hold.

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
