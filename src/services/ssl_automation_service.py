import os
import uuid
from src.services.postgres_service import postgres_service
from src.state.store import AsyncRedisStore, get_session, set_session
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SslAutomationService:
    """Service workflows for SSL Certificate Renewal and Installation."""

    async def collect_ssl_information(
        self,
        thread_id: str,
        store: AsyncRedisStore,
    ) -> dict:
        return {
            "next_action": (
                "If the user wants to renew or install an SSL certificate, ask for the server name and common name (FQDN).\n"
                "If they have already provided it, proceed to create the CSR (Linux or Windows).\n"
                "Available tools: create_csr_linux, create_csr_windows."
            )
        }

    async def create_csr_linux(
        self,
        incident_id: str,
        server_name: str,
        common_name: str,
        san_hostnames: str = "",
        thread_id: str = "",
        store: AsyncRedisStore = None,
    ) -> dict:
        # Mocking the creation of CSR
        csr_content = f"-----BEGIN CERTIFICATE REQUEST-----\n{uuid.uuid4().hex}\n-----END CERTIFICATE REQUEST-----"
        
        incident_data = {
            "incident_id": incident_id or f"SSL-INC-{uuid.uuid4().hex[:6].upper()}",
            "server_name": server_name,
            "common_name": common_name,
            "san_hostnames": san_hostnames,
            "current_status": "CSR_CREATED",
        }
        
        # Save or update incident
        existing = await postgres_service.get_ssl_incident(incident_data["incident_id"])
        if existing:
            await postgres_service.update_ssl_incident(incident_data["incident_id"], {"csr_content": csr_content, "current_status": "CSR_CREATED"})
        else:
            await postgres_service.create_ssl_incident(incident_data)
            await postgres_service.update_ssl_incident(incident_data["incident_id"], {"csr_content": csr_content})

        await postgres_service.record_ssl_action(
            incident_id=incident_data["incident_id"],
            action_name="CREATE_CSR_LINUX",
            outcome="EXECUTED",
            operator_confirmed=True
        )

        return {
            "next_action": (
                f"Tell the user: CSR for {common_name} has been created using Linux/OpenSSL procedure.\n"
                f"CSR content: {csr_content}\n"
                "Next step: Raise a ticket in Freshservice and attach this CSR file.\n"
                "Proceed calling raise_ssl_ticket tool."
            )
        }

    async def create_csr_windows(
        self,
        incident_id: str,
        server_name: str,
        common_name: str,
        san_hostnames: str = "",
        thread_id: str = "",
        store: AsyncRedisStore = None,
    ) -> dict:
        # Mocking the creation of CSR via MMC
        csr_content = f"-----BEGIN NEW CERTIFICATE REQUEST-----\n{uuid.uuid4().hex}\n-----END NEW CERTIFICATE REQUEST-----"
        
        incident_data = {
            "incident_id": incident_id or f"SSL-INC-{uuid.uuid4().hex[:6].upper()}",
            "server_name": server_name,
            "common_name": common_name,
            "san_hostnames": san_hostnames,
            "current_status": "CSR_CREATED",
        }
        
        # Save or update incident
        existing = await postgres_service.get_ssl_incident(incident_data["incident_id"])
        if existing:
            await postgres_service.update_ssl_incident(incident_data["incident_id"], {"csr_content": csr_content, "current_status": "CSR_CREATED"})
        else:
            await postgres_service.create_ssl_incident(incident_data)
            await postgres_service.update_ssl_incident(incident_data["incident_id"], {"csr_content": csr_content})

        await postgres_service.record_ssl_action(
            incident_id=incident_data["incident_id"],
            action_name="CREATE_CSR_WINDOWS",
            outcome="EXECUTED",
            operator_confirmed=True
        )

        return {
            "next_action": (
                f"Tell the user: CSR for {common_name} has been created using Windows MMC procedure.\n"
                f"CSR content: {csr_content}\n"
                "Next step: Raise a ticket in Freshservice and attach this CSR file.\n"
                "Proceed calling raise_ssl_ticket tool."
            )
        }

    async def raise_ssl_ticket(
        self,
        incident_id: str,
        thread_id: str,
        store: AsyncRedisStore,
    ) -> dict:
        incident = await postgres_service.get_ssl_incident(incident_id)
        if not incident or not incident.get("csr_content"):
            return {"next_action": "Tell the user: CSR content is missing. Please create CSR first."}

        # Mocking ticket raising
        ticket_id = f"FS-{uuid.uuid4().hex[:8].upper()}"
        
        await postgres_service.update_ssl_incident(incident_id, {"current_status": "TICKET_RAISED"})
        await postgres_service.record_ssl_action(
            incident_id=incident_id,
            action_name="RAISE_SSL_TICKET",
            outcome=f"TICKET_CREATED: {ticket_id}",
            operator_confirmed=True
        )

        return {
            "next_action": (
                f"Tell the user: A ticket {ticket_id} has been raised in Freshservice with the CSR attached.\n"
                "Please inform Kim Siang (kimsiang.kang@seagate.com) or SSL Admin about the ticket.\n"
                "They will send back the .cer file. Once you receive it, share it here to proceed with installation."
            )
        }

    async def install_certificate(
        self,
        incident_id: str,
        cer_file_content: str,
        thread_id: str,
        store: AsyncRedisStore,
    ) -> dict:
        incident = await postgres_service.get_ssl_incident(incident_id)
        if not incident:
            return {"next_action": "Tell the user: SSL incident not found."}

        # Mocking installation on IIS
        await postgres_service.update_ssl_incident(incident_id, {
            "cer_content": cer_file_content,
            "current_status": "INSTALLED"
        })
        await postgres_service.record_ssl_action(
            incident_id=incident_id,
            action_name="INSTALL_CERTIFICATE",
            outcome="SUCCESSFULLY_INSTALLED",
            operator_confirmed=True
        )

        return {
            "next_action": (
                f"Tell the user: The certificate for {incident['common_name']} has been successfully installed on {incident['server_name']}.\n"
                "HTTPS binding has been updated in IIS Manager.\n"
                "Verification successful."
            )
        }

ssl_automation_service = SslAutomationService()
