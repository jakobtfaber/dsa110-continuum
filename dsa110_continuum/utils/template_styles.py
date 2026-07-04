# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Shared CSS styles for HTML report templates.

This module provides a unified light theme for all HTML reports generated
by the DSA-110 continuum imaging pipeline. Templates can include the shared
CSS by calling get_shared_css() and inserting it into their <style> block.

Usage:
    from dsa110_continuum.utils.template_styles import get_shared_css

    html = f'''
    <html>
    <head>
        <style>
        {get_shared_css()}
        /* Template-specific overrides here */
        </style>
    </head>
    ...
    '''
"""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_shared_css() -> str:
    """Load and return the shared CSS styles for HTML reports.

    Returns
    -------
        str
        The contents of shared_styles.css

    Notes
    -----
        The result is cached for performance.
    """
    css_path = Path(__file__).parent.parent / "templates" / "shared_styles.css"
    return css_path.read_text()


def get_css_variables_only() -> str:
    """Return only the CSS variables (:root) block for templates that need
        just the color scheme without the full component styles.

    Returns
    -------
        str
    CSS :root block with all DSA theme variables
    """
    return """:root {
    /* Primary colors */
    --dsa-primary: #2c3e50;
    --dsa-primary-light: #34495e;
    --dsa-secondary: #3498db;
    --dsa-secondary-light: #5dade2;

    /* Background colors */
    --dsa-bg-default: #ffffff;
    --dsa-bg-paper: #f8f9fa;
    --dsa-bg-surface: #f5f5f5;
    --dsa-bg-code: #f4f4f4;

    /* Border colors */
    --dsa-border: #dee2e6;
    --dsa-border-light: #e9ecef;

    /* Text colors */
    --dsa-text-primary: #333333;
    --dsa-text-secondary: #666666;
    --dsa-text-muted: #7f8c8d;
    --dsa-text-disabled: #999999;

    /* Status colors */
    --dsa-success: #28a745;
    --dsa-success-bg: #d4edda;
    --dsa-success-border: #c3e6cb;
    --dsa-warning: #ffc107;
    --dsa-warning-bg: #fff3cd;
    --dsa-warning-border: #ffeeba;
    --dsa-error: #dc3545;
    --dsa-error-bg: #f8d7da;
    --dsa-error-border: #f5c6cb;
    --dsa-info: #17a2b8;
    --dsa-info-bg: #d1ecf1;
    --dsa-info-border: #bee5eb;

    /* Shadows */
    --dsa-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
    --dsa-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    --dsa-shadow-lg: 0 4px 8px rgba(0, 0, 0, 0.15);

    /* Spacing */
    --dsa-spacing-xs: 4px;
    --dsa-spacing-sm: 8px;
    --dsa-spacing-md: 16px;
    --dsa-spacing-lg: 24px;
    --dsa-spacing-xl: 32px;

    /* Border radius */
    --dsa-radius-sm: 4px;
    --dsa-radius: 6px;
    --dsa-radius-lg: 8px;
    --dsa-radius-xl: 12px;

    /* Typography */
    --dsa-font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    --dsa-font-mono: 'Roboto Mono', ui-monospace, 'SF Mono', Monaco, 'Cascadia Code', monospace;
    --dsa-line-height: 1.6;
}"""


# Mapping of legacy variable names to new DSA theme variables
# Use this to gradually migrate templates
VARIABLE_MAPPING = {
    # bandpass_report.html legacy variables
    "--primary-color": "var(--dsa-primary)",
    "--secondary-color": "var(--dsa-secondary)",
    "--success-color": "var(--dsa-success)",
    "--warning-color": "var(--dsa-warning)",
    "--danger-color": "var(--dsa-error)",
    "--light-bg": "var(--dsa-bg-paper)",
    "--border-color": "var(--dsa-border)",
    # fits_viewer legacy variables
    "--color-bg-default": "var(--dsa-bg-default)",
    "--color-bg-subtle": "var(--dsa-bg-paper)",
    "--color-text-primary": "var(--dsa-text-primary)",
    "--color-text-secondary": "var(--dsa-text-secondary)",
    "--color-border": "var(--dsa-border)",
}
