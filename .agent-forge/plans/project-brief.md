# Project Brief: itom-orchestrator

## Overview
The central coordinator for all ITOM agents in the ServiceNow Suite, built as an MCP server. It routes tasks to the appropriate agent based on domain, manages multi-agent workflows with state machine execution, enforces role boundaries between agents, provides inter-agent communication via message passing and event bus, and maintains execution state across the entire ITOM automation suite.

## Objectives
1. Route tasks to the correct ITOM agent based on domain, capability, and availability
2. Execute multi-agent workflows with state machine transitions, checkpoint/resume, and timeout handling
3. Enforce role boundaries ensuring agents only operate within their designated domains
4. Provide inter-agent communication through message passing and lifecycle event broadcasting
5. Maintain a comprehensive agent registry with health monitoring and dynamic configuration

## Target Users
- The ITOM Chat UI (itom-chat-ui) as the primary human-facing interface
- All ITOM agents (Discovery, Asset, CMDB, CSA, Auditor, Documentator) as managed participants
- DevOps teams needing coordinated multi-agent ITOM operations
- AI assistants orchestrating complex ITOM workflows via MCP protocol

## Tech Stack
- **Languages:** Python 3.11+
- **Frameworks:** FastMCP, Pydantic
- **Databases:** None (JSON file-based state persistence)
- **APIs/Services:** MCP Protocol (exposes 10+ orchestration tools)
- **Infrastructure:** Claude Code CLI

## Requirements

### Must Have (P0)
1. Agent registry with registration, capability declaration, and health checking
2. Domain-based task routing engine with configurable routing rules
3. Multi-agent workflow execution engine with state machine and checkpoint/resume
4. State persistence layer with atomic writes and file locking
5. Core Pydantic models for agents, tasks, workflows, and messages

### Should Have (P1)
1. Inter-agent message passing with delivery guarantees
2. Event bus for lifecycle events (agent registered, task started, workflow completed)
3. Role boundary enforcement with audit trail
4. Pre-built workflow templates for common ITOM operations

### Nice to Have (P2)
1. Workflow scheduling and prioritization
2. Agent load balancing and failover
3. Performance metrics and dashboard data
4. Integration tests with real agent endpoints

## Constraints
- Must be agent-agnostic -- works with any MCP-compatible agent
- Workflow definitions must be declarative (JSON-based, not code)
- State persistence must handle concurrent access safely
- Must coordinate with all 6 ITOM agents without tight coupling

## Existing Codebase
- **Starting from scratch:** No -- Phase 1 complete with foundation in place
- **Existing repo:** https://github.com/amragl/itom-orchestrator.git
- **Current state:** Active development. 4/25 tickets complete (16%). Phase 1 (Foundation) done.
- **Technical debt:** None identified yet

## Dependencies
- All ITOM agents (execution order #6 -- depends on all agents being available)
- Python 3.11+ runtime
- No external database -- uses JSON file persistence

## Success Criteria
1. All 6 ITOM agents registered and health-monitored through the orchestrator
2. Task routing correctly dispatches to the right agent by domain
3. Multi-agent workflows execute with proper state transitions and checkpoint/resume
4. Role boundaries prevent agents from operating outside their domain
5. 90%+ code coverage with all error paths tested

## Notes
- Execution order #6 in the ServiceNow Suite -- depends on all individual agents
- Phase 1 (Foundation) complete: project scaffold, MCP server, core models, state persistence
- Uses ORCH-xxx ticket prefix in the backlog
- 25 tickets across 7 phases
