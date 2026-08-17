"""
Thesis plot utilities.

Import and initialize at the top of every analysis notebook:

    import sys
    sys.path.append('../scenario_analysis')
    from plot_utils import apply_thesis_style, thesis_subplots, save_fig
    apply_thesis_style()

All conventions are documented in ../graph_style_guide.md.
"""

from pathlib import Path
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# --------------------------------------------------------------------------- #
# Colors                                                                       #
# --------------------------------------------------------------------------- #

CARRIER_COLORS = {
    'gas':      '#e74c3c',
    'coal':     '#7f8c8d',
    'lignite':  '#6d4c41',
    'oil':      '#e67e22',
    'hydrogen': '#f39c12',
    'other':    '#bdc3c7',
    'nuclear':  '#8e44ad',
    'hydro':    '#3498db',
    'onwind':   '#27ae60',
    'offwind':  '#1abc9c',
    'solar':    '#f1c40f',
}

SCALE_COLORS = {1: '#27ae60', 2: '#f39c12', 4: '#e74c3c', 8: '#8e44ad'}
SCALE_MARKERS = {1: 'o', 2: 's', 4: 'D', 8: '^'}

# --------------------------------------------------------------------------- #
# Naming                                                                       #
# --------------------------------------------------------------------------- #

SCALE_LABELS = {1: '1\u00d7', 2: '2\u00d7', 4: '4\u00d7', 8: '8\u00d7'}
DURATION_LABELS = {1: '1\u00d7', 2: '2\u00d7', 3: '3\u00d7', 4: '4\u00d7'}
CY_LABELS = {2003: 'CY2003', 2009: 'CY2009', 2012: 'CY2012'}
GAS_LABELS = {18: '18', 22.68: '22.68', 35: '35'}
CO2_LABELS = {80: '80', 113.4: '113.4', 140: '140'}

PARAM_AXIS_LABELS = {
    'bat_scale':    'bat_scale',
    'bat_duration': 'bat_duration',
    'gas_price':    'gas_price [EUR/MWh_th]',
    'co2_price':    'co2_price [EUR/tCO\u2082]',
    'climate_year': 'climate_year',
}


def combo_label(bat_scale, bat_duration):
    """Return a composite scale x duration label, e.g. '4\u00d73h'."""
    return f'{SCALE_LABELS[bat_scale]}{bat_duration}h'

POSITIVE = '#27ae60'
NEGATIVE = '#e74c3c'
NEUTRAL  = '#95a5a6'
IMPORT   = '#2ecc71'
EXPORT   = '#e74c3c'
REFLINE  = '#000000'

# --------------------------------------------------------------------------- #
# Figure sizes (width always 6.0 in = LaTeX \textwidth 150 mm)                #
# --------------------------------------------------------------------------- #

_FIGSIZES = {
    'single_short':  (6.0, 2.6),
    'single_tall':   (6.0, 3.3),
    'single_square': (6.0, 4.0),
    'horizontal_2':  (6.0, 2.4),
    'horizontal_3':  (6.0, 2.0),
    'stacked_2':     (6.0, 3.8),
    'stacked_3':     (6.0, 5.0),
    'grid_2x2':      (6.0, 3.8),
    'grid_3x2':      (6.0, 7.5),
    'grid_3x3':      (6.0, 5.2),
    'full_page':     (6.0, 6.5),
}


def figure_size(layout):
    """Return (width, height) in inches for a named layout."""
    if layout not in _FIGSIZES:
        raise ValueError(
            f"Unknown layout '{layout}'. Valid options: {sorted(_FIGSIZES)}"
        )
    return _FIGSIZES[layout]


# --------------------------------------------------------------------------- #
# Global style                                                                #
# --------------------------------------------------------------------------- #

def apply_thesis_style():
    """Apply uniform rcParams for all figures in the thesis."""
    mpl.rcParams.update({
        # Font family and sizes (tuned for \textwidth = 150 mm / 12 pt body)
        'font.family':        'DejaVu Sans',
        'font.size':          8,
        'axes.titlesize':     9,
        'axes.titleweight':   'normal',
        'axes.labelsize':     8,
        'xtick.labelsize':    7,
        'ytick.labelsize':    7,
        'legend.fontsize':    7,
        'figure.titlesize':   10,
        'figure.titleweight': 'normal',

        # Figure defaults
        'figure.dpi':         100,
        'savefig.dpi':        200,
        'savefig.bbox':       'tight',
        'savefig.pad_inches': 0.05,

        # Axes: clean look
        'axes.spines.top':    False,
        'axes.spines.right':  False,
        'axes.linewidth':     0.8,
        'axes.grid':          True,
        'axes.axisbelow':     True,

        # Grid: subtle
        'grid.color':         '#b0b0b0',
        'grid.alpha':         0.3,
        'grid.linewidth':     0.5,

        # Ticks
        'xtick.direction':    'out',
        'ytick.direction':    'out',
        'xtick.major.size':   3,
        'ytick.major.size':   3,
        'xtick.major.width':  0.8,
        'ytick.major.width':  0.8,

        # Legend
        'legend.frameon':     True,
        'legend.framealpha':  0.9,
        'legend.edgecolor':   '#b0b0b0',
        'legend.fancybox':    False,

        # Lines
        'lines.linewidth':    1.5,
        'lines.markersize':   5,

        # Patches (bars)
        'patch.linewidth':    0.5,
        'patch.edgecolor':    'white',
    })


# --------------------------------------------------------------------------- #
# Subplot helpers                                                             #
# --------------------------------------------------------------------------- #

def thesis_subplots(layout, nrows=1, ncols=1, figsize=None, **kwargs):
    """
    Wrapper around plt.subplots. `layout` selects a named figure size;
    pass `figsize=(w, h)` to override just for this plot when the named
    size doesn't fit. `nrows` and `ncols` control the grid (default 1x1).
    """
    if figsize is None:
        figsize = figure_size(layout)
    fig, ax = plt.subplots(nrows=nrows, ncols=ncols, figsize=figsize, **kwargs)
    return fig, ax


# --------------------------------------------------------------------------- #
# Export                                                                      #
# --------------------------------------------------------------------------- #

_DEFAULT_FIG_DIR = Path(__file__).resolve().parent.parent / 'tukedip_pdflatex_utf-8' / 'figures'


def save_fig(fig, name, fmt='jpg', out_dir=None):
    """
    Save figure as fig_{name}.{fmt} into the thesis figures directory.

    Returns the full path written.
    """
    out_dir = Path(out_dir) if out_dir else _DEFAULT_FIG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'fig_{name}.{fmt}'
    fig.savefig(path, dpi=200, bbox_inches='tight', pad_inches=0.05)
    print(f'Saved {path}')
    return path


# --------------------------------------------------------------------------- #
# Common helpers                                                              #
# --------------------------------------------------------------------------- #

def add_value_labels(ax, fmt='{:.1f}', orient='h', offset=3, fontsize=9):
    """
    Add numeric labels to bars. Use after ax.barh / ax.bar.

    orient='h' for horizontal bars (ax.barh), 'v' for vertical.
    offset is in points, away from the bar end.
    """
    for p in ax.patches:
        if orient == 'h':
            w = p.get_width()
            y = p.get_y() + p.get_height() / 2
            ax.annotate(fmt.format(w), (w, y),
                        xytext=(offset, 0), textcoords='offset points',
                        ha='left' if w >= 0 else 'right',
                        va='center', fontsize=fontsize)
        else:
            h = p.get_height()
            x = p.get_x() + p.get_width() / 2
            ax.annotate(fmt.format(h), (x, h),
                        xytext=(0, offset), textcoords='offset points',
                        ha='center',
                        va='bottom' if h >= 0 else 'top',
                        fontsize=fontsize)


def profit_cmap_sequential():
    """White -> green ramp for non-negative profit heatmaps."""
    return LinearSegmentedColormap.from_list(
        'profit_seq', ['white', '#27ae60', '#1a7a3a']
    )


def profit_cmap_diverging():
    """Red-yellow-green diverging, centered at 0."""
    return plt.get_cmap('RdYlGn')


def corr_cmap():
    """Correlation matrix colormap (red low, green high)."""
    return plt.get_cmap('RdYlGn')


# --------------------------------------------------------------------------- #
# Quick self-check                                                            #
# --------------------------------------------------------------------------- #

if __name__ == '__main__':
    apply_thesis_style()
    fig, ax = thesis_subplots('single_short')
    ax.plot([1, 2, 4, 8], [1.0, 0.9, 0.8, 0.7], marker='o', color=POSITIVE)
    ax.set_xlabel('Battery scale')
    ax.set_ylabel('CV')
    ax.set_title('Demo plot')
    save_fig(fig, 'demo_plot_utils', out_dir='/tmp')
