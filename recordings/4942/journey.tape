# OMNI-3765 / #4942 — Security Scan OSV step's export fails on declared
# extras conflicts. Replays the exact command the workflow runs.
Output recordings/4942/before-osv-export.mp4
Set FontSize 15
Set Width 1400
Set Height 750
Set Shell bash
Set TypingSpeed 30ms

Type "# Security Scan's 'OSV advisory scan (uv.lock)' step runs this on every PR that changes uv.lock:"
Enter
Sleep 500ms
Type "uv export --frozen --format requirements-txt --all-extras > /tmp/uv-req-full.txt"
Enter
Wait+Screen /incompatible with the declared/
Type "echo exit=$?"
Enter
Wait+Screen /exit=2/
Sleep 3s
