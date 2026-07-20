"""Interactive lightcurves for the Pipeline Console science page.

This is a tracer for folding HoloViz/Panel into the FastAPI console (ADR #50
territory: science exploration, not ops control). It reuses the same stacked
forced-photometry table the SVG lightcurve already uses, but renders an
interactive Bokeh figure (hover, zoom, pan) via HoloViews + Panel.

Enable on the console::

    pip install 'dsa110-continuum[panel]'
    DSA110_DASH_PANEL=1 python -m uvicorn scripts.dashboard_server:app --port 8766

Then open ``/science`` — the right-hand lightcurve pane becomes an iframe of
``/panel/lightcurves``. Without the extra deps or env flag the console keeps
the existing static SVG path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Match Pipeline Console science-page tokens (scripts/dashboard_server.py).
_BG = "#0b0d10"
_SURFACE = "#0f1216"
_CARD = "#12161c"
_LINE = "#1d222b"
_TX = "#e7ebf3"
_MUT = "#8b95a7"
_ACC = "#6aa7ff"

_DARK_CSS = f"""
:root {{
  color-scheme: dark;
}}
html, body, .pn-root, #pn-body, .bk-root {{
  background: {_BG} !important;
  color: {_TX} !important;
  margin: 0;
}}
/* Panel / Material widgets (Select, etc.) */
.pn-wrapper, .pn-col, .pn-row,
.mdc-select, .mdc-text-field, .bk-input-group {{
  background: {_BG} !important;
  color: {_TX} !important;
}}
.mdc-select__anchor,
.mdc-select__selected-text,
.mdc-floating-label,
.mdc-list-item__text,
label {{
  color: {_TX} !important;
}}
.mdc-select__dropdown-icon {{
  fill: {_MUT} !important;
}}
.mdc-menu-surface, .mdc-list {{
  background: {_CARD} !important;
  color: {_TX} !important;
}}
select, .bk-input, input, button {{
  background: {_SURFACE} !important;
  color: {_TX} !important;
  border: 1px solid {_LINE} !important;
}}
/* Bokeh canvas chrome + toolbar icons on dark */
.bk-canvas-wrapper, .bk-plot-wrapper {{
  background: {_BG} !important;
}}
.bk-root .bk-toolbar .bk-btn,
.bk-root .bk-toolbar .bk-tool-icon {{
  filter: invert(1) brightness(1.15);
  opacity: 0.85;
}}
.bk-root .bk-toolbar .bk-btn:hover,
.bk-root .bk-toolbar .bk-tool-icon:hover {{
  opacity: 1;
}}
"""

_SELECT_CSS = f"""
:host {{
  --panel-surface-color: {_CARD};
  --panel-on-surface-color: {_TX};
  --panel-primary-color: {_ACC};
  color: {_TX};
  background: transparent;
}}
label, .bk-input-group label, .mdc-floating-label {{
  color: {_MUT} !important;
}}
select, .bk-input, .mdc-select__selected-text {{
  background: {_SURFACE} !important;
  color: {_TX} !important;
  border-color: {_LINE} !important;
}}
"""


def _dark_bokeh_hook(plot, element) -> None:  # noqa: ARG001 — hv hook signature
    """Paint axis/frame/toolbar chrome to match the console dark surface."""
    p = plot.state.plot
    p.background_fill_color = _SURFACE
    p.border_fill_color = _BG
    p.outline_line_color = _LINE
    for ax in (p.xaxis, p.yaxis):
        ax.axis_label_text_color = _TX
        ax.major_label_text_color = _MUT
        ax.axis_line_color = _LINE
        ax.major_tick_line_color = _LINE
        ax.minor_tick_line_color = _LINE
    p.xgrid.grid_line_color = _LINE
    p.ygrid.grid_line_color = _LINE
    p.title.text_color = _TX


def build_lightcurve_app(
    get_table: Callable[[], dict[str, Any]],
) -> Callable[[], Any]:
    """Return a zero-arg Panel factory suitable for ``add_applications``.

    Parameters
    ----------
    get_table
        Callable returning the dict shape of
        ``scripts.dashboard_server._variability_table`` (keys ``sources``,
        each with ``source_id``, ``epochs``, ``flux_jy``, ``flux_err_jy``,
        ``mean_flux_jy``, ``v``, ``eta``, ``n_epochs``).
    """

    def _app():
        import holoviews as hv
        import pandas as pd
        import panel as pn

        hv.extension("bokeh", logo=False)
        pn.extension(sizing_mode="stretch_width", theme="dark")
        # Full-page black so the iframe doesn't flash a white Panel shell.
        if _DARK_CSS not in pn.config.raw_css:
            pn.config.raw_css.append(_DARK_CSS)

        table = get_table() or {}
        sources = list(table.get("sources") or [])
        if not sources:
            return pn.pane.Markdown(
                "Need ≥2 photometry epochs for variability.",
                styles={"color": _MUT, "font-size": "13px", "background": _BG},
            )

        by_id = {str(s["source_id"]): s for s in sources}
        options = list(by_id)
        default = options[0]
        loc = getattr(pn.state, "location", None)
        if loc is not None:
            qp = getattr(loc, "query_params", None) or {}
            candidate = qp.get("source")
            if isinstance(candidate, list):
                candidate = candidate[0] if candidate else None
            if candidate in by_id:
                default = str(candidate)

        select = pn.widgets.Select(
            name="Source",
            options=options,
            value=default,
            stylesheets=[_SELECT_CSS],
        )

        def _plot(sid: str):
            row = by_id.get(sid)
            if row is None:
                return pn.pane.Markdown(
                    f"Unknown source `{sid}`.",
                    styles={"color": _MUT, "background": _BG},
                )
            df = pd.DataFrame(
                {
                    "epoch": row["epochs"],
                    "flux_jy": row["flux_jy"],
                    "flux_err_jy": row["flux_err_jy"],
                }
            )
            df["x"] = range(len(df))
            # Keep the overlay minimal: Curve + ErrorBars + Scatter. Fancy
            # hover_tooltips / xtick label tuples have tripped Dimension KeyErrors
            # under Panel's reactive path even when hv.render() succeeds offline.
            # Keep the figure short enough that chrome (Select + meta) + plot
            # fit inside the science-page iframe (~360px) without scrolling.
            plot_opts = dict(
                color=_ACC,
                tools=["hover", "box_zoom", "reset", "pan", "wheel_zoom"],
                active_tools=["wheel_zoom"],
                xlabel="epoch index",
                ylabel="Jy",
                responsive=True,
                height=210,
                show_grid=True,
                bgcolor=_SURFACE,
                hooks=[_dark_bokeh_hook],
            )
            curve = hv.Curve(df, kdims=["x"], vdims=["flux_jy"]).opts(
                **plot_opts, line_width=1.5
            )
            errs = hv.ErrorBars(
                df, kdims=["x"], vdims=["flux_jy", "flux_err_jy"]
            ).opts(color=_ACC, alpha=0.55, hooks=[_dark_bokeh_hook])
            pts = hv.Scatter(df, kdims=["x"], vdims=["flux_jy"]).opts(
                color=_ACC,
                size=7,
                tools=["hover"],
                hooks=[_dark_bokeh_hook],
            )
            overlay = (curve * errs * pts).opts(hooks=[_dark_bokeh_hook])
            meta = (
                f"**{row['source_id']}** · {row['n_epochs']} epochs · "
                f"⟨S⟩ {row['mean_flux_jy']} Jy · V {row['v']} · η {row['eta']}"
            )
            return pn.Column(
                pn.pane.Markdown(
                    meta,
                    margin=(0, 0, 4, 0),
                    styles={
                        "color": _MUT,
                        "font-size": "11px",
                        "line-height": "1.3",
                        "background": _BG,
                    },
                ),
                pn.pane.HoloViews(
                    overlay,
                    linked_axes=False,
                    sizing_mode="stretch_width",
                    height=210,
                    margin=0,
                    styles={"background": _BG},
                ),
                styles={"background": _BG},
                margin=0,
            )

        # No in-iframe title — /science already shows "Panel · HoloViews".
        select.margin = (0, 0, 4, 0)
        return pn.Column(
            select,
            pn.bind(_plot, select),
            styles={"background": _BG, "padding": "4px 6px", "color": _TX},
            margin=0,
            stylesheets=[
                f"""
                :host {{ background: {_BG} !important; color: {_TX}; }}
                """
            ],
        )

    return _app


def try_mount_panel(fastapi_app: Any, get_table: Callable[[], dict[str, Any]]) -> bool:
    """Mount ``/panel/lightcurves`` when Panel + bokeh-fastapi are importable.

    Returns True if the mount succeeded.
    """
    try:
        from panel.io.fastapi import add_applications
    except ImportError:
        return False

    add_applications(
        {"/panel/lightcurves": build_lightcurve_app(get_table)},
        app=fastapi_app,
        title={"/panel/lightcurves": "DSA-110 lightcurves"},
        location=True,
    )
    return True
