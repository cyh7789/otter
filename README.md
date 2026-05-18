# Otter

Multi-agent orchestration via LangGraph + UiPath Cloud Platform.

**UiPath AgentHack 2026 — Track 2 (Maestro BPMN) submission.**

## Quickstart

```bash
# 1. Install dependencies
uv sync

# 2. Set up environment
cp .env.example .env
# Edit .env: fill in GOOGLE_API_KEY (LangChain → Gemini, free tier available)

# 3. Local debug (no UiPath account needed)
uv run main.py

# 4. UiPath Cloud deployment
uv run uipath auth     # browser login, populates UIPATH_URL + UIPATH_ACCESS_TOKEN
uv run uipath init     # generate entry-points.json from langgraph.json
uv run uipath pack     # build .nupkg
uv run uipath publish  # deploy to UiPath Cloud
```

## Architecture

Currently a single-node LangGraph starter. Roadmap:

- Multi-agent supervisor (alias-style handoff)
- UiPath services integration via uipath-python SDK (assets / buckets / queues / context_grounding)
- Human-in-the-loop via UiPath Action Center
- Optional: switch from `langchain-google-genai` to `uipath-llm-client` for deeper platform integration (UiPath LLM gateway)

## Coding Agent Disclosure

This project leverages **Claude Code** (Anthropic) for development. Per UiPath AgentHack bonus
criteria, prompt logs and conversation evidence are preserved in this repository.

## License

Apache 2.0
