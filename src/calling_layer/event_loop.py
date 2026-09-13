from __future__ import annotations

from langgraph.types import Command

from src.calling_layer.formatters import (
    format_event,
    format_final_state,
    format_interrupt,
)


def run_event_loop(graph, config: dict, initial_input: dict | None = None) -> dict:
    current_input = initial_input or {"messages": []}

    while True:
        print("\n" + "-" * 40)
        for event in graph.stream(current_input, config):
            print(format_event(event))

        state = graph.get_state(config)
        if not state.next:
            print(format_final_state(state.values))
            return state.values

        print(format_interrupt(state.values))
        user_input = input("> ").strip()

        current_input = Command(resume=user_input)
