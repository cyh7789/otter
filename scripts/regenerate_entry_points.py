"""Regenerate entry-points.json from the live Pydantic schemas.

UiPath Cloud reads entry-points.json to wire the agent's IO ports.
Hand-maintaining it means the BPMN ports drift from the runtime state
(which exactly what Codex high #4 warned about). Run this script
whenever schemas.py or graph.State change.

    uv run python scripts/regenerate_entry_points.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schemas import (  # noqa: E402
    DiagnosisOutput,
    EvidenceBundle,
    IncidentTrigger,
    KillSwitchDecision,
    PolicyDecision,
    RoutingProposal,
)


def build() -> dict:
    input_schema = {
        "type": "object",
        "properties": {
            "trigger": IncidentTrigger.model_json_schema(),
            "fixture_mode": {
                "type": "boolean",
                "default": False,
                "description": "If true, every node returns deterministic mock data and skips outbound LLM calls.",
            },
        },
        "required": ["trigger"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "diagnosis": DiagnosisOutput.model_json_schema(),
            "routing_proposal": RoutingProposal.model_json_schema(),
            "policy_decision": PolicyDecision.model_json_schema(),
            "kill_switch": KillSwitchDecision.model_json_schema(),
            "evidence_bundle": EvidenceBundle.model_json_schema(),
            "audit_log": {
                "type": "array",
                "items": {"type": "object"},
                "description": "One row per graph node, in execution order.",
            },
        },
    }

    return {
        "$schema": "https://cloud.uipath.com/draft/2024-12/entry-point",
        "$id": "entry-points.json",
        "entryPoints": [
            {
                "filePath": "otter",
                "uniqueId": "a8546758-2975-42e7-bbce-8a1769861fde",
                "type": "agent",
                "input": input_schema,
                "output": output_schema,
            }
        ],
    }


def main() -> int:
    target = ROOT / "entry-points.json"
    payload = build()
    target.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[entry-points] regenerated {target}")
    print(f"[entry-points] input properties: {list(payload['entryPoints'][0]['input']['properties'].keys())}")
    print(f"[entry-points] output properties: {list(payload['entryPoints'][0]['output']['properties'].keys())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
