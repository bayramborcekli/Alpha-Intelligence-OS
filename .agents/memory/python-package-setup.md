---
name: Python package setup
description: Replit-specific guidance for installing Python dependencies in this project.
---

Use a Replit Python tools module (currently Python 3.11) before installing project packages when the base Python interpreter reports that pip is unavailable or the environment is externally managed.

**Why:** The imported environment exposed a base Python without pip, while the supported Python tools module provided pip and installed the pinned dependencies into the project package path.

**How to apply:** Check available Python modules first; prefer the project’s supported Python tools module over creating a virtual environment or bypassing the externally managed environment.