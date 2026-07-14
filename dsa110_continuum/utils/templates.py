"""Template loading utilities for DSA-110."""

from pathlib import Path


def _get_shared_css() -> str:
    """Load the shared CSS from the templates directory."""
    current_dir = Path(__file__).resolve().parent
    css_path = current_dir.parent / "templates" / "shared_styles.css"
    if css_path.exists():
        return css_path.read_text()
    return ""


def render_template(template_name: str, **kwargs) -> str:
    """
    Load and return an HTML template from the templates directory.

    The shared_css variable is automatically injected into all templates
    that include {shared_css} placeholder.

    The templates directory is searched in the following order:
    1. dsa110_continuum/templates/
    2. dsa110_continuum/api/templates/
    """
    # 1. Check relative to this file
    # Structure: .../utils/templates.py -> .../templates/
    current_dir = Path(__file__).resolve().parent
    template_path = current_dir.parent / "templates" / template_name

    if not template_path.exists():
        # 2. Check in api/templates
        template_path = current_dir.parent / "api" / "templates" / template_name

    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")

    with open(template_path) as f:
        template_content = f.read()

    # Automatically inject shared CSS if the template uses it
    if "shared_css" not in kwargs and "{shared_css}" in template_content:
        kwargs["shared_css"] = _get_shared_css()

    try:
        return template_content.format(**kwargs)
    except (KeyError, ValueError) as e:
        # If formatting fails, it's likely due to unescaped braces or missing keys
        raise ValueError(f"Failed to format template '{template_name}': {e}")
