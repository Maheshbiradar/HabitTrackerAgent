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

def _update_streak_counts(tool_context: ToolContext) -> None:
    """Recalculate streaks for all habits and write streak_counts back to state."""
    habits = tool_context.state.get("habits", {})
    streak_counts = {}
    for habit_id, habit in habits.items():
        checkins = habit["checkins"]
        dates = sorted(
            {datetime.date.fromisoformat(c) for c in checkins},
            reverse=True
        )
        dates_set = set(dates)
        streak = 0
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        if dates and dates[0] >= yesterday:
            current = today if today in dates_set else yesterday
            while current in dates_set:
                streak += 1
                current -= datetime.timedelta(days=1)
        streak_counts[habit_id] = streak
    tool_context.state["streak_counts"] = streak_counts
    

def _ok(**kwargs) -> dict:
    """Return a structured success dict for tool responses."""
    return {"status": "success"} | kwargs

def add_habit(name: str, frequency: str,goal: str, tool_context: ToolContext) -> dict:
    """Add a new habit to the user's habit tracker.

    Args:
        name: Display name of the habit (e.g., 'Exercise'). Must not be empty.
        frequency: How often the habit should be performed. Must be one of: 'daily', 'weekly', 'custom'.
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
        "habit_id": habit_id,
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

def log_checkin(habit_id: str, date: str, tool_context: ToolContext) -> dict:
    """Record a completed check-in for a habit on a given date.

    Args:
        habit_id: The unique ID of the habit to check in (e.g., 'EXE-20260525').
        date: The date of the check-in in YYYY-MM-DD format (e.g., '2026-05-25').

    Returns:
        On success: {"status": "success", "habit_id": str, "date": str, "current_streak": int}
        On failure: {"status": "error", "message": str}
    """
    if not habit_id or not habit_id.strip():
        return _error("Habit ID cannot be empty.")
    if not date or not date.strip():
        return _error("Date cannot be empty.")
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return _error("Date must be in YYYY-MM-DD format.")

    habits = tool_context.state.get("habits", {})

    if habit_id not in habits:
        return _error(f"Habit '{habit_id}' not found.")

    habit = habits[habit_id]
    if date in habit["checkins"]:
        return _error(f"Check-in for '{date}' already exists.")

    habit["checkins"].append(date)
    habit["last_updated"] = datetime.datetime.now().isoformat()
    tool_context.state["habits"] = habits
    _update_streak_counts(tool_context)
    current_streak = tool_context.state.get("streak_counts", {}).get(habit_id, 0)
    return _ok(habit_id=habit_id, date=date, current_streak=current_streak)    

def view_habits(tool_context: ToolContext) -> dict:
    """Return a summary of all tracked habits, including streaks and check-in counts.

    Use this tool when the user asks to see their habits, list their habits,
    or wants an overview of their progress.

    Returns:
        On success (habits exist): {"status": "success", "habits": list[dict], "count": int}
            Each habit dict contains: habit_id, name, frequency, goal, start_date,
            current_streak (int), total_checkins (int), last_updated (str | None).
        On success (no habits): {"status": "success", "message": str, "habits": []}
    """
    habits = tool_context.state.get("habits", {})
    streak_counts = tool_context.state.get("streak_counts", {})
    if not habits:
        return _ok(message="You have no habits yet. Add one to get started!", habits=[])

    habit_list = [
        {
            "habit_id":       habit["habit_id"],
            "name":           habit["name"],
            "frequency":      habit["frequency"],
            "goal":           habit["goal"],
            "start_date":     habit["start_date"],
            "current_streak": streak_counts.get(habit_id, 0),
            "total_checkins": len(habit["checkins"]),
            "last_updated":   habit.get("last_updated"),
        }
        for habit_id, habit in habits.items()
    ]
    return _ok(habits=habit_list, count=len(habit_list))

def get_streak(habit_id: str, tool_context: ToolContext) -> dict:
    """Get the current streak and total check-in count for a specific habit.

    Use this tool when the user asks about their streak, how many days in a row
    they have completed a habit, or wants a progress update for a single habit.

    Args:
        habit_id: The unique ID of the habit to look up (e.g., 'EXE-20260525').

    Returns:
        On success: {"status": "success", "habit_id": str, "name": str,
                     "current_streak": int, "total_checkins": int}
        On failure: {"status": "error", "message": str}
    """
    if not habit_id or not habit_id.strip():
        return _error("Habit ID cannot be empty.")

    habits = tool_context.state.get("habits", {})
    if habit_id not in habits:
        return _error(f"Habit '{habit_id}' not found.")

    streak_counts = tool_context.state.get("streak_counts", {})
    current_streak = streak_counts.get(habit_id, 0)

    habit = habits[habit_id]
    return _ok(
        habit_id=habit_id,
        name=habit["name"],
        current_streak=current_streak,
        total_checkins=len(habit["checkins"]),
    )

def edit_habit(habit_id: str, name: str | None = None, frequency: str | None = None, goal: str | None = None, tool_context: ToolContext = None) -> dict:
    """Update one or more fields of an existing habit.

    Use this tool when the user wants to rename a habit, change its frequency,
    or update its goal. Only the fields you provide will be changed — omitted
    fields are left as-is.

    Args:
        habit_id: The unique ID of the habit to update (e.g., 'EXE-20260525').
        name: New display name for the habit. Must not be empty if provided.
        frequency: New frequency. Must be one of: 'daily', 'weekly', 'custom' if provided.
        goal: New goal description. Must not be empty if provided.

    Returns:
        On success: {"status": "success", "habit_id": str, "name": str}
        On failure: {"status": "error", "message": str}
    """
    if not habit_id or not habit_id.strip():
        return _error("Habit ID cannot be empty.")

    habits = tool_context.state.get("habits", {})
    if habit_id not in habits:
        return _error(f"Habit '{habit_id}' not found.")

    if frequency is not None and frequency not in VALID_FREQUENCIES:
        return _error(f"Invalid frequency '{frequency}'. Valid options are: {', '.join(VALID_FREQUENCIES)}.")
    if name is not None and not name.strip():
        return _error("Habit name cannot be empty.")
    if goal is not None and not goal.strip():
        return _error("Goal cannot be empty.")
    if name is None and frequency is None and goal is None:
        return _error("Nothing to update. Provide at least one of: name, frequency, or goal.")

    habit = habits[habit_id]
    if name is not None:
        habit["name"] = name.strip()
    if frequency is not None:
        habit["frequency"] = frequency
    if goal is not None:
        habit["goal"] = goal.strip()
    habit["last_updated"] = datetime.datetime.now().isoformat()
    tool_context.state["habits"] = habits
    return _ok(habit_id=habit_id, name=habit["name"])