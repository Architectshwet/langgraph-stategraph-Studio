import uuid
from datetime import date, datetime, timezone
from typing import Any

from src.db import postgres_repository
from src.db.db_singleton import DatabaseSingleton
from src.sample_data.seed_wafer_data import get_seed_payload
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _record_to_dict(record) -> dict:
    if not record:
        return {}
    return {key: _normalize_value(value) for key, value in dict(record).items()}


def _records_to_dicts(rows) -> list[dict]:
    return [{key: _normalize_value(value) for key, value in dict(row).items()} for row in rows]


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _extract_affected_rows(result: str) -> int:
    parts = str(result).split()
    if not parts:
        return 0
    last = parts[-1]
    return int(last) if last.isdigit() else 0


class PostgresService:
    """Wafer domain schema and query service."""

    def __init__(self):
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        await DatabaseSingleton.get_pool()
        self._initialized = True
        logger.info("Wafer domain schema initialized")

    async def seed_demo_data(self, reset: bool = False) -> dict:
        if not reset:
            return {"success": True, "seeded": False, "skipped": True, "reset": False}
        await postgres_repository.initialize_wafer_schema()

        payload = get_seed_payload()
        pool = await DatabaseSingleton.get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO soc_manual_intervention_incidents (
                        incident_id, tool_id, lot_id, cassette_id, run_state, abort_available,
                        wafers_in_process, loadlocks_empty, force_mda_enabled, special_lot,
                        current_status, priority, recommended_action, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (incident_id) DO NOTHING
                    """,
                    [
                        (
                            item["incident_id"],
                            item["tool_id"],
                            item["lot_id"],
                            item["cassette_id"],
                            item["run_state"],
                            bool(item["abort_available"]),
                            int(item["wafers_in_process"]),
                            bool(item["loadlocks_empty"]),
                            bool(item["force_mda_enabled"]),
                            bool(item["special_lot"]),
                            item["current_status"],
                            item["priority"],
                            item["recommended_action"],
                            _as_datetime(item["created_at"]),
                            _as_datetime(item["updated_at"]),
                        )
                        for item in payload["soc_incidents"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO soc_manual_intervention_actions (
                        audit_id, incident_id, action_name, outcome, approval_ticket,
                        operator_confirmed, executed_by, notes, executed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (audit_id) DO NOTHING
                    """,
                    [
                        (
                            item["audit_id"],
                            item["incident_id"],
                            item["action_name"],
                            item["outcome"],
                            item["approval_ticket"],
                            bool(item["operator_confirmed"]),
                            item["executed_by"],
                            item["notes"],
                            _as_datetime(item["executed_at"]),
                        )
                        for item in payload["soc_actions"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO image_upload_failure_incidents (
                        incident_id, tool_name, host_name, lot_id, stage, step_name, image_type,
                        image_viewer_available, images_on_host, rolling_log_has_failure,
                        working_directory_ready, current_status, priority, recommended_action,
                        alert_source, failure_signature, detected_at, updated_at, notes
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                    ON CONFLICT (incident_id) DO NOTHING
                    """,
                    [
                        (
                            item["incident_id"],
                            item["tool_name"],
                            item["host_name"],
                            item["lot_id"],
                            item["stage"],
                            item["step_name"],
                            item["image_type"],
                            bool(item["image_viewer_available"]),
                            bool(item["images_on_host"]),
                            bool(item["rolling_log_has_failure"]),
                            bool(item["working_directory_ready"]),
                            item["current_status"],
                            item["priority"],
                            item["recommended_action"],
                            item["alert_source"],
                            item["failure_signature"],
                            _as_datetime(item["detected_at"]),
                            _as_datetime(item["updated_at"]),
                            item["notes"],
                        )
                        for item in payload["image_incidents"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO image_upload_failure_actions (
                        action_id, incident_id, action_name, outcome, approval_ticket,
                        operator_confirmed, executed_by, notes, executed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (action_id) DO NOTHING
                    """,
                    [
                        (
                            item["action_id"],
                            item["incident_id"],
                            item["action_name"],
                            item["outcome"],
                            item["approval_ticket"],
                            bool(item["operator_confirmed"]),
                            item["executed_by"],
                            item["notes"],
                            _as_datetime(item["executed_at"]),
                        )
                        for item in payload["image_actions"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO dmr_incidents (
                        incident_id, tool_id, lot_id, current_status,
                        created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (incident_id) DO NOTHING
                    """,
                    [
                        (
                            item["incident_id"],
                            item["tool_id"],
                            item["lot_id"],
                            item["current_status"],
                            _as_datetime(item["created_at"]),
                            _as_datetime(item["updated_at"]),
                        )
                        for item in payload["partial_incidents"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO dmr_actions (
                        action_id, incident_id, action_name, outcome,
                        operator_confirmed, executed_by, executed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (action_id) DO NOTHING
                    """,
                    [
                        (
                            item["action_id"],
                            item["incident_id"],
                            item["action_name"],
                            item["outcome"],
                            bool(item["operator_confirmed"]),
                            item["executed_by"],
                            _as_datetime(item["executed_at"]),
                        )
                        for item in payload["partial_actions"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO ssl_incidents (
                        incident_id, server_name, common_name, organization, org_unit,
                        locality, state, country, san_hostnames, current_status, priority,
                        created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (incident_id) DO NOTHING
                    """,
                    [
                        (
                            item["incident_id"],
                            item["server_name"],
                            item["common_name"],
                            item["organization"],
                            item["org_unit"],
                            item["locality"],
                            item["state"],
                            item["country"],
                            item["san_hostnames"],
                            item["current_status"],
                            item["priority"],
                            _as_datetime(item["created_at"]),
                            _as_datetime(item["updated_at"]),
                        )
                        for item in payload["ssl_incidents"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO ssl_actions (
                        action_id, incident_id, action_name, outcome,
                        operator_confirmed, executed_by, executed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (action_id) DO NOTHING
                    """,
                    [
                        (
                            item["action_id"],
                            item["incident_id"],
                            item["action_name"],
                            item["outcome"],
                            bool(item["operator_confirmed"]),
                            item["executed_by"],
                            _as_datetime(item["executed_at"]),
                        )
                        for item in payload["ssl_actions"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO mdw_src_dest (mcass_id, dst_cid, dst_loc, lot_no)
                    VALUES ($1, $2, $3, $4)
                    """,
                    [
                        (
                            item["mcass_id"],
                            item["dst_cid"],
                            item["dst_loc"],
                            item["lot_no"],
                        )
                        for item in payload["mdw_src_dest"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO hold_rels_detail (
                        mcass_id, dmr_no, hold_code, hold_reason, hold_user, hold_at,
                        dmr_approved, fg_failed
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (mcass_id) DO NOTHING
                    """,
                    [
                        (
                            item["mcass_id"],
                            item["dmr_no"],
                            item["hold_code"],
                            item["hold_reason"],
                            item["hold_user"],
                            _as_datetime(item["hold_at"]),
                            bool(item["dmr_approved"]),
                            bool(item["fg_failed"]),
                        )
                        for item in payload["hold_rels_details"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO p_hold_rels_log (dmr_no, hold_status, drb_flag, yield_flag)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (dmr_no) DO NOTHING
                    """,
                    [
                        (
                            item["dmr_no"],
                            item["hold_status"],
                            item["drb_flag"],
                            item["yield_flag"],
                        )
                        for item in payload["p_hold_rels_log"]
                    ],
                )

                await conn.executemany(
                    """
                    INSERT INTO p_rels_log_dtl (
                        trans_id, fg_id, released, dmr_no, is_ea, rehold,
                        date_tm, date_tm_tx, affected_disc_qty
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (trans_id) DO NOTHING
                    """,
                    [
                        (
                            item["trans_id"],
                            item["fg_id"],
                            item["released"],
                            item["dmr_no"],
                            item.get("is_ea"),
                            item["rehold"],
                            _as_datetime(item["date_tm"]),
                            item.get("date_tm_tx"),
                            item.get("affected_disc_qty"),
                        )
                        for item in payload["p_rels_log_dtl"]
                    ],
                )

                # Note: tm1, tm3, tm10 are now temporary tables created during execution, so no seeding required here.

                await conn.executemany(
                    """
                    INSERT INTO system_logs (
                        domain, source_system, severity, event_code, message,
                        lot_id, tool_id, host_name, incident_id, logged_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    [
                        (
                            item["domain"],
                            item["source_system"],
                            item["severity"],
                            item["event_code"],
                            item["message"],
                            item["lot_id"],
                            item["tool_id"],
                            item["host_name"],
                            item["incident_id"],
                            _as_datetime(item["logged_at"]),
                        )
                        for item in payload["system_logs"]
                    ],
                )

        return {
            "success": True,
            "seeded": True,
            "skipped": False,
            "seeded_at": payload["seeded_at"],
            "reset": reset,
            "counts": await self.get_data_summary(),
        }

    async def get_data_summary(self) -> dict:
        tables = [
            "soc_manual_intervention_incidents",
            "soc_manual_intervention_actions",
            "image_upload_failure_incidents",
            "image_upload_failure_actions",
            "dmr_incidents",
            "dmr_actions",
            "mdw_src_dest",
            "hold_rels_detail",
            "p_hold_rels_log",
            "p_rels_log_dtl",
            "ssl_incidents",
            "ssl_actions",
            "system_logs",
        ]
        summary: dict[str, int] = {}
        for table in tables:
            row = await postgres_repository.fetchrow(f"SELECT COUNT(*) AS total FROM {table}")
            summary[table] = int(row["total"]) if row else 0
        return summary

    async def get_soc_incidents(self, lot_id: str = "") -> list[dict]:
        if lot_id:
            rows = await postgres_repository.fetch(
                """
                SELECT * FROM soc_manual_intervention_incidents
                WHERE lot_id = $1
                ORDER BY created_at DESC
                """,
                lot_id,
            )
        else:
            rows = await postgres_repository.fetch("SELECT * FROM soc_manual_intervention_incidents ORDER BY created_at DESC")
        return _records_to_dicts(rows)

    async def get_soc_incident(self, incident_id: str) -> dict:
        row = await postgres_repository.fetchrow(
            "SELECT * FROM soc_manual_intervention_incidents WHERE incident_id = $1",
            incident_id,
        )
        return _record_to_dict(row)

    async def get_soc_logs(self, incident_id: str, limit: int = 20) -> list[dict]:
        rows = await postgres_repository.fetch(
            """
            SELECT * FROM system_logs
            WHERE domain = 'soc' AND incident_id = $1
            ORDER BY logged_at DESC
            LIMIT $2
            """,
            incident_id,
            limit,
        )
        return _records_to_dicts(rows)

    async def record_soc_action(
        self,
        incident_id: str,
        action_name: str,
        outcome: str,
        approval_ticket: str = "",
        operator_confirmed: bool = False,
        notes: str = "",
        executed_by: str = "WaferAutomationAgent",
    ) -> dict:
        audit_id = f"SOCA-{uuid.uuid4().hex[:8].upper()}"
        row = await postgres_repository.fetchrow(
            """
            INSERT INTO soc_manual_intervention_actions (
                audit_id, incident_id, action_name, outcome, approval_ticket,
                operator_confirmed, executed_by, notes, executed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            RETURNING *
            """,
            audit_id,
            incident_id,
            action_name,
            outcome,
            approval_ticket,
            operator_confirmed,
            executed_by,
            notes,
        )
        await postgres_repository.execute(
            """
            INSERT INTO system_logs (
                domain, source_system, severity, event_code, message,
                lot_id, tool_id, host_name, incident_id, logged_at
            )
            VALUES ('soc', 'wafer-agent', 'INFO', 'SOC_ACTION_EXECUTED', $1, NULL, NULL, NULL, $2, NOW())
            """,
            f"{action_name} -> {outcome}",
            incident_id,
        )
        return _record_to_dict(row)

    async def update_soc_incident(self, incident_id: str, current_status: str, recommended_action: str) -> dict:
        row = await postgres_repository.fetchrow(
            """
            UPDATE soc_manual_intervention_incidents
            SET current_status = $2,
                recommended_action = $3,
                updated_at = NOW()
            WHERE incident_id = $1
            RETURNING *
            """,
            incident_id,
            current_status,
            recommended_action,
        )
        return _record_to_dict(row)

    async def get_image_upload_failure_incidents(self, lot_id: str = "") -> list[dict]:
        if lot_id:
            rows = await postgres_repository.fetch(
                """
                SELECT * FROM image_upload_failure_incidents
                WHERE lot_id = $1
                ORDER BY detected_at DESC
                """,
                lot_id,
            )
        else:
            rows = await postgres_repository.fetch("SELECT * FROM image_upload_failure_incidents ORDER BY detected_at DESC")
        return _records_to_dicts(rows)

    async def get_image_upload_failure_incident(self, incident_id: str) -> dict:
        row = await postgres_repository.fetchrow(
            "SELECT * FROM image_upload_failure_incidents WHERE incident_id = $1",
            incident_id,
        )
        return _record_to_dict(row)

    async def get_image_upload_failure_logs(self, incident_id: str, limit: int = 20) -> list[dict]:
        rows = await postgres_repository.fetch(
            """
            SELECT * FROM system_logs
            WHERE domain = 'image' AND incident_id = $1
            ORDER BY logged_at DESC
            LIMIT $2
            """,
            incident_id,
            limit,
        )
        return _records_to_dicts(rows)

    async def record_image_upload_failure_action(
        self,
        incident_id: str,
        action_name: str,
        outcome: str,
        approval_ticket: str = "",
        operator_confirmed: bool = False,
        notes: str = "",
        executed_by: str = "WaferAutomationAgent",
    ) -> dict:
        action_id = f"IMGA-{uuid.uuid4().hex[:8].upper()}"
        row = await postgres_repository.fetchrow(
            """
            INSERT INTO image_upload_failure_actions (
                action_id, incident_id, action_name, outcome, approval_ticket,
                operator_confirmed, executed_by, notes, executed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            RETURNING *
            """,
            action_id,
            incident_id,
            action_name,
            outcome,
            approval_ticket,
            operator_confirmed,
            executed_by,
            notes,
        )
        await postgres_repository.execute(
            """
            INSERT INTO system_logs (
                domain, source_system, severity, event_code, message,
                lot_id, tool_id, host_name, incident_id, logged_at
            )
            VALUES ('image', 'wafer-agent', 'INFO', 'IMAGE_ACTION_EXECUTED', $1, NULL, NULL, NULL, $2, NOW())
            """,
            f"{action_name} -> {outcome}",
            incident_id,
        )
        return _record_to_dict(row)

    async def update_image_upload_failure_incident(
        self, incident_id: str, current_status: str, recommended_action: str
    ) -> dict:
        row = await postgres_repository.fetchrow(
            """
            UPDATE image_upload_failure_incidents
            SET current_status = $2,
                recommended_action = $3,
                updated_at = NOW()
            WHERE incident_id = $1
            RETURNING *
            """,
            incident_id,
            current_status,
            recommended_action,
        )
        return _record_to_dict(row)

    async def get_dmr_incident(self, incident_id: str) -> dict:
        row = await postgres_repository.fetchrow(
            "SELECT * FROM dmr_incidents WHERE incident_id = $1",
            incident_id,
        )
        return _record_to_dict(row)

    async def record_dmr_action(
        self,
        incident_id: str,
        action_name: str,
        outcome: str,
        operator_confirmed: bool = False,
    ) -> dict:
        row = await postgres_repository.fetchrow(
            """
            INSERT INTO dmr_actions (
                action_id, incident_id, action_name, outcome,
                operator_confirmed, executed_by, executed_at
            )
            VALUES ($1, $2, $3, $4, $5, 'wafer-agent', NOW())
            RETURNING *
            """,
            f"DRA-{uuid.uuid4().hex[:8].upper()}",
            incident_id,
            action_name,
            outcome,
            operator_confirmed,
        )
        return _record_to_dict(row)

    async def update_dmr_incident(self, incident_id: str, current_status: str) -> dict:
        row = await postgres_repository.fetchrow(
            """
            UPDATE dmr_incidents
            SET current_status = $2,
                updated_at = NOW()
            WHERE incident_id = $1
            RETURNING *
            """,
            incident_id,
            current_status,
        )
        return _record_to_dict(row)

    async def get_ssl_incident(self, incident_id: str) -> dict:
        row = await postgres_repository.fetchrow(
            "SELECT * FROM ssl_incidents WHERE incident_id = $1",
            incident_id,
        )
        return _record_to_dict(row)

    async def get_ssl_incidents(self) -> list[dict]:
        rows = await postgres_repository.fetch("SELECT * FROM ssl_incidents ORDER BY created_at DESC")
        return _records_to_dicts(rows)

    async def create_ssl_incident(self, incident_data: dict) -> dict:
        row = await postgres_repository.fetchrow(
            """
            INSERT INTO ssl_incidents (
                incident_id, server_name, common_name, organization, org_unit,
                locality, state, country, san_hostnames, current_status, priority,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW(), NOW())
            RETURNING *
            """,
            incident_data["incident_id"],
            incident_data["server_name"],
            incident_data["common_name"],
            incident_data.get("organization", "SEAGATE TECHNOLOGY LLC"),
            incident_data.get("org_unit", "IT"),
            incident_data.get("locality", "Oklahoma City"),
            incident_data.get("state", "Oklahoma"),
            incident_data.get("country", "US"),
            incident_data.get("san_hostnames"),
            incident_data["current_status"],
            incident_data.get("priority", "Medium"),
        )
        return _record_to_dict(row)

    async def record_ssl_action(
        self,
        incident_id: str,
        action_name: str,
        outcome: str,
        operator_confirmed: bool = False,
    ) -> dict:
        row = await postgres_repository.fetchrow(
            """
            INSERT INTO ssl_actions (
                action_id, incident_id, action_name, outcome,
                operator_confirmed, executed_by, executed_at
            )
            VALUES ($1, $2, $3, $4, $5, 'wafer-agent', NOW())
            RETURNING *
            """,
            f"SLA-{uuid.uuid4().hex[:8].upper()}",
            incident_id,
            action_name,
            outcome,
            operator_confirmed,
        )
        return _record_to_dict(row)

    async def update_ssl_incident(self, incident_id: str, updates: dict) -> dict:
        set_parts = []
        params = [incident_id]
        for i, (key, value) in enumerate(updates.items()):
            set_parts.append(f"{key} = ${i+2}")
            params.append(value)
        
        set_clause = ", ".join(set_parts)
        query = f"""
            UPDATE ssl_incidents
            SET {set_clause},
                updated_at = NOW()
            WHERE incident_id = $1
            RETURNING *
        """
        row = await postgres_repository.fetchrow(query, *params)
        return _record_to_dict(row)

    async def get_hold_rels_detail_for_cassettes(self, cassette_ids: list[str]) -> list[dict]:
        rows = await postgres_repository.fetch(
            "SELECT * FROM hold_rels_detail WHERE mcass_id = ANY($1)",
            cassette_ids,
        )
        return _records_to_dicts(rows)

    async def get_p_hold_rels_log(self, dmr_no: str) -> dict:
        row = await postgres_repository.fetchrow(
            "SELECT * FROM p_hold_rels_log WHERE dmr_no = $1",
            dmr_no,
        )
        return _record_to_dict(row)

    async def check_existing_partial_release(self, cassette_ids: list[str]) -> list[dict]:
        rows = await postgres_repository.fetch(
            "SELECT * FROM p_rels_log_dtl WHERE fg_id = ANY($1)",
            cassette_ids,
        )
        return _records_to_dicts(rows)

    async def get_mdw_src_dest_for_cassettes(self) -> list[dict]:
        rows = await postgres_repository.fetch(
            "SELECT * FROM mdw_src_dest WHERE mcass_id IN ('BA5180FBYE156I3QK')",
        )
        return _records_to_dicts(rows)

    async def get_mdw_src_dest_for_lot(self, lot_id: str) -> list[dict]:
        rows = await postgres_repository.fetch(
            "SELECT * FROM mdw_src_dest WHERE lot_no = $1",
            lot_id,
        )
        return _records_to_dicts(rows)

    async def get_p_rels_log_dtl_template(self) -> dict:
        # Step 3.5: Create tem10 (template row for insert)
        row = await postgres_repository.fetchrow(
            """
            SELECT * FROM p_rels_log_dtl
            WHERE date_tm > NOW() - INTERVAL '10 days'
              AND dmr_no <> ' '
              AND released = '1'
              AND rehold = 'f'
            LIMIT 1
            """
        )
        return _record_to_dict(row)

    async def execute_dmr_step_by_step(
        self,
        cassette_ids: list[str],
        dmr_no: str,
    ) -> int:
        pool = await DatabaseSingleton.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 3.1 Create tm1 (TEMP TABLE)
                # Using hardcoded value from documentation as requested: 'BA5180FBYE156I3QK'
                await conn.execute(
                    """
                    CREATE TEMP TABLE tm1 AS
                    SELECT DISTINCT mcass_id, dst_cid, dst_loc, lot_no
                    FROM mdw_src_dest
                    WHERE mcass_id IN ('BA5180FBYE156I3QK')
                    """
                )
                
                # 3.4 Create tm3 (TEMP TABLE)
                await conn.execute(
                    """
                    CREATE TEMP TABLE tm3 AS
                    SELECT DISTINCT mcass_id, dmr_no
                    FROM hold_rels_detail
                    WHERE mcass_id IN (SELECT mcass_id FROM tm1)
                    """
                )

                # 3.5 Create tem10 (TEMP TABLE)
                await conn.execute(
                    """
                    CREATE TEMP TABLE tem10 AS
                    SELECT * FROM p_rels_log_dtl
                    WHERE date_tm > NOW() - INTERVAL '10 days'
                      AND dmr_no <> ' '
                      AND released = '1'
                      AND rehold = 'f'
                    LIMIT 1
                    """
                )

                # 3.6 Insert into p_rels_log_dtl (Partial Release)
                result = await conn.execute(
                    """
                    INSERT INTO p_rels_log_dtl (trans_id, fg_id, released, dmr_no, is_ea, rehold, date_tm, date_tm_tx, affected_disc_qty)
                    SELECT 
                        'TID-' || UPPER(SUBSTR(MD5(RANDOM()::TEXT), 1, 8)), 
                        b.mcass_id, 
                        '1', 
                        $1, 
                        a.is_ea, 
                        'f', 
                        NOW(), 
                        TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS'), 
                        a.affected_disc_qty
                    FROM tem10 a, tm1 b
                    """,
                    dmr_no
                )
                count = _extract_affected_rows(result)
                
                # 3.7 Delete from hold_rels_detail
                await conn.execute(
                    """
                    DELETE FROM hold_rels_detail
                    WHERE mcass_id IN (SELECT mcass_id FROM tm1)
                      AND dmr_no = $1
                    """,
                    dmr_no
                )

                # Cleanup (Drop TEMP tables)
                await conn.execute("DROP TABLE IF EXISTS tm1")
                await conn.execute("DROP TABLE IF EXISTS tm3")
                await conn.execute("DROP TABLE IF EXISTS tem10")

                return count


postgres_service = PostgresService()
