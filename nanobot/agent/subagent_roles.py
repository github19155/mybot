"""Subagent roles and trusted built-in tool permissions."""

SUBAGENT_ROLES: dict[str, dict[str, str]] = {
    "researcher": {
        "description": "Find and compare evidence; cite sources and distinguish facts from inference.",
        "permissions": "read-only",
    },
    "planner": {
        "description": "Inspect the task and propose a concrete plan, dependencies, and acceptance checks.",
        "permissions": "read-only",
    },
    "coder": {
        "description": "Implement the assigned changes using existing project conventions.",
        "permissions": "read-write-exec",
    },
    "debugger": {
        "description": "Reproduce the failure, find its root cause, and apply a focused fix.",
        "permissions": "read-write-exec",
    },
    "tester": {
        "description": "Exercise the assigned behavior and report reproducible failures and evidence.",
        "permissions": "read-write-exec",
    },
    "writer": {
        "description": "Read source material and write clear, accurate documentation or prose.",
        "permissions": "read-write",
    },
    "analyst": {
        "description": "Analyze code or data, check assumptions, and report evidence-backed conclusions.",
        "permissions": "read-write-exec",
    },
}

_READ_TOOLS = {
    "read_file": "filesystem",
    "list_dir": "filesystem",
    "find_files": "search",
    "grep": "search",
    "web_search": "web",
    "web_fetch": "web",
}
_WRITE_TOOLS = {
    **_READ_TOOLS,
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "apply_patch": "apply_patch",
}
_EXEC_TOOLS = {
    **_WRITE_TOOLS,
    "exec": "shell",
    "exec_session": "exec_session",
    "list_exec_sessions": "exec_session",
    "run_cli_app": "cli_apps",
}
ROLE_TOOL_MODULES = {
    "read-only": _READ_TOOLS,
    "read-write": _WRITE_TOOLS,
    "read-write-exec": _EXEC_TOOLS,
}
