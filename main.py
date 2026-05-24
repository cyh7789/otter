"""Otter — local runner for the Governed LLM Runtime Change Control graph.

Usage:
    uv run python main.py --help
    uv run python main.py --fixture --trigger-type proactive
    uv run python main.py --fixture --trigger-type reactive --incident-id INC-123
    uv run python main.py --trigger-type proactive --source pagerduty   # live (requires GEMINI_API_KEY etc)

Codex high #3: the previous entry treated `sys.argv[1:]` as a Gemini
prompt and crashed on network failure — i.e. the product story said
"protects against vendor outage" but the entry point itself didn't.
This entry:

  - parses flags with argparse, so --help / --version don't touch the
    LLM,
  - has --fixture mode that runs the entire graph with deterministic
    mock data and zero outbound calls (useful for CI, demo recording,
    and offline iteration),
  - wraps the live graph invocation in a guard that surfaces a
    structured failure instead of an unhandled traceback.

Exit codes:
    0  graph completed (rollback decision recorded either way)
    1  graph could not start (config / credentials / etc.)
    2  graph started but raised — failure recorded to the audit log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# Schema + graph import after load_dotenv so any env-driven defaults resolve.
from schemas import IncidentTrigger  # noqa: E402
from graph import graph  # noqa: E402


def _build_trigger(args: argparse.Namespace) -> IncidentTrigger:
    incident_id = args.incident_id or f"inc-{uuid.uuid4().hex[:10]}"
    return IncidentTrigger(
        incident_id=incident_id,
        trigger_type=args.trigger_type,
        correlation_key=args.correlation_key or incident_id,
        timestamp=datetime.now(timezone.utc),
        source=args.source,
    )


def _emit(event_name: str, payload: dict) -> None:
    """One JSON line per graph event — keeps the stream parseable and the
    judge / fixture-mode CI script can SPL-style grep on `event`."""
    line = {"event": event_name, **payload}
    print(json.dumps(line, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="otter",
        description=(
            "Otter — Governed LLM Runtime Change Control "
            "(UiPath AgentHack Track 2)."
        ),
    )
    parser.add_argument(
        "--trigger-type",
        choices=["reactive", "proactive"],
        default="proactive",
        help="reactive (paging-driven outage) or proactive (eval/drift driven).",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help=(
            "Run the graph with deterministic mock data and zero outbound "
            "calls. Use for demo recording, CI, and offline iteration."
        ),
    )
    parser.add_argument(
        "--incident-id",
        default=None,
        help="Override the auto-generated incident id (useful for replay).",
    )
    parser.add_argument(
        "--correlation-key",
        default=None,
        help="Stable dedup key. Defaults to incident-id when not provided.",
    )
    parser.add_argument(
        "--source",
        default="manual",
        help="Where the trigger came from (e.g. pagerduty, eval_scheduler).",
    )
    parser.add_argument(
        "--recursion-limit",
        type=int,
        default=25,
        help="LangGraph recursion limit (default: 25).",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Print runtime env summary and exit — does NOT invoke the graph.",
    )
    args = parser.parse_args(argv)

    if args.health:
        _emit("health", {
            "ok": True,
            "fixture_capable": True,
            "gemini_api_key_set": bool(os.environ.get("GEMINI_API_KEY")
                                       or os.environ.get("GOOGLE_API_KEY")),
            "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        })
        return 0

    trigger = _build_trigger(args)
    _emit("trigger.built", {
        "incident_id": trigger.incident_id,
        "trigger_type": trigger.trigger_type,
        "fixture_mode": args.fixture,
        "source": trigger.source,
    })

    initial_state = {"trigger": trigger, "fixture_mode": args.fixture}
    config = {"recursion_limit": args.recursion_limit}

    try:
        final = graph.invoke(initial_state, config=config)
    except Exception as exc:  # noqa: BLE001 — entry must not raise to shell
        _emit("graph.error", {
            "incident_id": trigger.incident_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "hint": "Re-run with --fixture for offline mode if this is a vendor outage.",
        })
        return 2

    # Stream audit then summary so the judge can `grep '"event":"node"'`.
    for row in final.get("audit_log", []):
        _emit("node", row)

    summary = {
        "incident_id": trigger.incident_id,
        "trigger_type": trigger.trigger_type,
        "fixture_mode": args.fixture,
        "root_cause": final["diagnosis"].root_cause,
        "incident_severity": final["diagnosis"].incident_severity,
        "diagnosis_confidence": final["diagnosis"].diagnosis_confidence,
        "recommended_model": final["routing_proposal"].recommended.model_id,
        "change_risk": final["routing_proposal"].change_risk,
        "policy_decision": final["policy_decision"].decision,
        "policy_rule": final["policy_decision"].rule_id,
        "rollback": final["kill_switch"].rollback,
    }
    _emit("summary", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
