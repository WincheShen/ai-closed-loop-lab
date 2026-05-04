from __future__ import annotations

from typing import Callable, Dict, List, Any


class WorkflowStep:
    def __init__(self, name: str, handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.name = name
        self.handler = handler


class Workflow:
    """
    Simple sequential workflow.

    Later this can evolve into a DAG-based engine.
    """

    def __init__(self, name: str):
        self.name = name
        self.steps: List[WorkflowStep] = []

    def step(self, name: str):
        def decorator(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
            self.steps.append(WorkflowStep(name, fn))
            return fn

        return decorator

    def run(self, context: Dict[str, Any]):
        state = context

        for step in self.steps:
            state = step.handler(state)

        return state


class WorkflowEngine:
    """
    Minimal workflow registry and executor.

    Later versions will support:
    - async tasks
    - event triggers
    - DAG workflows
    """

    def __init__(self):
        self.workflows: Dict[str, Workflow] = {}

    def register(self, workflow: Workflow):
        self.workflows[workflow.name] = workflow

    def run(self, workflow_name: str, context: Dict[str, Any]):
        workflow = self.workflows.get(workflow_name)

        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_name}")

        return workflow.run(context)
