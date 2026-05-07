from typing import Literal
from src.services.postgres_service import postgres_service

SOC_ACTION = Literal["OPERATOR_ABORT", "FORCE_RUN_COMPLETE", "CASSETTE_RESET", "PURGE"]


class SocAutomationService:
    """Service workflows for SOC manual intervention."""

    async def collect_soc_manual_intervention_information(self) -> dict:
        return {
            "next_action": (
                "If the user already provided the SOC incident ID, proceed calling triage_soc_manual_intervention tool.\n"
                "Otherwise, tell the user only this: Please share the SOC incident ID so that I can continue.\n"
                "Once the user provides the incident ID, proceed calling triage_soc_manual_intervention tool."
            )
        }

    async def triage_soc_manual_intervention(self, incident_id: str) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {
                "next_action": (
                    "Tell the user only this: provide a SOC incident ID such as SOC-INC-7002."
                )
            }

        incident = await postgres_service.get_soc_incident(resolved_incident_id)
        if not incident:
            return {"next_action": f"Tell the user only this: SOC incident {resolved_incident_id} was not found."}

        logs = await postgres_service.get_soc_logs(resolved_incident_id, limit=8)
        recommended_action, _ = self._recommend_soc_action(incident, logs)

        summary = self._soc_summary_line(incident, resolved_incident_id)
        safety = self._soc_safety_line(incident)
        recommendation = self._recommendation_line(recommended_action)
        return {
            "next_action": (
                "Tell the user only this:\n"
                f"- {summary}\n"
                f"- {safety}\n"
                f"- {recommendation}\n"
                f"- Do you want me to proceed with executing {recommended_action} on {resolved_incident_id}?\n"
                "Once the user confirms, proceed calling execute_soc_manual_intervention tool."
            )
        }

    async def execute_soc_manual_intervention(
        self,
        incident_id: str,
        action: SOC_ACTION | str = "FORCE_RUN_COMPLETE",
        operator_confirmed: bool = False,
        approval_ticket: str = "",
    ) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {"next_action": "Tell the user only this: provide a SOC incident ID before execution."}

        incident = await postgres_service.get_soc_incident(resolved_incident_id)
        if not incident:
            return {"next_action": f"Tell the user only this: SOC incident {resolved_incident_id} was not found."}

        logs = await postgres_service.get_soc_logs(resolved_incident_id, limit=8)
        recommended_action, _ = self._recommend_soc_action(incident, logs)
        chosen_action = str(action or recommended_action).strip().upper()

        allowed_actions = {"OPERATOR_ABORT", "FORCE_RUN_COMPLETE", "CASSETTE_RESET", "PURGE"}
        if chosen_action not in allowed_actions:
            return {
                "next_action": (
                    "Tell the user only this: choose one of OPERATOR_ABORT, FORCE_RUN_COMPLETE, CASSETTE_RESET, or PURGE."
                )
            }

        if chosen_action == "OPERATOR_ABORT":
            await postgres_service.record_soc_action(
                incident_id=resolved_incident_id,
                action_name=chosen_action,
                outcome="DEFERRED_TO_OPERATOR",
                approval_ticket=approval_ticket,
                operator_confirmed=operator_confirmed,
                notes="Operator abort was preferred over IT intervention.",
            )
            await postgres_service.update_soc_incident(
                incident_id=resolved_incident_id,
                current_status="WAITING_OPERATOR",
                recommended_action=chosen_action,
            )
            return {
                "next_action": (
                    f"Tell the user only this: {resolved_incident_id} should be handled by the operator Abort/Stop path."
                )
            }

        if not operator_confirmed:
            return {"next_action": "Tell the user only this: operator confirmation is required before execution."}

        if chosen_action == "PURGE":
            purge_violations: list[str] = []
            if bool(incident.get("abort_available")):
                purge_violations.append("abort is available")
            if int(incident.get("wafers_in_process") or 0) != 0:
                purge_violations.append(f"wafers in process are {int(incident.get('wafers_in_process') or 0)}")
            if not bool(incident.get("loadlocks_empty")):
                purge_violations.append("loadlocks are not empty")
            if not bool(incident.get("force_mda_enabled")):
                purge_violations.append("force MDA is not enabled")
            if bool(incident.get("special_lot")):
                purge_violations.append("this is a special lot")
            if not approval_ticket:
                purge_violations.append("approval ticket is missing")
            if purge_violations:
                return {
                    "next_action": self._blocked_action_line(
                        resolved_incident_id,
                        chosen_action,
                        purge_violations,
                        "SOC",
                    )
                }

        await postgres_service.record_soc_action(
            incident_id=resolved_incident_id,
            action_name=chosen_action,
            outcome="EXECUTED",
            approval_ticket=approval_ticket,
            operator_confirmed=operator_confirmed,
            notes=f"{chosen_action} executed with guarded SOC checks.",
        )
        await postgres_service.update_soc_incident(
            incident_id=resolved_incident_id,
            current_status="RESOLVED",
            recommended_action=chosen_action,
        )

        return {
            "next_action": (
                f"Tell the user only this: {chosen_action} was executed for {resolved_incident_id} and the incident is now resolved."
            )
        }

    def _soc_summary_line(self, incident: dict, incident_id: str) -> str:
        return (
            f"SOC incident {incident_id} is on tool {incident.get('tool_id')} for lot {incident.get('lot_id')}. "
            f"Run state is {incident.get('run_state')} and priority is {incident.get('priority')}."
        )

    def _soc_safety_line(self, incident: dict) -> str:
        parts: list[str] = []
        if "abort_available" in incident:
            parts.append("abort is available" if bool(incident.get("abort_available")) else "abort is unavailable")
        if "wafers_in_process" in incident:
            parts.append(f"wafers in process are {int(incident.get('wafers_in_process') or 0)}")
        if "loadlocks_empty" in incident:
            parts.append("loadlocks are empty" if bool(incident.get("loadlocks_empty")) else "loadlocks are not empty")
        if "force_mda_enabled" in incident:
            parts.append("force MDA is enabled" if bool(incident.get("force_mda_enabled")) else "force MDA is not enabled")
        if "special_lot" in incident:
            parts.append("this is a special lot" if bool(incident.get("special_lot")) else "this is not a special lot")
        return "Important checks: " + ", ".join(parts) + "."

    def _recommendation_line(self, recommended_action: str) -> str:
        return f"Recommended action is {recommended_action}."

    def _blocked_action_line(self, incident_id: str, action_name: str, failed_checks: list[str], context_label: str) -> str:
        check_text = ", ".join(failed_checks)
        return (
            f"Tell the user only this: {action_name} cannot proceed for {incident_id} because {check_text}. "
            f"Ask the user to review the {context_label} safety checks before trying again."
        )

    def _recommend_soc_action(self, incident: dict, logs: list[dict]) -> tuple[str, str]:
        if bool(incident.get("abort_available")):
            return "OPERATOR_ABORT", "Abort is available, so the operator path should be used first."

        if int(incident.get("wafers_in_process") or 0) > 0:
            return "CASSETTE_RESET", "Wafers are still in process, so a controlled cassette reset is safer."

        import json
        searchable_log_text = " ".join(
            f"{entry.get('event_code', '')} {entry.get('message', '')}".lower() for entry in logs
        )
        if "ceid" in searchable_log_text or "out-of-order" in searchable_log_text:
            return "FORCE_RUN_COMPLETE", "The logs point to missing or out-of-order CEIDs."

        if (
            ("f_abrt" in searchable_log_text or "invalid transition" in searchable_log_text)
            and bool(incident.get("loadlocks_empty"))
            and bool(incident.get("force_mda_enabled"))
            and not bool(incident.get("special_lot"))
        ):
            return "PURGE", "The state mismatch is severe and purge is eligible from the safety checks."

        fallback = str(incident.get("recommended_action") or "CASSETTE_RESET").upper()
        if fallback not in {"OPERATOR_ABORT", "FORCE_RUN_COMPLETE", "CASSETTE_RESET", "PURGE"}:
            fallback = "CASSETTE_RESET"
        return fallback, "Using the seeded SOC recommendation."


soc_automation_service = SocAutomationService()
