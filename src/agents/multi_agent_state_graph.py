import datetime
import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langchain_core.runnables.graph import MermaidDrawMethod

from langgraph.prebuilt import ToolNode
from src.prompts.StateGraph_Prompt.main_prompt import STATEGRAPH_ROUTER_SYSTEM_PROMPT_TEMPLATE
from src.prompts.StateGraph_Prompt.partial_normal_dmr_prompt import PARTIAL_NORMAL_DMR_SYSTEM_PROMPT_TEMPLATE
from src.prompts.StateGraph_Prompt.ssl_prompt import SSL_SYSTEM_PROMPT_TEMPLATE
from src.services.llm_usage_service import llm_usage_service
from src.state.checkpointer import get_checkpointer
from src.state.store import get_store
from src.tools.partial_dmr_tools import (
    collect_dmr_partial_release_information,
    perform_dmr_partial_release_resolution,
    triage_dmr_partial_release,
)
from src.tools.ssl_tools import (
    collect_ssl_information,
    create_csr_linux,
    create_csr_windows,
    raise_ssl_ticket,
    install_certificate,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

router_llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
    temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.1")),
)

partial_normal_dmr_tools = [
    collect_dmr_partial_release_information,
    triage_dmr_partial_release,
    perform_dmr_partial_release_resolution,
]

ssl_tools = [
    collect_ssl_information,
    create_csr_linux,
    create_csr_windows,
    raise_ssl_ticket,
    install_certificate,
]


def get_current_date_string() -> str:
    return datetime.datetime.now().strftime("%B %d, %Y (%A)")


class RouterState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    partial_normal_dmr_history: Annotated[list[BaseMessage], add_messages]
    ssl_history: Annotated[list[BaseMessage], add_messages]
    router_action: Literal["CALL_DMR_AUTOMATION", "CALL_SSL_AUTOMATION", "FINAL_RESPONSE"]
    router_request: str
    loop_count: int


ROUTER_DECISION_SCHEMA: dict[str, Any] = {
    "title": "RouterDecision",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["CALL_DMR_AUTOMATION", "CALL_SSL_AUTOMATION", "FINAL_RESPONSE"],
            "description": "Router next action: CALL_DMR_AUTOMATION, CALL_SSL_AUTOMATION, or FINAL_RESPONSE.",
        },
        "request": {
            "type": "string",
            "description": (
                "The exact user request to hand off to the specialist. "
                "Do NOT add any extra context, assumptions, or instructions. "
                "Required when action is CALL_DMR_AUTOMATION, empty for FINAL_RESPONSE."
            ),
        },
        "response": {
            "type": "string",
            "description": (
                "Final user response text. Required when action is FINAL_RESPONSE, "
                "empty for CALL_DMR_AUTOMATION."
            ),
        },
    },
    "required": ["action", "request", "response"],
}

DMR_PREFIX = "Tell the user ONLY this from `partial_normal_dmr_node`:"
SSL_PREFIX = "Tell the user ONLY this from `ssl_node`:"


def _extract_last_message_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return str(result)

    last_message = messages[-1]
    content = getattr(last_message, "content", "")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        merged = " ".join(part for part in text_parts if part)
        return merged.strip() or str(last_message)
    return str(content or last_message)


def _extract_last_ai_message_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return ""

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            merged = " ".join(part for part in text_parts if part)
            return merged.strip()
        return str(content or "")

    return ""


def _build_subagent_config(config: RunnableConfig | None, agent_scope: str) -> dict[str, Any]:
    current = config or {}
    parent_configurable = dict(current.get("configurable", {}) or {})
    parent_thread_id = str(parent_configurable.get("thread_id") or "").strip()
    if not parent_thread_id:
        return {}

    specialist_thread_id = f"{parent_thread_id}:{agent_scope}"
    logger.info(
        "[stategraph_subagent_config] scope=%s parent_thread_id=%s specialist_thread_id=%s",
        agent_scope,
        parent_thread_id,
        specialist_thread_id,
    )
    return {
        "configurable": {
            "thread_id": specialist_thread_id,
            "parent_thread_id": parent_thread_id,
        }
    }

async def router_node(state: RouterState, config: RunnableConfig) -> dict[str, Any]:
    writer = get_stream_writer()
    loop_count = int(state.get("loop_count", 0)) + 1
    if loop_count > 6:
        return {
            "router_action": "FINAL_RESPONSE",
            "loop_count": loop_count,
            "messages": [AIMessage(content="Please share one clear request, and I will continue from there.")],
        }

    messages = list(state.get("messages", []))
    dmr_history = list(state.get("partial_normal_dmr_history", []))
    ssl_history = list(state.get("ssl_history", []))

    # Intent Locking: If history already exists for a specialist, stay in that use case.
    if dmr_history:
        last_user_msg = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_user_msg = str(m.content)
                break
        logger.info("[stategraph_router] Locking session to DMR Specialist based on history.")
        return {
            "router_action": "CALL_DMR_AUTOMATION",
            "router_request": last_user_msg,
            "loop_count": loop_count,
            "messages": [AIMessage(content=f"Continuing with DMR specialist: {last_user_msg}")],
        }

    if ssl_history:
        last_user_msg = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_user_msg = str(m.content)
                break
        logger.info("[stategraph_router] Locking session to SSL Specialist based on history.")
        return {
            "router_action": "CALL_SSL_AUTOMATION",
            "router_request": last_user_msg,
            "loop_count": loop_count,
            "messages": [AIMessage(content=f"Continuing with SSL specialist: {last_user_msg}")],
        }

    system_prompt = STATEGRAPH_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(current_date=get_current_date_string())
    router_chain = router_llm.with_structured_output(ROUTER_DECISION_SCHEMA)
    router_config = llm_usage_service.build_config(config, node_name="router")
    decision = await router_chain.ainvoke([SystemMessage(content=system_prompt), *messages], config=router_config)
    logger.info("[stategraph_router] decision=%s", decision)

    decision_action = str(decision.get("action") or "").strip()
    decision_request = str(decision.get("request") or "").strip()
    decision_response = str(decision.get("response") or "").strip()

    if decision_action not in {"CALL_DMR_AUTOMATION", "CALL_SSL_AUTOMATION", "FINAL_RESPONSE"}:
        decision_action = "FINAL_RESPONSE"

    if decision_action == "FINAL_RESPONSE":
        response_text = decision_response or "I need one more precise detail from you to proceed."
        return {
            "router_action": "FINAL_RESPONSE",
            "loop_count": loop_count,
            "messages": [AIMessage(content=response_text)],
        }

    if decision_action == "CALL_DMR_AUTOMATION":
        return {
            "router_action": "CALL_DMR_AUTOMATION",
            "router_request": decision_request,
            "loop_count": loop_count,
            "messages": [AIMessage(content=f"Routing to DMR specialist: {decision_request}")],
        }

    if decision_action == "CALL_SSL_AUTOMATION":
        return {
            "router_action": "CALL_SSL_AUTOMATION",
            "router_request": decision_request,
            "loop_count": loop_count,
            "messages": [AIMessage(content=f"Routing to SSL specialist: {decision_request}")],
        }

    writer({"node": "router", "status": "Router could not build a specialist handoff and is asking for more detail."})
    return {
        "router_action": "FINAL_RESPONSE",
        "loop_count": loop_count,
        "messages": [AIMessage(content="Please share what you want me to do for DMR partial release.")],
    }

async def partial_normal_dmr_node(state: RouterState, config: RunnableConfig) -> dict[str, Any]:
    writer = get_stream_writer()
    partial_normal_dmr_history = list(state.get("partial_normal_dmr_history", []))
    router_request = str(state.get("router_request") or "").strip()

    new_messages = []
    if router_request:
        writer({"node": "partial_normal_dmr_node", "status": f"Processing handoff: {router_request}"})
        new_messages.append(HumanMessage(content=router_request))

    system_prompt = PARTIAL_NORMAL_DMR_SYSTEM_PROMPT_TEMPLATE.format(current_date=get_current_date_string())

    partial_normal_dmr_llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
        temperature=0.0,
    ).bind_tools(partial_normal_dmr_tools)

    partial_normal_dmr_config = llm_usage_service.build_config(config, node_name="partial_normal_dmr_node")
    response = await partial_normal_dmr_llm.ainvoke(
        [SystemMessage(content=system_prompt)] + partial_normal_dmr_history + new_messages,
        config=partial_normal_dmr_config
    )

    new_messages.append(response)

    updates: dict[str, Any] = {
        "partial_normal_dmr_history": new_messages,
        "router_request": "",
    }

    if not response.tool_calls:
        response_text = response.content or "[DMR automation completed]"
        prefixed_response = f"{DMR_PREFIX}\n{response_text}"
        updates["messages"] = [AIMessage(content=prefixed_response)]
        logger.info("[partial_normal_dmr_node] response=%s", prefixed_response)
        writer({"node": "partial_normal_dmr_node", "status": "DMR automation finished."})
        
    return updates

async def ssl_node(state: RouterState, config: RunnableConfig) -> dict[str, Any]:
    writer = get_stream_writer()
    ssl_history = list(state.get("ssl_history", []))
    router_request = str(state.get("router_request") or "").strip()

    new_messages = []
    if router_request:
        writer({"node": "ssl_node", "status": f"Processing handoff: {router_request}"})
        new_messages.append(HumanMessage(content=router_request))

    system_prompt = SSL_SYSTEM_PROMPT_TEMPLATE.format(current_date=get_current_date_string())

    ssl_llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
        temperature=0.0,
    ).bind_tools(ssl_tools)

    ssl_config = llm_usage_service.build_config(config, node_name="ssl_node")
    response = await ssl_llm.ainvoke(
        [SystemMessage(content=system_prompt)] + ssl_history + new_messages,
        config=ssl_config
    )

    new_messages.append(response)

    updates: dict[str, Any] = {
        "ssl_history": new_messages,
        "router_request": "",
    }

    if not response.tool_calls:
        response_text = response.content or "[SSL automation completed]"
        prefixed_response = f"{SSL_PREFIX}\n{response_text}"
        updates["messages"] = [AIMessage(content=prefixed_response)]
        logger.info("[ssl_node] response=%s", prefixed_response)
        writer({"node": "ssl_node", "status": "SSL automation finished."})
        
    return updates

def route_ssl(state: RouterState) -> str:
    if not state.get("ssl_history"):
        return "router"
    last_message = state["ssl_history"][-1]
    if last_message.tool_calls:
        return "ssl_tools"
    return END

def route_partial_normal_dmr(state: RouterState) -> str:
    last_message = state["partial_normal_dmr_history"][-1]
    if last_message.tool_calls:
        return "partial_normal_dmr_tools"
    return END

def route_from_router(state: RouterState) -> str:
    action = state.get("router_action", "FINAL_RESPONSE")
    if action == "CALL_DMR_AUTOMATION":
        return "partial_normal_dmr_node"
    if action == "CALL_SSL_AUTOMATION":
        return "ssl_node"
    return "end"

def create_seagate_stategraph_agent(use_memory_checkpointer: bool = False):
    if use_memory_checkpointer:
        supervisor_checkpointer = MemorySaver()
        logger.info("Using in-memory checkpointer for Seagate router")
    else:
        supervisor_checkpointer = get_checkpointer()
        logger.info("Using persistent checkpointer for Seagate router")

    store = get_store()

    builder = StateGraph(RouterState)
    builder.add_node("router", router_node)
    builder.add_node("partial_normal_dmr_node", partial_normal_dmr_node)
    builder.add_node("partial_normal_dmr_tools", ToolNode(partial_normal_dmr_tools, messages_key="partial_normal_dmr_history"))
    builder.add_node("ssl_node", ssl_node)
    builder.add_node("ssl_tools", ToolNode(ssl_tools, messages_key="ssl_history"))

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_from_router,
        {
            "partial_normal_dmr_node": "partial_normal_dmr_node",
            "ssl_node": "ssl_node",
            "end": END
        },
    )
    builder.add_conditional_edges(
        "partial_normal_dmr_node",
        route_partial_normal_dmr,
        {"partial_normal_dmr_tools": "partial_normal_dmr_tools", END: END},
    )
    builder.add_edge("partial_normal_dmr_tools", "partial_normal_dmr_node")

    builder.add_conditional_edges(
        "ssl_node",
        route_ssl,
        {"ssl_tools": "ssl_tools", END: END},
    )
    builder.add_edge("ssl_tools", "ssl_node")

    graph = builder.compile(checkpointer=supervisor_checkpointer, store=store)
    logger.info("StateGraph Seagate Agent Assistant supervisor compiled successfully with shared state architecture")

    # Save graph visualization to a file
    try:
        # Use the remote Mermaid.ink API to render the graph (no local dependencies needed)
        img_data = graph.get_graph().draw_mermaid_png(draw_method=MermaidDrawMethod.API)
        with open("seagate_stategraph.png", "wb") as f:
            f.write(img_data)
        logger.info("StateGraph figure saved successfully to seagate_stategraph.png")
    except Exception as e:
        logger.warning("Could not generate graph figure: %s", e)

    return graph


def create_seagate_stategraph_agent_dev():
    return create_seagate_stategraph_agent(use_memory_checkpointer=True)
