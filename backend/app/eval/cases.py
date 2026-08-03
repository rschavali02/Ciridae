"""Shape of a single eval case.

A case is a description of the world the agent wakes up in -- which vendor
exists, what has already been paid, what PO was raised -- plus the decision a
careful reviewer would reach given that world.
"""

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    name: str
    invoice: dict
    expected_decision: str
    vendor: dict | None = None
    past_invoices: list[dict] = field(default_factory=list)
    purchase_order: dict | None = None
    expected_tools: list[str] = field(default_factory=list)
    # Cases whose correct answer depends on a rule that exists only in the AP
    # policy. These are expected to fail at the Phase 4 baseline; the whole
    # point of running the suite twice is to see whether retrieval fixes them.
    needs_policy: bool = False


@dataclass
class TrialResult:
    decision: str | None
    confidence: float | None
    tools_called: list[str]
    reasoning: str | None
    tool_calls: list[dict]


@dataclass
class CaseResult:
    case_name: str
    trials: list[TrialResult]
