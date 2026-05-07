from typing import Literal
from src.services.postgres_service import postgres_service

IMAGE_UPLOAD_FAILURE_ACTION = Literal[
    "VERIFY_IMAGE_VIEWER",
    "COLLECT_IMAGES",
    "PREPARE_LOTLIST",
    "RUN_PROCESS_LOTLIST_CSV",
    "ESCALATE_TO_TOOL_OWNER",
]


class ImageAutomationService:
    """Service workflows for image upload failure recovery."""

    async def collect_image_upload_failure_information(self) -> dict:
        return {
            "next_action": (
                "If the user already provided the image upload failure incident ID, proceed calling triage_image_upload_failure tool.\n"
                "Otherwise, tell the user only this: Please share the image upload failure incident ID so that I can continue.\n"
                "Once the user provides the incident ID, proceed calling triage_image_upload_failure tool."
            )
        }

    async def triage_image_upload_failure(self, incident_id: str) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {
                "next_action": "Tell the user only this: please share the image upload failure incident ID so that I can continue."
            }

        incident = await postgres_service.get_image_upload_failure_incident(resolved_incident_id)
        if not incident:
            return {
                "next_action": f"Tell the user only this: image upload failure incident {resolved_incident_id} was not found."
            }

        logs = await postgres_service.get_image_upload_failure_logs(resolved_incident_id, limit=8)
        recommended_action, _ = self._recommend_image_upload_failure_action(incident, logs)

        summary = self._image_upload_failure_summary_line(incident, resolved_incident_id)
        recovery = self._image_upload_failure_recovery_line(incident)
        recommendation = self._recommendation_line(recommended_action)
        return {
            "next_action": (
                f"Tell the user only this: {summary} {recovery} {recommendation} "
                "Reply with `proceed` to execute the recommended recovery action, or provide a different recovery action.\n"
                "Once the user confirms, proceed calling execute_image_upload_failure tool."
            )
        }

    async def execute_image_upload_failure(
        self,
        incident_id: str,
        action: IMAGE_UPLOAD_FAILURE_ACTION | str = "RUN_PROCESS_LOTLIST_CSV",
        operator_confirmed: bool = False,
        approval_ticket: str = "",
    ) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {
                "next_action": "Tell the user only this: provide an image upload failure incident ID before execution."
            }

        incident = await postgres_service.get_image_upload_failure_incident(resolved_incident_id)
        if not incident:
            return {
                "next_action": f"Tell the user only this: image upload failure incident {resolved_incident_id} was not found."
            }

        logs = await postgres_service.get_image_upload_failure_logs(resolved_incident_id, limit=8)
        recommended_action, _ = self._recommend_image_upload_failure_action(incident, logs)
        chosen_action = str(action or recommended_action).strip().upper()

        allowed_actions = {
            "VERIFY_IMAGE_VIEWER",
            "COLLECT_IMAGES",
            "PREPARE_LOTLIST",
            "RUN_PROCESS_LOTLIST_CSV",
            "ESCALATE_TO_TOOL_OWNER",
        }
        if chosen_action not in allowed_actions:
            return {
                "next_action": (
                    "Tell the user only this: choose one of VERIFY_IMAGE_VIEWER, COLLECT_IMAGES, PREPARE_LOTLIST, "
                    "RUN_PROCESS_LOTLIST_CSV, or ESCALATE_TO_TOOL_OWNER."
                )
            }

        if chosen_action != "ESCALATE_TO_TOOL_OWNER" and not operator_confirmed:
            return {
                "next_action": "Tell the user only this: operator confirmation is required before recovery execution."
            }

        failed_checks: list[str] = []
        if chosen_action in {"COLLECT_IMAGES", "RUN_PROCESS_LOTLIST_CSV"} and not bool(incident.get("images_on_host")):
            failed_checks.append("images are not on the SOC host")
        if chosen_action == "RUN_PROCESS_LOTLIST_CSV" and not bool(incident.get("working_directory_ready")):
            failed_checks.append("the recovery workspace is not ready")
        if chosen_action == "VERIFY_IMAGE_VIEWER" and not bool(incident.get("image_viewer_available")):
            failed_checks.append("Image Viewer is not available")

        if failed_checks and chosen_action != "ESCALATE_TO_TOOL_OWNER":
            return {
                "next_action": self._blocked_action_line(
                    resolved_incident_id,
                    chosen_action,
                    failed_checks,
                    "image upload failure",
                )
            }

        await postgres_service.record_image_upload_failure_action(
            incident_id=resolved_incident_id,
            action_name=chosen_action,
            outcome="ESCALATED" if chosen_action == "ESCALATE_TO_TOOL_OWNER" else "EXECUTED",
            approval_ticket=approval_ticket,
            operator_confirmed=operator_confirmed,
            notes=f"{chosen_action} executed for image upload recovery.",
        )
        await postgres_service.update_image_upload_failure_incident(
            incident_id=resolved_incident_id,
            current_status="RESOLVED" if chosen_action in {"VERIFY_IMAGE_VIEWER", "RUN_PROCESS_LOTLIST_CSV"} else "IN_PROGRESS",
            recommended_action=chosen_action,
        )

        return {
            "next_action": f"Tell the user only this: {chosen_action} was applied for {resolved_incident_id}."
        }

    def _image_upload_failure_summary_line(self, incident: dict, incident_id: str) -> str:
        return (
            f"Image upload failure incident {incident_id} is tied to tool {incident.get('tool_name')} on lot {incident.get('lot_id')}. "
            f"Stage is {incident.get('stage')} and step is {incident.get('step_name')}."
        )

    def _image_upload_failure_recovery_line(self, incident: dict) -> str:
        parts: list[str] = []
        if "image_viewer_available" in incident:
            parts.append("Image Viewer is available" if bool(incident.get("image_viewer_available")) else "Image Viewer is not available")
        if "images_on_host" in incident:
            parts.append("images are on the SOC host" if bool(incident.get("images_on_host")) else "images are not on the SOC host")
        if "rolling_log_has_failure" in incident:
            parts.append(
                "Rolling.log shows an ImageManager failure"
                if bool(incident.get("rolling_log_has_failure"))
                else "Rolling.log does not show an ImageManager failure"
            )
        if "working_directory_ready" in incident:
            parts.append("the recovery workspace is ready" if bool(incident.get("working_directory_ready")) else "the recovery workspace is not ready")
        return "Recovery check: " + ", ".join(parts) + "."

    def _recommendation_line(self, recommended_action: str) -> str:
        return f"Recommended action is {recommended_action}."

    def _blocked_action_line(self, incident_id: str, action_name: str, failed_checks: list[str], context_label: str) -> str:
        check_text = ", ".join(failed_checks)
        return (
            f"Tell the user only this: {action_name} cannot proceed for {incident_id} because {check_text}. "
            f"Ask the user to review the {context_label} safety checks before trying again."
        )

    def _recommend_image_upload_failure_action(self, incident: dict, logs: list[dict]) -> tuple[str, str]:
        if bool(incident.get("image_viewer_available")):
            return "VERIFY_IMAGE_VIEWER", "The Image Viewer already shows the image set."

        if bool(incident.get("images_on_host")) and bool(incident.get("working_directory_ready")):
            return "RUN_PROCESS_LOTLIST_CSV", "Images are available locally and the recovery workspace is ready."

        if bool(incident.get("images_on_host")):
            return "COLLECT_IMAGES", "Copy the failed images into the recovery workspace first."

        if bool(incident.get("rolling_log_has_failure")):
            return "PREPARE_LOTLIST", "The Rolling.log failure needs a fresh LotList.csv before replay."

        if any("ImageManager" in str(entry.get("source_system", "")) for entry in logs):
            return "ESCALATE_TO_TOOL_OWNER", "The alert is present but no local recovery path is confirmed."

        fallback = str(incident.get("recommended_action") or "ESCALATE_TO_TOOL_OWNER").upper()
        if fallback not in {
            "VERIFY_IMAGE_VIEWER",
            "COLLECT_IMAGES",
            "PREPARE_LOTLIST",
            "RUN_PROCESS_LOTLIST_CSV",
            "ESCALATE_TO_TOOL_OWNER",
        }:
            fallback = "ESCALATE_TO_TOOL_OWNER"
        return fallback, "Using the seeded recovery recommendation."


image_automation_service = ImageAutomationService()
