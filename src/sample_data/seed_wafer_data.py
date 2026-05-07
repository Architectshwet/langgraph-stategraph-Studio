from __future__ import annotations

from datetime import datetime, timedelta, timezone


BASE_TS = datetime(2026, 4, 7, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: int) -> str:
    return (BASE_TS + timedelta(minutes=minutes)).isoformat()


def _build_soc_incidents() -> list[dict]:
    tools = ["TOOL-A12", "TOOL-B04", "TOOL-C19", "TOOL-D02", "TOOL-E17", "TOOL-F08"]
    lots = ["LOT-ALPHA-221", "LOT-BETA-118", "LOT-GAMMA-908", "LOT-DELTA-414", "LOT-EPS-771", "LOT-ZETA-530"]
    states = [
        ("RUNNING_WITH_ABORT_OPTION", True, 2, False, False, "OPERATOR_ABORT", "MEDIUM"),
        ("WAITING_CEID", False, 0, True, False, "FORCE_RUN_COMPLETE", "HIGH"),
        ("INVALID_TRANSITION", False, 0, True, False, "PURGE", "CRITICAL"),
        ("HOST_TOOL_DISCONNECT", False, 1, False, True, "CASSETTE_RESET", "HIGH"),
        ("PROCESS_PROGRAM_TIMEOUT", False, 0, True, True, "FORCE_RUN_COMPLETE", "MEDIUM"),
        ("TOOL_ALARM", False, 0, True, False, "PURGE", "HIGH"),
    ]

    incidents: list[dict] = []
    for idx in range(60):
        template = states[idx % len(states)]
        run_state, abort_available, wafers_in_process, loadlocks_empty, force_mda_enabled, recommended_action, priority = template
        special_lot = idx % 11 == 0 and recommended_action == "PURGE"
        if special_lot:
            recommended_action = "CASSETTE_RESET"
        incident_no = 7001 + idx
        lot_id = lots[idx % len(lots)]
        tool_id = tools[idx % len(tools)]
        cassette_id = f"CAS-{1001 + idx:04d}"
        created_at = _ts(10 + idx * 6)
        updated_at = _ts(12 + idx * 6)
        incidents.append(
            {
                "incident_id": f"SOC-INC-{incident_no}",
                "tool_id": tool_id,
                "lot_id": lot_id,
                "cassette_id": cassette_id,
                "run_state": run_state,
                "abort_available": abort_available,
                "wafers_in_process": wafers_in_process if idx < 6 else (idx % 3),
                "loadlocks_empty": loadlocks_empty if idx < 6 else (idx % 4 != 0),
                "force_mda_enabled": force_mda_enabled if idx < 6 else (idx % 5 != 0),
                "special_lot": special_lot or (idx % 13 == 0),
                "current_status": "OPEN",
                "priority": priority,
                "recommended_action": recommended_action,
                "created_at": created_at,
                "updated_at": updated_at,
                "notes": (
                    f"Seeded SOC incident {incident_no} for {tool_id} on {lot_id}. "
                    f"Observed run state {run_state}."
                ),
            }
        )
    return incidents


def _soc_logs_for(incident: dict, idx: int) -> list[dict]:
    event_map = {
        "RUNNING_WITH_ABORT_OPTION": ("CEID_MISSING", "Expected CEID sequence paused, abort path is available."),
        "WAITING_CEID": ("CEID_MISSING", "Tool stopped receiving collection events from the host."),
        "INVALID_TRANSITION": ("INVALID_TRANSITION", "SOC moved from RUNNING to COMPLETED without a valid close."),
        "HOST_TOOL_DISCONNECT": ("HOST_DISCONNECT", "Host and tool connection dropped during the run."),
        "PROCESS_PROGRAM_TIMEOUT": ("PROCESS_PROGRAM_TIMEOUT", "Process program selection timed out on the SOC host."),
        "TOOL_ALARM": ("F_ABRT", "Transaction aborted after a tool alarm interrupted the run."),
    }
    event_code, message = event_map.get(str(incident.get("run_state")), ("SOC_ALERT", "Operator review is required."))
    logged_at = datetime.fromisoformat(str(incident["created_at"]))
    return [
        {
            "domain": "soc",
            "source_system": "process.log",
            "severity": "WARN" if idx % 2 else "ERROR",
            "event_code": event_code,
            "message": message,
            "lot_id": incident["lot_id"],
            "tool_id": incident["tool_id"],
            "host_name": None,
            "incident_id": incident["incident_id"],
            "logged_at": (logged_at - timedelta(minutes=2)).isoformat(),
        },
        {
            "domain": "soc",
            "source_system": "rolling.log",
            "severity": "ERROR",
            "event_code": "RUN_DIAGNOSTIC",
            "message": (
                f"Run history captured for cassette {incident['cassette_id']} with "
                f"{incident['wafers_in_process']} wafers in process."
            ),
            "lot_id": incident["lot_id"],
            "tool_id": incident["tool_id"],
            "host_name": None,
            "incident_id": incident["incident_id"],
            "logged_at": (logged_at - timedelta(minutes=1)).isoformat(),
        },
    ]


def _build_image_incidents() -> list[dict]:
    tools = ["SEM_82", "FIB_14", "TEC_31", "AXI_07", "FXS_18", "MDD_04", "OUS_22", "WSA_09"]
    stages = ["WP1_VE", "WP2_POST", "WB1_PRE", "WB2_INSPECT", "SP1_METRO", "SP2_VERIFY"]
    steps = ["CAPTURE", "REVIEW", "UPLOAD", "RETRY", "VALIDATE", "ARCHIVE"]
    image_types = ["SEM", "FIB", "AXI", "TEC", "WSA", "MDD"]
    recommendations = [
        "VERIFY_IMAGE_VIEWER",
        "COLLECT_IMAGES",
        "PREPARE_LOTLIST",
        "RUN_PROCESS_LOTLIST_CSV",
        "ESCALATE_TO_TOOL_OWNER",
    ]

    incidents: list[dict] = []
    for idx in range(60):
        lot_id = f"LOT-OCAP-{500 + idx:03d}"
        tool_name = tools[idx % len(tools)]
        stage = stages[idx % len(stages)]
        step_name = steps[idx % len(steps)]
        image_type = image_types[idx % len(image_types)]
        image_viewer_available = idx % 6 == 0
        images_on_host = idx % 6 in {1, 3, 5}
        rolling_log_has_failure = idx % 6 in {1, 2, 4}
        working_directory_ready = idx % 5 in {1, 3}
        recommended_action = recommendations[idx % len(recommendations)]
        if image_viewer_available:
            recommended_action = "VERIFY_IMAGE_VIEWER"
        elif images_on_host and working_directory_ready:
            recommended_action = "RUN_PROCESS_LOTLIST_CSV"
        elif images_on_host:
            recommended_action = "COLLECT_IMAGES"
        elif rolling_log_has_failure:
            recommended_action = "PREPARE_LOTLIST"

        incident_no = 8001 + idx
        incident = {
            "incident_id": f"OCAP-INC-{incident_no}",
            "tool_name": tool_name,
            "host_name": f"soc-host-{11 + (idx % 9):02d}",
            "lot_id": lot_id,
            "stage": stage,
            "step_name": step_name,
            "image_type": image_type,
            "image_viewer_available": image_viewer_available,
            "images_on_host": images_on_host,
            "rolling_log_has_failure": rolling_log_has_failure,
            "working_directory_ready": working_directory_ready,
            "current_status": "OPEN",
            "priority": "CRITICAL" if idx % 7 == 0 else ("HIGH" if idx % 3 == 0 else "MEDIUM"),
            "recommended_action": recommended_action,
            "alert_source": "ImageManager",
            "failure_signature": f"Image not Uploaded for {image_type} on {tool_name}",
            "detected_at": _ts(20 + idx * 4),
            "updated_at": _ts(22 + idx * 4),
            "notes": (
                f"Alert opened for {tool_name} at stage {stage}, step {step_name}. "
                f"Viewer={image_viewer_available}, host_copy={images_on_host}."
            ),
        }
        incidents.append(incident)
    return incidents


def _image_logs_for(incident: dict, idx: int) -> list[dict]:
    detected_at = datetime.fromisoformat(str(incident["detected_at"]))
    logs = [
        {
            "domain": "ocap",
            "source_system": "ImageManager",
            "severity": "ERROR" if idx % 2 == 0 else "WARN",
            "event_code": "IMAGE_NOT_UPLOADED",
            "message": incident["failure_signature"],
            "lot_id": incident["lot_id"],
            "tool_id": incident["tool_name"],
            "host_name": incident["host_name"],
            "incident_id": incident["incident_id"],
            "logged_at": (detected_at - timedelta(minutes=3)).isoformat(),
        },
        {
            "domain": "ocap",
            "source_system": "Rolling.log",
            "severity": "ERROR",
            "event_code": "IMAGE_MANAGER_FAILURE",
            "message": (
                "Rolling.log captured the missing image path and run number required for recovery."
                if incident["rolling_log_has_failure"]
                else "Rolling.log shows the upload interruption was isolated to the image manager queue."
            ),
            "lot_id": incident["lot_id"],
            "tool_id": incident["tool_name"],
            "host_name": incident["host_name"],
            "incident_id": incident["incident_id"],
            "logged_at": (detected_at - timedelta(minutes=1)).isoformat(),
        },
    ]
    return logs


def _build_soc_actions(incidents: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for idx, incident in enumerate(incidents[:24]):
        actions.append(
            {
                "audit_id": f"SOCA-{8101 + idx}",
                "incident_id": incident["incident_id"],
                "action_name": incident["recommended_action"],
                "outcome": "EXECUTED" if idx % 4 else "DEFERRED_TO_OPERATOR",
                "approval_ticket": f"SNOW-{54000 + idx}" if incident["recommended_action"] == "PURGE" else "",
                "operator_confirmed": idx % 4 != 0,
                "executed_by": "l2.nightshift" if idx % 2 else "wafer-auto-bot",
                "notes": f"Historical reference for {incident['tool_id']} with safe operator handling.",
                "executed_at": _ts(300 + idx * 12),
            }
        )
    return actions


def _build_partial_release_incidents() -> list[dict]:
    tools = ["TOOL-A12", "TOOL-B04", "TOOL-C19", "TOOL-D02"]
    lots = ["LOT-ALPHA-221", "LOT-BETA-118", "LOT-GAMMA-908", "LOT-DELTA-414"]
    
    incidents: list[dict] = []
    for idx in range(20):
        incident_no = 9001 + idx
        lot_id = lots[idx % len(lots)]
        tool_id = tools[idx % len(tools)]
        created_at = _ts(15 + idx * 8)
        updated_at = _ts(17 + idx * 8)
        incidents.append(
            {
                "incident_id": f"PRR-INC-{incident_no}",
                "tool_id": tool_id,
                "lot_id": lot_id,
                "dmr_status": "APPROVED",
                "fg_status": "FAILED",
                "hold_record_exists": True,
                "current_status": "OPEN",
                "priority": "HIGH" if idx % 3 == 0 else "MEDIUM",
                "recommended_action": "PARTIAL_RELEASE",
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return incidents


def _build_partial_release_actions(incidents: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for idx, incident in enumerate(incidents[:10]):
        actions.append(
            {
                "action_id": f"PRRA-{7101 + idx}",
                "incident_id": incident["incident_id"],
                "action_name": "PARTIAL_RELEASE",
                "outcome": "EXECUTED",
                "operator_confirmed": True,
                "executed_by": "wafer-release-bot",
                "notes": f"Partial release executed for {incident['lot_id']}.",
                "executed_at": _ts(450 + idx * 10),
            }
        )
    return actions


def _build_image_actions(incidents: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for idx, incident in enumerate(incidents[:24]):
        action_name = incident["recommended_action"]
        if action_name == "VERIFY_IMAGE_VIEWER":
            outcome = "VERIFIED"
        elif action_name == "ESCALATE_TO_TOOL_OWNER":
            outcome = "ESCALATED"
        else:
            outcome = "EXECUTED"
        actions.append(
            {
                "action_id": f"OCAPA-{9101 + idx}",
                "incident_id": incident["incident_id"],
                "action_name": action_name,
                "outcome": outcome,
                "approval_ticket": "",
                "operator_confirmed": True,
                "executed_by": "wafer-image-bot",
                "notes": f"OCAP recovery step captured for {incident['tool_name']} on {incident['lot_id']}.",
                "executed_at": _ts(420 + idx * 9),
            }
        )
    return actions


def _build_system_logs(soc_incidents: list[dict], image_incidents: list[dict]) -> list[dict]:
    logs: list[dict] = []
    for idx, incident in enumerate(soc_incidents):
        logs.extend(_soc_logs_for(incident, idx))
    for idx, incident in enumerate(image_incidents):
        logs.extend(_image_logs_for(incident, idx))
    return logs


def _build_ssl_incidents() -> list[dict]:
    servers = ["WOODSFSWD", "WOODSFSW1", "WOODSFSW2", "SFSWEB"]
    common_names = ["woodsfs.woo.sing.seagate.com", "sfsweb.woo.sing.seagate.com"]
    
    incidents: list[dict] = []
    for idx in range(10):
        incident_no = 10001 + idx
        server_name = servers[idx % len(servers)]
        common_name = common_names[idx % len(common_names)]
        created_at = _ts(20 + idx * 10)
        updated_at = _ts(22 + idx * 10)
        incidents.append(
            {
                "incident_id": f"SSL-INC-{incident_no}",
                "server_name": server_name,
                "common_name": common_name,
                "organization": "SEAGATE TECHNOLOGY LLC",
                "org_unit": "IT",
                "locality": "Singapore",
                "state": "Singapore",
                "country": "SG",
                "san_hostnames": "sfsweb.woo.sing.seagate.com,woodsfswd.woo.sing.seagate.com",
                "current_status": "PENDING_CSR",
                "priority": "Medium",
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return incidents


def _build_ssl_actions(incidents: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for idx, incident in enumerate(incidents[:5]):
        actions.append(
            {
                "action_id": f"SSLA-{idx+1:04d}",
                "incident_id": incident["incident_id"],
                "action_name": "CREATE_CSR_WINDOWS" if idx % 2 == 0 else "CREATE_CSR_LINUX",
                "outcome": "EXECUTED",
                "operator_confirmed": True,
                "executed_by": "wafer-ssl-bot",
                "executed_at": _ts(500 + idx * 15),
            }
        )
    return actions


def _build_mdw_src_dest() -> list[dict]:
    return [
        {
            "mcass_id": "BA5180FBYE156I3QK",
            "dst_cid": "DST-9988",
            "dst_loc": "PACK-A1",
            "lot_no": "LOT-ALPHA-221"
        },
        {
            "mcass_id": "CAS-1002",
            "dst_cid": "DST-9989",
            "dst_loc": "PACK-A2",
            "lot_no": "LOT-BETA-118"
        }
    ]


def _build_hold_rels_details() -> list[dict]:
    return [
        {
            "mcass_id": "BA5180FBYE156I3QK",
            "dmr_no": "W1465325645",
            "hold_code": "HC-100",
            "hold_reason": "Partial Release Pending",
            "hold_user": "operator_42",
            "hold_at": _ts(100),
            "dmr_approved": True,
            "fg_failed": True
        },
        {
            "mcass_id": "CAS-1002",
            "dmr_no": "W1465325646",
            "hold_code": "HC-101",
            "hold_reason": "Partial Release Pending",
            "hold_user": "operator_43",
            "hold_at": _ts(200),
            "dmr_approved": True,
            "fg_failed": True
        }
    ]


def _build_p_hold_rels_log() -> list[dict]:
    return [
        {
            "dmr_no": "W1465325645",
            "hold_status": "TATEST",
            "drb_flag": "T",
            "yield_flag": "T"
        },
        {
            "dmr_no": "W1465325646",
            "hold_status": "TTATEST",
            "drb_flag": "T",
            "yield_flag": "T"
        }
    ]


def _build_p_rels_log_dtl() -> list[dict]:
    # Need one template row for Step 3.5
    return [
        {
            "trans_id": "TID-PREV-01",
            "fg_id": "CAS-PREV",
            "released": "1",
            "dmr_no": "W-PREV",
            "is_ea": "T",
            "rehold": "f",
            "date_tm": _ts(-1000), # Old enough but still useful
            "date_tm_tx": "2026-04-01 10:00:00",
            "affected_disc_qty": 25
        }
    ]


def _build_tm1() -> list[dict]:
    return []


def _build_tm3() -> list[dict]:
    return []


def _build_tm10() -> list[dict]:
    return []


def get_seed_payload() -> dict:
    soc_incidents = _build_soc_incidents()
    image_incidents = _build_image_incidents()
    partial_incidents = _build_partial_release_incidents()
    ssl_incidents = _build_ssl_incidents()
    return {
        "soc_incidents": soc_incidents,
        "soc_actions": _build_soc_actions(soc_incidents),
        "image_incidents": image_incidents,
        "image_actions": _build_image_actions(image_incidents),
        "partial_incidents": partial_incidents,
        "partial_actions": _build_partial_release_actions(partial_incidents),
        "ssl_incidents": ssl_incidents,
        "ssl_actions": _build_ssl_actions(ssl_incidents),
        "mdw_src_dest": _build_mdw_src_dest(),
        "hold_rels_details": _build_hold_rels_details(),
        "p_hold_rels_log": _build_p_hold_rels_log(),
        "p_rels_log_dtl": _build_p_rels_log_dtl(),
        "tm1": _build_tm1(),
        "tm3": _build_tm3(),
        "tm10": _build_tm10(),
        "system_logs": _build_system_logs(soc_incidents, image_incidents),
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }
