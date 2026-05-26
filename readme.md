# Habit Tracker Agent

A conversational ADK agent that helps you build and maintain daily habits.

## What it does

- Add habits with a name, frequency, and goal
- Log daily check-ins
- View all habits with current streak data
- Edit or delete habits
- Get AI-powered habit suggestions via web search

## Project structure

```
HabitTrackerAgent/
├── agent/
│   ├── __init__.py       # ADK entry point — exposes root_agent
│   └── agent.py          # all tools, search_agent, and root_agent
├── __init__.py           # empty (package marker)
├── .env                  # GOOGLE_API_KEY (never commit)
├── .gitignore
└── readme.md
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install google-adk
cp .env.example .env            # add your GOOGLE_API_KEY
```

## Run

```bash
adk web
```

Then open `http://localhost:8000` in your browser.

## Example prompts

```
Add a new habit: Exercise, daily, goal is 30 min cardio
Log a check-in for Exercise today
Show all my habits
What's my current streak for Exercise?
Suggest a new mindfulness habit
Delete my Exercise habit
```

## Key concepts practiced

- Session state and ToolContext
- validate-before-write pattern
- Append-only state (checkins list)
- State invariants (streak_counts)
- AgentTool pattern for google_search isolation
- Instruction engineering and routing rules
