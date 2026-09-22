from abc import ABC, abstractmethod

from .schemas import NormalizedResponse


class ResearchAgent(ABC):
    @abstractmethod
    def research(self, question: str, model_id: str) -> NormalizedResponse:
        """Return a normalized result, or raise a safe AgentError."""
