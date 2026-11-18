"""ai_commit: write a git commit message for your staged changes with Groq.

Run it inside a git repository after `git add`. It is set up as the git alias `git aic`.
"""

import subprocess
import sys

import groq_api

MAX_DIFF_CHARS = 15000  # longer diffs are cut so the request stays within Groq's free limits
PROMPT = (
    "You write git commit messages. The user sends a staged diff. Reply with only the commit message: "
    "a summary line in the imperative mood of at most 72 characters, then, if the change needs it, "
    "a blank line and a few short bullet points. No code fences or quotes."
)


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            check=False)
    if result.returncode != 0:
        sys.exit(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def main():
    if not groq_api.api_key():
        sys.exit("Set the GROQ_API_KEY environment variable first.")
    diff = git("diff", "--staged")
    if not diff.strip():
        sys.exit("Nothing is staged. Run git add first.")
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n[diff cut off here]"
    message = groq_api.chat(PROMPT, git("diff", "--staged", "--stat") + "\n" + diff)
    print(f"\n{message}\n")
    choice = input("Commit with this message? [Y]es / [e]dit / [n]o: ").strip().lower()
    if choice in ("", "y", "yes"):
        return subprocess.run(["git", "commit", "-m", message], check=False).returncode
    if choice in ("e", "edit"):
        return subprocess.run(["git", "commit", "-e", "-m", message], check=False).returncode
    print("Cancelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
