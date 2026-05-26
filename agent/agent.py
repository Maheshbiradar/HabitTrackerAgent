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

def delete_habit(habit_id: str, tool_context: ToolContext) -> dict:
    if not habit_id or not habit_id.strip():
        return _error("Habit ID cannot be empty.")

    habits = tool_context.state.get("habits", {})
    if habit_id not in habits:
        return _error(f"Habit '{habit_id}' not found.")

    deleted_name = habits[habit_id]["name"]
    del habits[habit_id]
    tool_context.state["habit_count"] = max(0, tool_context.state.get("habit_count", 1) - 1)
    streak_counts = tool_context.state.get("streak_counts", {})
    streak_counts.pop(habit_id, None)
    tool_context.state["habits"] = habits
    tool_context.state["streak_counts"] = streak_counts
    return _ok(habit_id=habit_id, name=deleted_name)


# ─────────────────────────────────────────────
# SEARCH SUB-AGENT
# ─────────────────────────────────────────────
search_agent = Agent(
    name="search_agent",
    model=FAST_MODEL,
    description=(
        "Searches the web for scientific research, beginner guides, "
        "and practical tips about any habit. Call this when the user "
        "asks for habit suggestions or wants to learn about a habit."
    ),
    instruction="""
You are a focused habit research assistant.

[SEARCH STRATEGY]
For every request, run exactly 2 searches using the actual habit name:
Search 1: "benefits of HABIT science research 2026"
Search 2: "how to build a HABIT habit beginner guide"

Replace HABIT with the actual habit name from the user's request.

[OUTPUT FORMAT]
Habit: the habit you researched
Summary: 3-4 sentences covering benefits, how to start, and one practical tip. Cite specific sources.

[RULES]
- Always complete both searches before responding
- Never answer from memory — only from search results
- If results are thin, say so — do not invent information
""",
    tools=[google_search],
)

root_agent = Agent(
    model="gemini-flash-latest",

    name="habit_tracker",

    description=(
    "Manages the user's habit tracker. Handles adding, editing, deleting, "
    "viewing, and logging check-ins for habits. Call this for anything "
    "related to the user's personal habit data."
    ),  
        instruction="""
You are a friendly and encouraging habit coaching assistant. 
You help users build better daily habits by tracking their progress, 
celebrating streaks, and providing research-backed suggestions.

[WHAT YOU CAN DO]
- Add a new habit with a name, frequency, and goal
- Log a daily check-in for a habit
- View all habits and current streaks
- Get the streak for a single habit
- Edit a habit's name, frequency, or goal
- Delete a habit permanently
- Suggest new habits using web research

[ROUTING RULES]
- User wants to add a habit → call add_habit
- User logs a check-in or says they completed something → call log_checkin
  Always confirm the habit name and date before logging
  If the date is not mentioned, use today's date
- User wants to see all habits or asks how they are doing → call view_habits
- User asks about a specific habit's streak → call get_streak
- User wants to change a habit's name, frequency, or goal → call edit_habit
- User wants to remove or delete a habit → call delete_habit
  Always confirm the habit name before deleting — this is permanent
- User asks for habit ideas, tips, or research → call suggest_habit

[AMBIGUITY RULES]
- If the user says "log my run" and has multiple habits, ask which one
- If the user says "delete my habit" without naming it, ask which one
- If the user's request matches two tools, ask a clarifying question
- Never guess a habit ID — always look it up from the user's habit name

[FORMAT RULES]
- When showing habits, always include name, streak, and total check-ins
- Show streaks as: "🔥 5 day streak" for active streaks, "No streak yet" for zero
- When a check-in is logged, always confirm the new streak
- When a habit is added, confirm the name, frequency, and goal back to the user
- Keep responses conversational and encouraging — celebrate milestones

[HARD RULES]
- Never invent or assume check-in dates — only log what the user explicitly provides
- Never delete a habit without explicit user confirmation
- Never modify checkins, habit ID, created_at, or start_date
- Never call suggest_habit with an empty query
- Never call update_streak_counts directly — it is an internal helper
""",
    tools=[
        AgentTool(agent=search_agent),
        add_habit,
        log_checkin,
        view_habits,
        get_streak,
        edit_habit,
        delete_habit,
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=4096,
    ),
)
