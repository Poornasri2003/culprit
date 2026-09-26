"""Minimal watsonx Orchestrate client — deploy the Compass agent and skill."""
from __future__ import annotations

from dataclasses import dataclass

from compass.config import OrchestrateConfig


@dataclass
class OrchestrateClient:
    config: OrchestrateConfig

    def deploy_agent(self, agent_yaml: str, openapi_yaml: str) -> str:
        """Register the Compass agent + OpenAPI tool; returns the agent id."""
        raise NotImplementedError("OrchestrateClient.deploy_agent")
