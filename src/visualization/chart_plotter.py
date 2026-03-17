"""
Visualization Module — Chart Plotter
Generates equity curves, performance summaries, and trade overlays.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend (safe for servers)
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Rectangle
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

logger = logging.getLogger(__name__)


class ChartPlotter:
    """
    Generates charts from backtest results and trade history.

    All plots are saved as PNG files to results_dir.
    """

    COLORS = {
        'equity':     '#2196F3',
        'balance':    '#4CAF50',
        'drawdown':   '#F44336',
        'win':        '#4CAF50',
        'loss':       '#F44336',
        'demand':     '#4CAF50',
        'supply':     '#F44336',
        'long_entry': '#2196F3',
        'short_entry':'#FF9800',
        'sl':         '#F44336',
        'tp':         '#4CAF50',
        'background': '#1E1E2E',
        'grid':       '#2E2E3E',
        'text':       '#CDD6F4',
    }

    def __init__(self, results_dir: str = 'results'):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(exist_ok=True)
        self._check_available()

    def _check_available(self):
        if not MATPLOTLIB_AVAILABLE:
            logger.warning("matplotlib not installed — charts disabled. "
                           "Run: pip install matplotlib")

    # ------------------------------------------------------------------
    # Equity Curve
    # ------------------------------------------------------------------

    def plot_equity_curve(self, equity_curve: List[Dict],
                          filename: str = 'equity_curve.png') -> Optional[Path]:
        """
        Plot account equity and balance over time with drawdown shading.

        Args:
            equity_curve: List of {'time', 'equity', 'balance', 'open_positions'}
            filename:     Output filename

        Returns:
            Path to saved PNG or None
        """
        if not MATPLOTLIB_AVAILABLE or not equity_curve:
            return None

        df = pd.DataFrame(equity_curve)
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time').sort_index()

        # Calculate drawdown
        peak      = df['equity'].cummax()
        drawdown  = (peak - df['equity']) / peak * 100

        fig = plt.figure(figsize=(14, 9), facecolor=self.COLORS['background'])
        gs  = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.08)

        # --- Top: Equity + Balance ---
        ax1 = fig.add_subplot(gs[0])
        ax1.set_facecolor(self.COLORS['background'])
        ax1.plot(df.index, df['equity'],  color=self.COLORS['equity'],
                 linewidth=2, label='Equity')
        ax1.plot(df.index, df['balance'], color=self.COLORS['balance'],
                 linewidth=1.5, linestyle='--', alpha=0.7, label='Balance')
        ax1.fill_between(df.index, df['equity'], df['balance'],
                         alpha=0.1, color=self.COLORS['equity'])
        ax1.set_ylabel('Account Value ($)', color=self.COLORS['text'])
        ax1.legend(loc='upper left', facecolor=self.COLORS['background'],
                   labelcolor=self.COLORS['text'])
        ax1.grid(True, color=self.COLORS['grid'], alpha=0.5)
        ax1.tick_params(colors=self.COLORS['text'], labelbottom=False)
        ax1.spines[:].set_color(self.COLORS['grid'])
        ax1.set_title('Equity Curve', color=self.COLORS['text'], fontsize=14, pad=10)

        # --- Middle: Drawdown ---
        ax2 = fig.add_subplot(gs[1], sharex=ax1)
        ax2.set_facecolor(self.COLORS['background'])
        ax2.fill_between(df.index, 0, -drawdown,
                         color=self.COLORS['drawdown'], alpha=0.6)
        ax2.plot(df.index, -drawdown, color=self.COLORS['drawdown'], linewidth=1)
        ax2.set_ylabel('Drawdown (%)', color=self.COLORS['text'])
        ax2.grid(True, color=self.COLORS['grid'], alpha=0.5)
        ax2.tick_params(colors=self.COLORS['text'], labelbottom=False)
        ax2.spines[:].set_color(self.COLORS['grid'])

        # --- Bottom: Open positions ---
        ax3 = fig.add_subplot(gs[2], sharex=ax1)
        ax3.set_facecolor(self.COLORS['background'])
        ax3.bar(df.index, df['open_positions'],
                color=self.COLORS['equity'], alpha=0.5, width=0.02)
        ax3.set_ylabel('Open Trades', color=self.COLORS['text'])
        ax3.set_xlabel('Date', color=self.COLORS['text'])
        ax3.grid(True, color=self.COLORS['grid'], alpha=0.5)
        ax3.tick_params(colors=self.COLORS['text'])
        ax3.spines[:].set_color(self.COLORS['grid'])

        # Format x-axis dates
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        fig.autofmt_xdate(rotation=30)

        out = self.results_dir / filename
        plt.savefig(out, dpi=150, bbox_inches='tight',
                    facecolor=self.COLORS['background'])
        plt.close(fig)
        logger.info(f"Equity curve saved: {out}")
        return out

    # ------------------------------------------------------------------
    # Performance Summary
    # ------------------------------------------------------------------

    def plot_performance_summary(self, results: Dict,
                                 filename: str = 'performance_summary.png') -> Optional[Path]:
        """
        4-panel summary: win/loss bar, RR distribution, monthly PnL,
        profit factor gauge.
        """
        if not MATPLOTLIB_AVAILABLE:
            return None

        fig, axes = plt.subplots(2, 2, figsize=(14, 9),
                                 facecolor=self.COLORS['background'])
        fig.suptitle('Performance Summary', color=self.COLORS['text'],
                     fontsize=15, y=1.01)

        for ax in axes.flat:
            ax.set_facecolor(self.COLORS['background'])
            ax.tick_params(colors=self.COLORS['text'])
            ax.spines[:].set_color(self.COLORS['grid'])

        # --- 1: Win / Loss bar ---
        ax = axes[0, 0]
        wins   = results.get('wins',   0)
        losses = results.get('losses', 0)
        bars = ax.bar(['Wins', 'Losses'], [wins, losses],
                      color=[self.COLORS['win'], self.COLORS['loss']])
        ax.set_title('Win / Loss', color=self.COLORS['text'])
        ax.set_ylabel('Count',     color=self.COLORS['text'])
        ax.grid(axis='y', color=self.COLORS['grid'], alpha=0.5)
        for bar, val in zip(bars, [wins, losses]):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                    str(val), ha='center', color=self.COLORS['text'], fontsize=12)

        # --- 2: Key metrics table ---
        ax = axes[0, 1]
        ax.axis('off')
        metrics = [
            ('Total Trades',   f"{results.get('total_trades', 0)}"),
            ('Win Rate',       f"{results.get('win_rate', 0):.1%}"),
            ('Total PnL',      f"${results.get('total_pnl', 0):.2f}"),
            ('Avg Win',        f"${results.get('avg_win', 0):.2f}"),
            ('Avg Loss',       f"${results.get('avg_loss', 0):.2f}"),
            ('Profit Factor',  f"{results.get('profit_factor', 0):.2f}"),
            ('Expectancy',     f"${results.get('expectancy', 0):.2f}"),
            ('Max Drawdown',   f"{results.get('max_drawdown', 0):.2f}%"),
            ('Final Balance',  f"${results.get('final_balance', 0):.2f}"),
        ]
        col_labels = ['Metric', 'Value']
        table = ax.table(
            cellText=metrics,
            colLabels=col_labels,
            cellLoc='center',
            loc='center',
        )
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.2, 1.6)
        for key, cell in table.get_celld().items():
            cell.set_facecolor(self.COLORS['background'])
            cell.set_text_props(color=self.COLORS['text'])
            cell.set_edgecolor(self.COLORS['grid'])
        ax.set_title('Key Metrics', color=self.COLORS['text'])

        # --- 3: Equity curve (mini) ---
        ax = axes[1, 0]
        equity_curve = results.get('equity_curve', [])
        if equity_curve:
            eq_df = pd.DataFrame(equity_curve)
            eq_df['time'] = pd.to_datetime(eq_df['time'])
            ax.plot(eq_df['time'], eq_df['equity'],
                    color=self.COLORS['equity'], linewidth=1.5)
            initial = eq_df['equity'].iloc[0]
            ax.axhline(initial, color=self.COLORS['grid'],
                       linestyle='--', linewidth=0.8, alpha=0.7)
            ax.fill_between(eq_df['time'], initial, eq_df['equity'],
                            where=(eq_df['equity'] >= initial),
                            alpha=0.2, color=self.COLORS['win'])
            ax.fill_between(eq_df['time'], initial, eq_df['equity'],
                            where=(eq_df['equity'] < initial),
                            alpha=0.2, color=self.COLORS['loss'])
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%y'))
            fig.autofmt_xdate(rotation=30)
        else:
            ax.text(0.5, 0.5, 'No equity data', ha='center', va='center',
                    color=self.COLORS['text'])
        ax.set_title('Equity', color=self.COLORS['text'])
        ax.set_ylabel('$',     color=self.COLORS['text'])
        ax.grid(True,          color=self.COLORS['grid'], alpha=0.5)

        # --- 4: Profit factor gauge (horizontal bar) ---
        ax = axes[1, 1]
        pf = min(results.get('profit_factor', 0), 5.0)  # Cap at 5 for display
        ax.barh(['Profit\nFactor'], [pf], color=self.COLORS['win'] if pf >= 1.5
                else self.COLORS['loss'], height=0.4)
        ax.axvline(1.0, color='white', linestyle='--', linewidth=1, alpha=0.7,
                   label='Break-even (1.0)')
        ax.axvline(1.5, color=self.COLORS['win'], linestyle=':', linewidth=1,
                   alpha=0.7, label='Good (1.5)')
        ax.set_xlim(0, 5)
        ax.set_xlabel('Profit Factor', color=self.COLORS['text'])
        ax.set_title('Profit Factor', color=self.COLORS['text'])
        ax.legend(facecolor=self.COLORS['background'],
                  labelcolor=self.COLORS['text'], fontsize=8)
        ax.text(pf + 0.05, 0, f'{pf:.2f}', va='center', color=self.COLORS['text'])
        ax.grid(axis='x', color=self.COLORS['grid'], alpha=0.5)

        plt.tight_layout()
        out = self.results_dir / filename
        plt.savefig(out, dpi=150, bbox_inches='tight',
                    facecolor=self.COLORS['background'])
        plt.close(fig)
        logger.info(f"Performance summary saved: {out}")
        return out

    # ------------------------------------------------------------------
    # OHLC + Zones + Trades
    # ------------------------------------------------------------------

    def plot_trades_on_chart(self, ohlc_df: pd.DataFrame,
                              trades: List[Dict],
                              zones: List[Dict] = None,
                              title: str = 'Trade Chart',
                              filename: str = 'trade_chart.png') -> Optional[Path]:
        """
        Plot OHLC candlestick chart with supply/demand zones and trade entries/exits.

        Args:
            ohlc_df: DataFrame with open/high/low/close columns and datetime index
            trades:  List of trade dicts from Position.to_dict()
            zones:   List of zone dicts from Zone.to_dict()
            title:   Chart title
            filename: Output filename
        """
        if not MATPLOTLIB_AVAILABLE or ohlc_df.empty:
            return None

        fig, ax = plt.subplots(figsize=(16, 8), facecolor=self.COLORS['background'])
        ax.set_facecolor(self.COLORS['background'])
        ax.spines[:].set_color(self.COLORS['grid'])
        ax.tick_params(colors=self.COLORS['text'])
        ax.set_title(title, color=self.COLORS['text'], fontsize=13)

        # Draw candlesticks
        self._draw_candles(ax, ohlc_df)

        # Draw supply/demand zones
        if zones:
            for zone in zones:
                self._draw_zone(ax, zone, ohlc_df)

        # Draw trade entries/exits
        if trades:
            for trade in trades:
                self._draw_trade(ax, trade)

        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        fig.autofmt_xdate(rotation=30)
        ax.grid(True, color=self.COLORS['grid'], alpha=0.3)
        ax.set_xlabel('Date',  color=self.COLORS['text'])
        ax.set_ylabel('Price', color=self.COLORS['text'])

        out = self.results_dir / filename
        plt.savefig(out, dpi=150, bbox_inches='tight',
                    facecolor=self.COLORS['background'])
        plt.close(fig)
        logger.info(f"Trade chart saved: {out}")
        return out

    # ------------------------------------------------------------------
    # Batch generation from results folder
    # ------------------------------------------------------------------

    def generate_all_charts(self) -> List[Path]:
        """
        Load all JSON result files from results_dir and generate all charts.

        Returns list of generated file paths.
        """
        generated = []

        # Equity curve
        eq_file = self.results_dir / 'equity_curve.json'
        if eq_file.exists():
            with open(eq_file) as f:
                equity_curve = json.load(f)
            p = self.plot_equity_curve(equity_curve)
            if p:
                generated.append(p)

        # Performance summary (needs full results — build from risk_summary + trade_history)
        risk_file    = self.results_dir / 'risk_summary.json'
        history_file = self.results_dir / 'trade_history.json'
        if risk_file.exists() and history_file.exists():
            with open(risk_file)    as f: risk    = json.load(f)
            with open(history_file) as f: history = json.load(f)

            wins   = [t for t in history if t.get('realized_pnl', 0) > 0]
            losses = [t for t in history if t.get('realized_pnl', 0) <= 0]
            n      = len(history)
            total_pnl = sum(t.get('realized_pnl', 0) for t in history)
            avg_win   = (sum(t['realized_pnl'] for t in wins)   / len(wins))   if wins   else 0
            avg_loss  = abs(sum(t['realized_pnl'] for t in losses) / len(losses)) if losses else 0
            gross_win  = sum(t.get('realized_pnl', 0) for t in wins)
            gross_loss = abs(sum(t.get('realized_pnl', 0) for t in losses))

            results = {
                'total_trades':  n,
                'wins':          len(wins),
                'losses':        len(losses),
                'win_rate':      len(wins) / n if n else 0,
                'total_pnl':     total_pnl,
                'avg_win':       avg_win,
                'avg_loss':      avg_loss,
                'profit_factor': gross_win / gross_loss if gross_loss else 0,
                'expectancy':    (len(wins)/n * avg_win) - ((1 - len(wins)/n) * avg_loss) if n else 0,
                'max_drawdown':  risk.get('max_drawdown', 0),
                'final_balance': risk.get('account_balance', 0) + risk.get('daily_pnl', 0),
                'equity_curve':  json.load(open(eq_file)) if eq_file.exists() else [],
            }
            p = self.plot_performance_summary(results)
            if p:
                generated.append(p)

        logger.info(f"Generated {len(generated)} charts")
        return generated

    # ------------------------------------------------------------------
    # Internal drawing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_candles(ax, df: pd.DataFrame):
        """Draw OHLC candlesticks."""
        width  = 0.6 / len(df)  # Normalised candle width
        dates  = mdates.date2num(df.index.to_pydatetime())

        for i, (date, row) in enumerate(zip(dates, df.itertuples())):
            o, h, l, c = row.open, row.high, row.low, row.close
            color = '#26A69A' if c >= o else '#EF5350'  # Green/Red
            # Body
            rect = Rectangle(
                (date - width / 2, min(o, c)),
                width, abs(c - o),
                facecolor=color, edgecolor=color, linewidth=0.5
            )
            ax.add_patch(rect)
            # Wick
            ax.plot([date, date], [l, min(o, c)], color=color, linewidth=0.8)
            ax.plot([date, date], [max(o, c), h], color=color, linewidth=0.8)

        ax.set_xlim(dates[0] - width, dates[-1] + width)
        ax.set_ylim(df['low'].min() * 0.999, df['high'].max() * 1.001)

    @staticmethod
    def _draw_zone(ax, zone: dict, df: pd.DataFrame):
        """Shade a supply or demand zone."""
        zone_type = zone.get('zone_type', '')
        color = '#4CAF50' if zone_type == 'demand' else '#F44336'

        dates = mdates.date2num(df.index.to_pydatetime())
        x_min = dates[0]
        x_max = dates[-1]

        rect = Rectangle(
            (x_min, zone['low']),
            x_max - x_min,
            zone['high'] - zone['low'],
            facecolor=color, alpha=0.15,
            edgecolor=color, linewidth=0.8, linestyle='--'
        )
        ax.add_patch(rect)

    @staticmethod
    def _draw_trade(ax, trade: dict):
        """Draw entry/exit markers and SL/TP lines for a single trade."""
        direction = trade.get('direction', 'long')
        entry     = trade.get('entry_price', 0)
        sl        = trade.get('stop_loss',   0)
        tp        = trade.get('take_profit', 0)

        marker = '^' if direction == 'long' else 'v'
        color  = '#2196F3' if direction == 'long' else '#FF9800'

        ax.axhline(entry, color=color,        linestyle='-',  linewidth=0.8, alpha=0.8)
        ax.axhline(sl,    color='#F44336',    linestyle='--', linewidth=0.6, alpha=0.6)
        if tp:
            ax.axhline(tp, color='#4CAF50',   linestyle='--', linewidth=0.6, alpha=0.6)
