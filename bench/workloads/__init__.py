"""Bench workloads. One module per (task × framework × model) cell.

Each workload module exposes a top-level `WORKLOAD` instance (a subclass of
bench.workload.Workload). The runner discovers cells by listing modules in
this package.

Implemented so far:
    w5_adversarial — Workload 5, adversarial / known-bad inputs, Haiku 4.5.
        Proof-of-concept showing the Workload contract end-to-end.

To be implemented (see BENCH_PROTOCOL.md §"Bench matrix"):
    w1_code_langgraph        — code-gen + tests, LangGraph + Haiku 4.5
    w1_code_claude_agent_sdk — same workload, Claude Agent SDK adapter
    w2_debate_autogen        — multi-agent critique-revise, AutoGen + GPT-4.1-mini
    w2_debate_crewai         — same workload, CrewAI adapter
    w3_planner_openai_agents — planner-executor, OpenAI Agents SDK + GPT-4.1-mini
    w3_planner_langgraph     — planner-executor, LangGraph + Sonnet 4.6
    w4_rag_langchain         — iterative RAG, LangChain + Haiku 4.5 + embedding
    w5_adversarial_*         — adversarial across remaining frameworks
"""
