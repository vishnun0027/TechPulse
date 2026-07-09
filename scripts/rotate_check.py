#!/usr/bin/env python3
import subprocess
import sys
import re

# Regex patterns for common secrets
PATTERNS = {
    "Groq API Key": re.compile(r"gsk_[a-zA-Z0-9]{50,}"),
    "Supabase Key / JWT": re.compile(r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9-_]+\.[a-zA-Z0-9-_]+"),
    "Postgres Database URL": re.compile(r"postgres(?:ql)?://[^:]+:[^@]+@[^/]+/.*"),
    "Slack Webhook URL": re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/[B-Z0-9]+/[A-Za-z0-9]+"),
    "Discord Webhook URL": re.compile(r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9-_]+"),
    "Generic Secret / API Key": re.compile(r"(?:api_key|apikey|secret|passwd|password)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{16,}['\"]", re.IGNORECASE)
}

def get_staged_files():
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, check=True
        )
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"Error getting staged files: {e}")
        return []

def get_file_content(filepath):
    try:
        # Get the staged content, not the working tree content
        res = subprocess.run(
            ["git", "show", f":{filepath}"],
            capture_output=True, text=True, check=True
        )
        return res.stdout
    except Exception:
        # Fallback to direct file read if git show fails
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

def main():
    staged_files = get_staged_files()
    if not staged_files:
        return 0

    violations = []

    for filepath in staged_files:
        # Ignore this script and lock/binary files
        if filepath == "scripts/rotate_check.py" or filepath.endswith((".lock", ".png", ".jpg", ".webp", ".pdf", ".parquet")):
            continue

        content = get_file_content(filepath)
        if not content:
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    violations.append(
                        f"❌ Violation: Found {name} in {filepath} on line {line_num}"
                    )

    if violations:
        print("\n".join(violations))
        print("\n🚨 Commit blocked! Please remove the exposed secrets before committing.")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
