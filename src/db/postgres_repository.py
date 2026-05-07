import json
import logging
from typing import Any

from src.db.db_singleton import DatabaseSingleton

logger = logging.getLogger(__name__)

_CONVERSATION_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_history (
    id SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL DEFAULT 'WaferAgent',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_history_conversation_id
ON conversation_history(conversation_id);

CREATE INDEX IF NOT EXISTS idx_conversation_history_created_at
ON conversation_history(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_history_agent
ON conversation_history(agent_name);

CREATE INDEX IF NOT EXISTS idx_conversation_history_role
ON conversation_history(role);
"""

_SESSION_STORE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_store (
    id SERIAL PRIMARY KEY,
    thread_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(thread_id, key)
);

CREATE INDEX IF NOT EXISTS idx_session_store_thread_id ON session_store(thread_id);
CREATE INDEX IF NOT EXISTS idx_session_store_created_at ON session_store(created_at DESC);
"""

_LLM_USAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_usage_log (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL,
    parent_run_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_session_id
ON llm_usage_log(session_id);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_thread_id
ON llm_usage_log(thread_id);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_created_at
ON llm_usage_log(created_at DESC);
"""

_WAFER_SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS soc_manual_intervention_incidents (
    incident_id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    cassette_id TEXT NOT NULL,
    run_state TEXT NOT NULL,
    abort_available BOOLEAN NOT NULL DEFAULT FALSE,
    wafers_in_process INTEGER NOT NULL DEFAULT 0,
    loadlocks_empty BOOLEAN NOT NULL DEFAULT FALSE,
    force_mda_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    special_lot BOOLEAN NOT NULL DEFAULT FALSE,
    current_status TEXT NOT NULL,
    priority TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS soc_manual_intervention_actions (
    audit_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES soc_manual_intervention_incidents(incident_id) ON DELETE CASCADE,
    action_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    approval_ticket TEXT NOT NULL DEFAULT '',
    operator_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    executed_by TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    executed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS image_upload_failure_incidents (
    incident_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    host_name TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    step_name TEXT NOT NULL,
    image_type TEXT NOT NULL,
    image_viewer_available BOOLEAN NOT NULL DEFAULT FALSE,
    images_on_host BOOLEAN NOT NULL DEFAULT FALSE,
    rolling_log_has_failure BOOLEAN NOT NULL DEFAULT FALSE,
    working_directory_ready BOOLEAN NOT NULL DEFAULT FALSE,
    current_status TEXT NOT NULL,
    priority TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    alert_source TEXT NOT NULL,
    failure_signature TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS image_upload_failure_actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES image_upload_failure_incidents(incident_id) ON DELETE CASCADE,
    action_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    approval_ticket TEXT NOT NULL DEFAULT '',
    operator_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    executed_by TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    executed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dmr_incidents (
    incident_id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    current_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dmr_actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES dmr_incidents(incident_id) ON DELETE CASCADE,
    action_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    operator_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    executed_by TEXT NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ssl_incidents (
    incident_id TEXT PRIMARY KEY,
    server_name TEXT NOT NULL,
    common_name TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT 'SEAGATE TECHNOLOGY LLC',
    org_unit TEXT NOT NULL DEFAULT 'IT',
    locality TEXT NOT NULL DEFAULT 'Oklahoma City',
    state TEXT NOT NULL DEFAULT 'Oklahoma',
    country TEXT NOT NULL DEFAULT 'US',
    san_hostnames TEXT,
    csr_content TEXT,
    cer_content TEXT,
    current_status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Medium',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ssl_actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES ssl_incidents(incident_id) ON DELETE CASCADE,
    action_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    operator_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    executed_by TEXT NOT NULL DEFAULT 'WaferAutomationAgent',
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mdw_src_dest (
    mcass_id TEXT NOT NULL,
    dst_cid TEXT NOT NULL,
    dst_loc TEXT NOT NULL,
    lot_no TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hold_rels_detail (
    id SERIAL PRIMARY KEY,
    mcass_id TEXT NOT NULL,
    dmr_no TEXT,
    hold_code TEXT,
    hold_reason TEXT,
    hold_user TEXT,
    hold_at TIMESTAMPTZ,
    dmr_approved BOOLEAN DEFAULT FALSE,
    fg_failed BOOLEAN DEFAULT FALSE,
    UNIQUE(mcass_id)
);

CREATE TABLE IF NOT EXISTS p_hold_rels_log (
    dmr_no TEXT PRIMARY KEY,
    hold_status TEXT NOT NULL,
    drb_flag TEXT NOT NULL,
    yield_flag TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS p_rels_log_dtl (
    trans_id TEXT PRIMARY KEY,
    fg_id TEXT NOT NULL,
    released TEXT NOT NULL,
    dmr_no TEXT NOT NULL,
    is_ea TEXT,
    rehold TEXT NOT NULL,
    date_tm TIMESTAMPTZ NOT NULL,
    date_tm_tx TEXT,
    affected_disc_qty INTEGER
);

CREATE TABLE IF NOT EXISTS system_logs (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    source_system TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_code TEXT NOT NULL,
    message TEXT NOT NULL,
    lot_id TEXT,
    tool_id TEXT,
    host_name TEXT,
    incident_id TEXT,
    logged_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_soc_incidents_status ON soc_manual_intervention_incidents(current_status);
CREATE INDEX IF NOT EXISTS idx_image_incidents_status ON image_upload_failure_incidents(current_status);
CREATE INDEX IF NOT EXISTS idx_dmr_incidents_status ON dmr_incidents(current_status);
CREATE INDEX IF NOT EXISTS idx_ssl_incidents_status ON ssl_incidents(current_status);
CREATE INDEX IF NOT EXISTS idx_logs_domain ON system_logs(domain);
CREATE INDEX IF NOT EXISTS idx_logs_incident ON system_logs(incident_id);
"""


async def initialize_conversation_table():
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_CONVERSATION_HISTORY_TABLE_SQL)
        logger.info("Conversation history table initialized successfully")


async def initialize_session_store_table():
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SESSION_STORE_TABLE_SQL)
        logger.info("Session store table initialized successfully")


async def initialize_llm_usage_table():
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_LLM_USAGE_TABLE_SQL)
        logger.info("LLM usage table initialized successfully")


async def initialize_wafer_schema():
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_WAFER_SCHEMA_SQL)
        logger.info("Wafer schema initialized successfully")


async def append_message_to_conversation(
    conversation_id: str,
    role: str,
    content: str,
    agent_name: str = "WaferAgent",
) -> dict[str, Any]:
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            INSERT INTO conversation_history (
                conversation_id,
                agent_name,
                role,
                content,
                created_at
            )
            VALUES ($1, $2, $3, $4, NOW())
            RETURNING id, conversation_id, role, created_at
            """,
            conversation_id,
            agent_name,
            role,
            content,
        )
        return {
            "id": result["id"],
            "conversation_id": result["conversation_id"],
            "role": result["role"],
            "created_at": result["created_at"].isoformat(),
            "success": True,
        }


async def upsert_session_data(thread_id: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        value_json = json.dumps(value)
        result = await conn.fetchrow(
            """
            INSERT INTO session_store (thread_id, key, value, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (thread_id, key)
            DO UPDATE SET
                value = $3::jsonb,
                updated_at = NOW()
            RETURNING id, thread_id, key, created_at, updated_at
            """,
            thread_id,
            key,
            value_json,
        )
        return {
            "id": result["id"],
            "thread_id": result["thread_id"],
            "key": result["key"],
            "created_at": result["created_at"].isoformat(),
            "updated_at": result["updated_at"].isoformat(),
            "success": True,
        }


async def insert_llm_usage_record(
    session_id: str,
    thread_id: str,
    node_name: str,
    model_name: str,
    run_id: str,
    parent_run_id: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    total_tokens: int,
    details: dict[str, Any],
) -> dict[str, Any]:
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        details_json = json.dumps(details, default=str)
        result = await conn.fetchrow(
            """
            INSERT INTO llm_usage_log (
                session_id,
                thread_id,
                node_name,
                model_name,
                run_id,
                parent_run_id,
                input_tokens,
                cached_tokens,
                output_tokens,
                total_tokens,
                details,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, NOW())
            RETURNING id, session_id, thread_id, node_name, created_at
            """,
            session_id,
            thread_id,
            node_name,
            model_name,
            run_id,
            parent_run_id or None,
            input_tokens,
            cached_tokens,
            output_tokens,
            total_tokens,
            details_json,
        )
        return {
            "id": result["id"],
            "session_id": result["session_id"],
            "thread_id": result["thread_id"],
            "node_name": result["node_name"],
            "created_at": result["created_at"].isoformat(),
            "success": True,
        }


async def fetchrow(query: str, *args):
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch(query: str, *args):
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def execute(query: str, *args):
    pool = await DatabaseSingleton.get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)
