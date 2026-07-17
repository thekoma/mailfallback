"""Single source of truth for the app version.

Bumped ONLY by the release PR (release-pr.yml). Kept outside pyproject.toml
because PEP 440 would normalize CalVer "2026.07.0" to "2026.7.0".
"""

__version__ = "2026.07.1"
