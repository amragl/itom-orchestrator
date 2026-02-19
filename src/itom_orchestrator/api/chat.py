"""
Chat endpoint models and logic for the ITOM Orchestrator HTTP API.

Handles incoming chat messages from itom-chat-ui, routes them to the
appropriate ITOM agent via the TaskRouter, and returns structured responses.

This module implements ORCH-027: POST /api/chat endpoint for message routing.
SE-011: Adds ClarificationResponse model and pending-clarification token store.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskStatus

# Module-level store for pending clarifications.
# Maps pending_message_token -> {original_message, session_id, created_at}
_pending_clarifications: dict[str, dict[str, Any]] = {}

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class ChatRequest(BaseModel):
    """Incoming chat message from itom-chat-ui.

    Attributes:
        message: The user's chat message text.
        target_agent: Optional explicit agent ID to route the message to.
            If not provided, the router determines the best agent.
        domain: Optional domain hint for routing (cmdb, discovery, etc.).
        context: Optional context from the chat session (prior messages,
            selected CI, etc.) passed to the agent for context-aware responses.
        session_id: Optional session identifier for conversation continuity.
    """

    message: str
    target_agent: str | None = None
    domain: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_must_be_non_empty(cls, v: str) -> str:
        """Message must not be empty or whitespace-only."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Chat message must not be empty")
        return stripped


class ChatResponse(BaseModel):
    """Response to a chat message.

    Attributes:
        message_id: Unique identifier for this response.
        status: Response status (success, error, routed).
        agent_id: The agent that handled the message.
        agent_name: Human-readable name of the handling agent.
        domain: The domain the message was routed to.
        response: The agent's response content.
        routing_method: How the message was routed (explicit, domain, rule, etc.).
        timestamp: When the response was generated.
        session_id: Echo of the session_id from the request.
    """

    message_id: str
    status: str
    agent_id: str
    agent_name: str
    domain: str
    response: dict[str, Any]
    routing_method: str
    timestamp: str
    session_id: str | None = None


class ChatErrorResponse(BaseModel):
    """Error response for chat endpoint failures.

    Attributes:
        status: Always "error".
        error_code: Machine-readable error code.
        error_message: Human-readable error description.
        timestamp: When the error occurred.
    """

    status: str = "error"
    error_code: str
    error_message: str
    timestamp: str


class ClarificationResponse(BaseModel):
    """Response emitted when the router cannot disambiguate a message.

    The client should present the question and options to the user and
    then POST /api/chat/clarify with the chosen answer and the token.

    Attributes:
        message_id: Unique ID for this response.
        response_type: Always "clarification".
        question: The question to show the user.
        options: Selectable option strings.
        pending_message_token: Opaque token to reference the original message
            in the /api/chat/clarify request.
        session_id: Echoed from the request.
        timestamp: When this response was generated.
    """

    message_id: str
    response_type: str = "clarification"
    question: str
    options: list[str]
    pending_message_token: str
    session_id: str | None = None
    timestamp: str


def process_chat_message(
    request: ChatRequest,
    router: Any,
    executor: Any,
) -> "ChatResponse | ClarificationResponse":
    """Process a chat message by routing it to the appropriate agent.

    Creates a Task from the chat message, checks for routing ambiguity,
    routes it via the TaskRouter, executes it via the TaskExecutor, and
    returns a structured response.

    If the router detects an ambiguous query (two domains match at the same
    priority), returns a ClarificationResponse instead of executing.  The
    caller should surface the clarification question to the user and then
    call process_clarified_message() with the user's answer.

    Args:
        request: The incoming chat request.
        router: TaskRouter instance for routing.
        executor: TaskExecutor instance for execution.

    Returns:
        ChatResponse with the agent's response, or ClarificationResponse
        when the query is ambiguous.

    Raises:
        ValueError: If the domain is invalid.
        Exception: Re-raised routing or execution errors.
    """
    # Generate a unique task ID for this chat message
    task_id = f"chat-{uuid.uuid4().hex[:12]}"

    # Parse domain if provided
    parsed_domain = None
    if request.domain:
        try:
            parsed_domain = AgentDomain(request.domain)
        except ValueError:
            raise ValueError(
                f"Invalid domain '{request.domain}'. "
                f"Valid domains: {[d.value for d in AgentDomain]}"
            )

    # Create a task from the chat message
    task = Task(
        task_id=task_id,
        title=request.message[:100],  # First 100 chars as title
        description=request.message,
        domain=parsed_domain,
        target_agent=request.target_agent,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.PENDING,
        parameters={
            "source": "chat-ui",
            "session_id": request.session_id,
            "context": request.context,
            "full_message": request.message,
        },
        created_at=datetime.now(UTC),
        timeout_seconds=30.0,  # Chat messages get a shorter timeout
        max_retries=1,  # One retry for chat
    )

    logger.info(
        "Processing chat message",
        extra={
            "extra_data": {
                "task_id": task_id,
                "domain": request.domain,
                "target_agent": request.target_agent,
                "message_length": len(request.message),
            }
        },
    )

    # Check for routing ambiguity before attempting to route (SE-011)
    clarification = router.detect_ambiguity(task)
    if clarification is not None:
        pending_token = uuid.uuid4().hex
        _pending_clarifications[pending_token] = {
            "original_message": request.message,
            "session_id": request.session_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        logger.info(
            "Returning clarification request",
            extra={
                "extra_data": {
                    "task_id": task_id,
                    "competing_domains": clarification.competing_domains,
                    "pending_token": pending_token,
                }
            },
        )
        return ClarificationResponse(
            message_id=task_id,
            question=clarification.question,
            options=clarification.options,
            pending_message_token=pending_token,
            session_id=request.session_id,
            timestamp=datetime.now(UTC).isoformat(),
        )

    # Route the task
    decision = router.route(task)

    # Execute the task
    result = executor.execute(task, decision)

    # Build the response
    response = ChatResponse(
        message_id=task_id,
        status="success",
        agent_id=decision.agent.agent_id,
        agent_name=decision.agent.name,
        domain=decision.agent.domain.value,
        response={
            "task_id": task_id,
            "result": result.result_data,
            "routing": {
                "method": decision.method,
                "reason": decision.reason,
            },
        },
        routing_method=decision.method,
        timestamp=datetime.now(UTC).isoformat(),
        session_id=request.session_id,
    )

    logger.info(
        "Chat message processed",
        extra={
            "extra_data": {
                "task_id": task_id,
                "agent_id": decision.agent.agent_id,
                "routing_method": decision.method,
            }
        },
    )

    return response
