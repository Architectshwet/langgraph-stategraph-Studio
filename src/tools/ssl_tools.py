from typing import Optional
from langchain_core.tools import tool
from langgraph.config import get_config, get_stream_writer
from src.services.ssl_automation_service import ssl_automation_service
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
async def collect_ssl_information() -> dict:
    """Collects initial information for SSL certificate renewal or installation."""
    thread_id = _get_thread_context()
    store = get_store()
    logger.info("[collect_ssl_information] thread_id=%s", thread_id)
    result = await ssl_automation_service.collect_ssl_information(
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("collect_ssl_information", result)
    return result

@tool
async def create_csr_linux(
    server_name: str,
    common_name: str,
    incident_id: Optional[str] = None,
    san_hostnames: Optional[str] = "",
) -> dict:
    """Creates a CSR for Linux/OpenSSL following the step-by-step procedure.
    
    Args:
        server_name: Name of the server.
        common_name: Common Name (FQDN) for the certificate.
        incident_id: Optional incident ID if already exists.
        san_hostnames: Optional comma-separated list of Subject Alternative Names.
    """
    thread_id = _get_thread_context()
    store = get_store()
    _emit_progress(f"Generating CSR for {common_name} using Linux procedure.")
    result = await ssl_automation_service.create_csr_linux(
        incident_id=incident_id,
        server_name=server_name,
        common_name=common_name,
        san_hostnames=san_hostnames,
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("create_csr_linux", result)
    return result

@tool
async def create_csr_windows(
    server_name: str,
    common_name: str,
    incident_id: Optional[str] = None,
    san_hostnames: Optional[str] = "",
) -> dict:
    """Creates a CSR for Windows/MMC following the step-by-step procedure.
    
    Args:
        server_name: Name of the server.
        common_name: Common Name (FQDN) for the certificate.
        incident_id: Optional incident ID if already exists.
        san_hostnames: Optional comma-separated list of Subject Alternative Names.
    """
    thread_id = _get_thread_context()
    store = get_store()
    _emit_progress(f"Generating CSR for {common_name} using Windows procedure.")
    result = await ssl_automation_service.create_csr_windows(
        incident_id=incident_id,
        server_name=server_name,
        common_name=common_name,
        san_hostnames=san_hostnames,
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("create_csr_windows", result)
    return result

@tool
async def raise_ssl_ticket(
    incident_id: str,
) -> dict:
    """Raises a ticket in Freshservice for the SSL certificate request.
    
    Args:
        incident_id: The SSL incident ID.
    """
    thread_id = _get_thread_context()
    store = get_store()
    _emit_progress(f"Raising Freshservice ticket for incident {incident_id}.")
    result = await ssl_automation_service.raise_ssl_ticket(
        incident_id=incident_id,
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("raise_ssl_ticket", result)
    return result

@tool
async def install_certificate(
    incident_id: str,
    cer_file_content: str,
) -> dict:
    """Installs the received .cer file on the IIS server.
    
    Args:
        incident_id: The SSL incident ID.
        cer_file_content: The content of the .cer file received from SSL Admin.
    """
    thread_id = _get_thread_context()
    store = get_store()
    _emit_progress(f"Installing certificate for incident {incident_id}.")
    result = await ssl_automation_service.install_certificate(
        incident_id=incident_id,
        cer_file_content=cer_file_content,
        thread_id=thread_id,
        store=store,
    )
    _log_next_action("install_certificate", result)
    return result
