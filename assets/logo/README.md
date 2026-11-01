# Installer logo hook

`installer.py` displays an optional terminal banner before installation prompts.
The banner is intentionally not bundled yet.

To add one later, create `codex-wechat.txt` in this directory. The installer
shows it only in an interactive terminal when every line fits the terminal
width. Keep the file UTF-8 and avoid terminal control sequences.
