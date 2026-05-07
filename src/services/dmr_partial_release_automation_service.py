import os
import pandas as pd
from src.services.postgres_service import postgres_service
from src.state.store import AsyncRedisStore, get_session, set_session


class DmrPartialReleaseAutomationService:
    """Service workflows for DMR Partial Release resolution following documented procedure."""

    async def collect_dmr_partial_release_information(
        self,
        thread_id: str,
        store: AsyncRedisStore,
    ) -> dict:
        return {
            "next_action": (
                "If the user already provided the partial DMR incident ID, proceed calling triage_dmr_partial_release tool.\n"
                "Otherwise, tell the user only this: Please share the partial DMR incident ID so that I can proceed with partial release of DMR.\n"
                "Once the user provides the incident ID, proceed calling triage_dmr_partial_release tool."
            )
        }

    async def triage_dmr_partial_release(
        self,
        incident_id: str,
        thread_id: str,
        store: AsyncRedisStore,
    ) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {"next_action": "Tell the user only this: Please provide a partial DMR incident ID to proceed with partial release of DMR."}

        # Step 1: Check for and read the uploaded CSV file (cassette list)
        cassette_ids = []
        file_path = os.path.join("uploads", f"{thread_id}_dmr.csv")
        if not os.path.exists(file_path):
            return {
                "next_action": (
                    "Tell the user only this: You have not uploaded the CSV file for this incident which contains the cassette list. "
                    "Please upload the CSV file before I can proceed with the partial release of DMR.\n"
                    "Once the user confirms they have uploaded the file, proceed calling triage_dmr_partial_release tool again."
                )
            }
        
        df = pd.read_csv(file_path)
        # Assume the CSV has a column named 'mcass_id' or 'cassette_id' or it's the first column
        if 'mcass_id' in df.columns:
            cassette_ids = df['mcass_id'].astype(str).tolist()
        elif 'cassette_id' in df.columns:
            cassette_ids = df['cassette_id'].astype(str).tolist()
        else:
            cassette_ids = df.iloc[:, 0].astype(str).tolist()
        
        if not cassette_ids:
            return {"next_action": "Tell the user only this: The uploaded CSV file is empty. Please upload a valid CSV file with cassette IDs so that I can continue with partial release of DMR."}
        
        # Step 2: Confirm DMR / Hold status
        # 2A: Get DMR from hold_rels_detail for the cassettes from the CSV.
        if not cassette_ids:
            return {"next_action": f"Tell the user only this: No cassettes found in the uploaded CSV, so you are not eligible for partial release of DMR."}

        hold_details = await postgres_service.get_hold_rels_detail_for_cassettes(cassette_ids)
        if not hold_details:
            return {"next_action": f"Tell the user only this: No hold records was found in hold release detail table for the provided cassette. So you are not eligible for partial release of DMR."}

        dmr_nos = list(set([d.get('dmr_no') for d in hold_details if d.get('dmr_no')]))
        if not dmr_nos:
            return {"next_action": "Tell the user only this: No DMR found for this cassette, so you are not eligible for partial release of DMR."}
        
        dmr_no = dmr_nos[0]

        # Store critical data in session individually for execute step
        session = await get_session(store, thread_id)
        session["cassette_ids"] = cassette_ids
        session["dmr_no"] = dmr_no
        session["incident_id"] = resolved_incident_id
        await set_session(store, thread_id, session)

        # 2C: Get hold status + flags from p_hold_rels_log
        rels_log = await postgres_service.get_p_hold_rels_log(dmr_no)
        if not rels_log:

            return {
                "next_action": (
                    f"Tell the user only this: We were not able to do this, no entry was found in pe hold rels log table for DMR number {dmr_no}. "
                    "We have also sent an email to the SME, Lakshmi and Tanveer."
                )
            }

        hold_status = rels_log.get('hold_status', '')
        drb_flag = rels_log.get('drb_flag', '')
        yield_flag = rels_log.get('yield_flag', '')

        # Decision A: Is hold_status = TATEST / TTATEST / TTTATEST?
        if hold_status not in ('TATEST', 'TTATEST', 'TTTATEST'):
            return {
                "next_action": (
                    f"Tell the user only this: The incident is not eligible for DMR resolution because the hold status is {hold_status}. "
                    "We have also sent an email to the SME, Lakshmi and Tanveer."
                )
            }

        # Decision B: Are drb_flag = 'T' AND yield_flag = 'T'?
        if drb_flag == 'T' and yield_flag == 'T':
            # YES: Proceed to Step 3 (Confirmation)
            return {
                "next_action": (
                    f"Tell the user only this:\n"
                    f"- Incident {resolved_incident_id} is eligible for partial release.\n"
                    f"- DMR: {dmr_no}\n"
                    f"- Hold Status: {hold_status}\n"
                    f"- DRB Flag: {drb_flag}, Yield Flag: {yield_flag}\n"
                    f"- Do you want me to proceed with executing the partial release resolution?\n"
                    "Once the user confirms, proceed calling perform_dmr_partial_release_resolution tool."
                )
            }
        elif yield_flag == 'CO':
            # NO: check if yield_flag='CO'
            return {
                "next_action": (
                    f"Tell the user only this: This was not possible as the yield flag was CO for DMR {dmr_no}. "
                    "We will think about the partial DMR later. I have sent an email to Lakshmi, SME, for further review."
                )
            }
        else:
            # Stop: flags not ready
            return {
                "next_action": (
                    f"Tell the user only this: The flags are not ready (DRB: {drb_flag}, Yield: {yield_flag}) for DMR {dmr_no}. "
                    "We were not able to proceed. We have sent an email to Tanveer, SME."
                )
            }

    async def execute_dmr_partial_release(
        self,
        incident_id: str,
        thread_id: str,
        store: AsyncRedisStore,
        operator_confirmed: bool = False,
    ) -> dict:
        resolved_incident_id = (incident_id or "").strip()
        if not resolved_incident_id:
            return {"next_action": "Tell the user only this: Provide a partial DMR incident ID before execution."}

        if not operator_confirmed:
            return {"next_action": "Tell the user only this: Operator confirmation is required before execution."}

        # Retrieve data strictly from session
        session = await get_session(store, thread_id)
        
        if session.get("incident_id") == resolved_incident_id:
            cassette_ids = session.get("cassette_ids", [])
            dmr_no = session.get("dmr_no", "")
        else:
            cassette_ids = []
            dmr_no = ""

        # Report error if session data is missing - no fallbacks
        if not cassette_ids or not dmr_no:
            return {
                "next_action": (
                    "Tell the user there was some issue executing DMR partial release. "
                    "Please run the triage step again before executing the resolution."
                )
            }

        # Step 3: Execution
        # Execute step-by-step resolution script (Steps 3.1 to 3.7)
        executed_count = await postgres_service.execute_dmr_step_by_step(
            cassette_ids=cassette_ids,
            dmr_no=dmr_no
        )

        # Record action
        await postgres_service.record_dmr_action(
            incident_id=resolved_incident_id,
            action_name="DMR_PARTIAL_RELEASE",
            outcome="EXECUTED",
            operator_confirmed=True,
        )
        await postgres_service.update_dmr_incident(
            incident_id=resolved_incident_id,
            current_status="RESOLVED",
        )

        #fresh service 

        return {
            "next_action": (
                f"Tell the user only this: Partial release resolution for {resolved_incident_id} has been executed successfully.\n"
                f"- Cassettes processed: {executed_count}\n"
                f"- DMR: {dmr_no}\n"
                f"- Result: Hold record removed and partial release logged in p_rels_log_dtl."
            )
        }


dmr_partial_release_automation_service = DmrPartialReleaseAutomationService()
