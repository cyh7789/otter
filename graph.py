"""Otter: Multi-agent supervisor pattern for UiPath AgentHack Track 2 (Maestro BPMN).

Architecture:
    START → supervisor → (analyst | researcher | writer) → supervisor → ... → END

The supervisor routes each turn to one of three specialists based on the conversation
state. Specialists do their work and return to the supervisor, which decides whether
to continue routing or finish.

This pattern maps cleanly onto BPMN 2.0:
- supervisor = exclusive gateway
- analyst / researcher / writer = task nodes
- END = end event
"""

from __future__ import annotations

import os
from typing import Annotated

from typing_extensions import TypedDict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages


class State(TypedDict):
    """Shared state — messages accumulate, supervisor records next routing decision."""

    messages: Annotated[list, add_messages]
    next_agent: str


def make_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
    )


def _content_as_text(message) -> str:
    """Normalize LangChain message content to plain string.

    Gemini sometimes returns structured content (list of dict) — flatten for routing.
    """
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        parts = []
        for item in message.content:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(message.content)


def supervisor(state: State) -> dict:
    """Decides which specialist runs next, or FINISH if the task is complete."""
    llm = make_llm()
    system = SystemMessage(
        content=(
            "You are the Otter supervisor, coordinating three specialists:\n"
            "- ANALYST: analyzes data, computes metrics, evaluates trade-offs\n"
            "- RESEARCHER: gathers information, summarizes sources, contextualizes\n"
            "- WRITER: drafts the final user-facing output\n\n"
            "Look at the conversation so far. Respond with EXACTLY two lines:\n"
            "NEXT: <ANALYST|RESEARCHER|WRITER|FINISH>\n"
            "REASON: <one short sentence>\n\n"
            "Rules:\n"
            "- Choose FINISH only after WRITER has produced a polished final answer.\n"
            "- Don't loop the same specialist twice unless needed.\n"
            "- Typical flow: RESEARCHER → ANALYST → WRITER → FINISH."
        )
    )
    response = llm.invoke([system, *state["messages"]])
    content = _content_as_text(response)

    next_agent = "FINISH"
    for line in content.splitlines():
        if line.upper().startswith("NEXT:"):
            value = line.split(":", 1)[1].strip().upper().rstrip(".,!?")
            if value in ("ANALYST", "RESEARCHER", "WRITER"):
                next_agent = value.lower()
            elif value == "FINISH":
                next_agent = "FINISH"
            break

    return {
        "next_agent": next_agent,
        "messages": [
            HumanMessage(
                content=f"[supervisor → {next_agent}]\n{content}",
                name="supervisor",
            )
        ],
    }


def make_specialist(role: str, system_prompt: str):
    """Factory for specialist agent nodes — each has a different system prompt."""

    def node(state: State) -> dict:
        llm = make_llm()
        system = SystemMessage(content=system_prompt)
        response = llm.invoke([system, *state["messages"]])
        return {
            "messages": [
                HumanMessage(
                    content=_content_as_text(response),
                    name=role,
                )
            ]
        }

    return node


analyst = make_specialist(
    "analyst",
    "You are the analyst specialist. Examine the user's request and any prior context, "
    "then provide concise analysis: key metrics, comparisons, or trade-offs. "
    "Be direct, no fluff. Keep response under 150 words.",
)

researcher = make_specialist(
    "researcher",
    "You are the researcher specialist. Gather and summarize relevant information for "
    "the user's request based on your training knowledge. Note assumptions and "
    "limitations. Keep response under 150 words.",
)

writer = make_specialist(
    "writer",
    "You are the writer specialist. Draft the final response to the user based on the "
    "conversation so far (including prior analyst/researcher contributions). "
    "Format clearly, structure for readability. This is the polished final answer.",
)


def route_after_supervisor(state: State) -> str:
    """Conditional edge: route based on supervisor's decision."""
    next_agent = state.get("next_agent", "FINISH")
    return END if next_agent == "FINISH" else next_agent


def build_graph() -> StateGraph:
    builder = StateGraph(State)
    builder.add_node("supervisor", supervisor)
    builder.add_node("analyst", analyst)
    builder.add_node("researcher", researcher)
    builder.add_node("writer", writer)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "analyst": "analyst",
            "researcher": "researcher",
            "writer": "writer",
            END: END,
        },
    )
    # Specialists return to supervisor for next decision
    builder.add_edge("analyst", "supervisor")
    builder.add_edge("researcher", "supervisor")
    builder.add_edge("writer", "supervisor")

    return builder.compile()


graph = build_graph()
