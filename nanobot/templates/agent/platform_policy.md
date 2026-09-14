{% if system == 'Windows' %}
## Platform Context (Windows)
- Workers execute on Windows for this session. When delegating shell or file work, do not assume GNU utilities such as `grep`, `sed`, or `awk` exist.
- Prefer a Worker with Windows-compatible capabilities and include any platform-sensitive constraint in the delegated task.
{% else %}
## Platform Context (POSIX)
- Workers execute on a POSIX system for this session. Preserve platform-sensitive constraints when delegating shell, file, build, or test work.
{% endif %}
