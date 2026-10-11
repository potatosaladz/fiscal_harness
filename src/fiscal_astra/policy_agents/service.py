"""Invocation and persistence service for typed policy expert agents."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database import AgentMessage, AgentSession
from fiscal_astra.database.enums import AgentRole, MessageRole
from fiscal_astra.llm.contracts import LlmPurpose, LlmRequest
from fiscal_astra.llm.errors import LlmBoundaryError
from fiscal_astra.llm.gateway import LlmGateway
from fiscal_astra.policy_agents.contracts import (
    PolicyAgentCapability,
    PolicyAgentContext,
    PolicyAgentOutput,
)
from fiscal_astra.policy_agents.protocols import PolicyContextProvider
from fiscal_astra.policy_agents.registry import resolve_policy_agent


class PolicyAgentService:
    """Load context, invoke one expert, and append its complete conversation."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateways: dict[str, LlmGateway],
        context_provider: PolicyContextProvider,
    ) -> None:
        self._session_factory = session_factory
        self._gateways = gateways
        self._context_provider = context_provider

    def invoke(
        self, *, run_id: str, agent_id: str, trace_id: str | None = None
    ) -> PolicyAgentOutput:
        capability = resolve_policy_agent(agent_id)
        gateway = self._gateways[agent_id]
        context = self._context_provider.load_context(run_id=run_id, agent_id=agent_id)
        trace = trace_id or uuid.uuid4().hex
        session_id = self._start_session(run_id, capability.agent_id, capability.prompt_version)
        self._append_message(
            session_id,
            MessageRole.SYSTEM,
            self._system_prompt(capability),
            tool_name=None,
        )
        self._append_message(
            session_id,
            MessageRole.TOOL,
            context.model_dump_json(),
            tool_name=self._context_provider.tool_name,
        )
        request = LlmRequest(
            agent_id=agent_id,
            purpose=LlmPurpose.DELIBERATION,
            prompt_version=capability.prompt_version,
            system_prompt=self._system_prompt(capability),
            user_prompt=json.dumps(
                {
                    "instruction": "Return an independent policy position.",
                    "context": context.model_dump(mode="json"),
                },
                sort_keys=True,
            ),
            response_schema=PolicyAgentOutput,
            pre_schema_validator=self._provenance_validator(agent_id, context),
        )
        try:
            response = gateway.invoke(request, run_id=uuid.UUID(run_id), trace_id=trace)
            output = PolicyAgentOutput.model_validate(response.output)
            self._append_message(session_id, MessageRole.ASSISTANT, output.model_dump_json(), None)
            return output
        finally:
            # The session must be closed whether the invocation succeeded, was
            # refused by the boundary, or failed terminally.
            self._finish_session(session_id, gateway.provider.model_name)

    @staticmethod
    def _system_prompt(capability: PolicyAgentCapability) -> str:
        tasks = "; ".join(capability.tasks)
        criteria = ", ".join(capability.evaluation_criteria)
        return (
            f"You are {capability.title}. Use only supplied evidence references and numeric "
            "references. Do not produce economic numbers. Mark unsupported claims as assumptions. "
            f"Tasks: {tasks}. Criteria: {criteria}."
        )

    @staticmethod
    def _provenance_validator(
        agent_id: str, context: PolicyAgentContext
    ) -> Callable[[dict[str, Any]], None]:
        verified = {
            item.evidence_id
            for item in context.evidence_pack
            if item.verification_status == "verified"
        }

        def validate(payload: dict[str, Any]) -> None:
            if payload.get("agent_id") != agent_id:
                raise LlmBoundaryError(
                    f"response agent_id does not match requested agent {agent_id!r}",
                    agent_id=agent_id,
                )
            for claim in payload.get("claims", []):
                if claim.get("type") == "assumption":
                    continue
                missing = set(claim.get("evidence_ids", [])) - verified
                if not claim.get("evidence_ids") or missing:
                    raise LlmBoundaryError(
                        f"claim lacks verified evidence references: {sorted(missing)}",
                        agent_id=agent_id,
                    )
            for evaluation in payload.get("evaluation", {}).values():
                missing = set(evaluation.get("evidence_ids", [])) - verified
                if missing:
                    raise LlmBoundaryError(
                        f"evaluation cites unverified evidence: {sorted(missing)}",
                        agent_id=agent_id,
                    )

        return validate

    def _start_session(self, run_id: str, agent_id: str, prompt_version: str) -> uuid.UUID:
        session_id = uuid.uuid4()
        with self._session_factory.begin() as session:
            session.add(
                AgentSession(
                    id=session_id,
                    run_id=uuid.UUID(run_id),
                    agent_id=agent_id,
                    role=AgentRole.POLICY_EXPERT.value,
                    prompt_version=prompt_version,
                    started_at=datetime.now(UTC),
                    session_metadata={"context_tool": self._context_provider.tool_name},
                )
            )
        return session_id

    def _append_message(
        self, session_id: uuid.UUID, role: MessageRole, content: str, tool_name: str | None
    ) -> None:
        with self._session_factory.begin() as session:
            # ponytail: read-then-write sequence assignment. Safe while one
            # session is driven by a single worker; switch to a DB-side
            # nextval/identity column if sessions ever run concurrently.
            last = session.scalar(
                select(AgentMessage.sequence)
                .where(AgentMessage.session_id == session_id)
                .order_by(AgentMessage.sequence.desc())
                .limit(1)
            )
            session.add(
                AgentMessage(
                    session_id=session_id,
                    sequence=(last + 1 if last is not None else 0),
                    role=role.value,
                    content=content,
                    tool_name=tool_name,
                    message_metadata={},
                    created_at=datetime.now(UTC),
                )
            )

    def _finish_session(self, session_id: uuid.UUID, model_name: str) -> None:
        with self._session_factory.begin() as session:
            stored = session.get(AgentSession, session_id)
            if stored is not None:
                stored.model_name = model_name
                stored.finished_at = datetime.now(UTC)


__all__ = ["PolicyAgentService"]
