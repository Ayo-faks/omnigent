# Security Scan OSV step after the fix: the two compatible-resolution
# exports the workflow now runs both succeed on the unmodified lockfile.
Output recordings/4942/after-osv-export.mp4
Set FontSize 15
Set Width 1400
Set Height 750
Set Shell bash
Set TypingSpeed 30ms

Type "# Security Scan's 'OSV advisory scan (uv.lock)' step now exports each compatible resolution set:"
Enter
Sleep 500ms
Type "uv export --frozen --format requirements-txt --all-extras --no-extra antigravity --all-groups > /tmp/uv-req-full.txt; echo exit=$?"
Enter
Wait+Screen /exit=0/
Sleep 500ms
Type "uv export --frozen --format requirements-txt --extra antigravity --extra all --no-default-groups > /tmp/uv-req-antigravity.txt; echo exit=$?"
Enter
Wait+Screen /exit=0/
Sleep 500ms
Type "# both forks resolve; pip-audit now runs on every pinned package (sentinels from each fork):"
Enter
Type "grep -h -c '==' /tmp/uv-req-full.txt /tmp/uv-req-antigravity.txt; grep -ho 'cwsandbox==[0-9.]*' /tmp/uv-req-full.txt; grep -ho 'google-antigravity==[0-9.]*' /tmp/uv-req-antigravity.txt"
Enter
Wait+Screen /google-antigravity==/
Sleep 3s
