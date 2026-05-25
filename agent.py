from enum import unique
import os
from click import Option
import requests
import datetime
from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool
from google.adk.code_executors import BuiltInCodeExecutor
from google.genai import types


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Centralized model strings — change here, updates everywhere
FAST_MODEL  = "gemini-flash-latest"   # all three agents use this
SMART_MODEL = "gemini-pro-latest"     # swap root_agent here for harder tasks

# Temperature per agent role
SEARCH_TEMP = 0.1   # factual search needs maximum consistency
ROOT_TEMP   = 0.3   # coordinator needs some flexibility to handle varied requests

VALID_FREQUENCIES = ["daily", "weekly", "custom"]


def _error(message: str) -> dict:
    """Return a structured error dict for tool responses."""
    return {"status": "error", "message": message}


def _ok(**kwargs) -> dict:
    """Return a structured success dict for tool responses."""
    return {"status": "success"} | kwargs

def add_habit(name: str, frequency: str,goal: str, tool_context: ToolContext) -> dict:
    """Add a new habit to the user's habit tracker.

    Args:
        name: Display name of the habit (e.g., 'Exercise'). Must not be empty.
        frequency: How often the habit should be performed. Must be one of: 'daily', 'weekly', 'custom'.
        start_date: The date the habit starts in YYYY-MM-DD format (e.g., '2026-05-25').
        goal: A short description of the desired outcome (e.g., '30 min cardio'). Must not be empty.

    Returns:
        On success: {"status": "success", "habit_id": str, "name": str}
        On failure: {"status": "error", "message": str}
    """
    if not name or not name.strip():
        return _error("Habit name cannot be empty.")
    if not goal or not goal.strip():
        return _error("Goal cannot be empty.")
    if frequency not in VALID_FREQUENCIES:
        return _error(f"Invalid frequency '{frequency}'. Valid options are: {', '.join(VALID_FREQUENCIES)}.")

    habits = tool_context.state.get("habits", {})

    existing_ids = set(habits.keys())
    base_id = f"{name.strip()[:3].upper()}-{datetime.date.today().strftime('%Y%m%d')}"
    habit_id = base_id
    counter = 1

    while habit_id in existing_ids:
        habit_id = f"{base_id}-{counter}"
        counter += 1

    start_date = datetime.date.today().isoformat()

    new_habit = {
        "id": habit_id,
        "name": name.strip(),
        "frequency": frequency,
        "start_date": start_date,
        "goal": goal.strip(),
        "checkins": [],
        "created_at": datetime.datetime.now().isoformat()
    }
    habits[habit_id] = new_habit
    tool_context.state["habits"] = habits
    tool_context.state["habit_count"] = tool_context.state.get("habit_count", 0) + 1
    streak_counts = tool_context.state.get("streak_counts", {})
    streak_counts[habit_id] = 0
    tool_context.state["streak_counts"] = streak_counts
    return _ok(habit_id=habit_id, name=new_habit["name"])

