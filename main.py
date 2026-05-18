"""Local debug runner — invokes the multi-agent graph standalone."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from graph import graph  # noqa: E402 — must follow load_dotenv()


def main() -> None:
    user_input = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else "Compare LangGraph vs CrewAI for multi-agent orchestration in 200 words."
    )

    print(f"\n=== User input ===\n{user_input}\n")

    # Stream events to see each agent's turn
    config = {"recursion_limit": 20}
    final_state = None
    for event in graph.stream(
        {"messages": [{"role": "user", "content": user_input}], "next_agent": ""},
        config=config,
        stream_mode="updates",
    ):
        for node_name, node_output in event.items():
            print(f"\n--- {node_name} ---")
            for msg in node_output.get("messages", []):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                print(content[:600] + ("..." if len(content) > 600 else ""))
            if "next_agent" in node_output:
                print(f"[routing decision: {node_output['next_agent']}]")
        final_state = event

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
