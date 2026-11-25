"""Render the latest holdout baseline chart from a backtest CSV."""
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.robustness import bootstrap_trade_outcomes


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "backtest_20260821_111912.csv"
    output = root / "data" / "oos_baseline_chart.png"
    trades = pd.read_csv(csv_path)
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades = trades.sort_values("entry_time").reset_index(drop=True)
    trades["cumulative_r"] = trades["profit_r"].cumsum()
    by_symbol = trades.groupby("symbol")["profit_r"].sum().sort_values()
    boot = bootstrap_trade_outcomes(trades["profit_r"])
    rng = np.random.default_rng(boot.seed)
    totals = rng.choice(trades["profit_r"].to_numpy(), size=(boot.paths, len(trades)), replace=True).sum(axis=1)

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axes = plt.subplots(2, 2, figsize=(16, 10), gridspec_kw={"height_ratios": [1.15, 1]})
    figure.patch.set_facecolor("#f8fafc")
    figure.suptitle("Baseline OOS — 1 Jul a 21 Ago 2026", fontsize=20, fontweight="bold", x=0.06, ha="left")
    figure.text(0.06, 0.94, "Órdenes stop pendientes + costes conservadores | 29 operaciones | Evidencia todavía insuficiente", color="#475569", fontsize=11)

    ax = axes[0, 0]
    ax.plot(range(1, len(trades) + 1), trades["cumulative_r"], color="#2563eb", linewidth=2.8, marker="o", markersize=4)
    ax.axhline(0, color="#64748b", linewidth=1)
    ax.fill_between(range(1, len(trades) + 1), 0, trades["cumulative_r"], where=trades["cumulative_r"] >= 0, color="#bfdbfe", alpha=0.55)
    ax.set_title("R acumulado por operación", loc="left", fontweight="bold")
    ax.set_xlabel("Operación cronológica")
    ax.set_ylabel("R neto")
    ax.annotate(f"{trades['cumulative_r'].iloc[-1]:+.2f} R", (len(trades), trades["cumulative_r"].iloc[-1]), xytext=(-70, 12), textcoords="offset points", color="#1d4ed8", fontweight="bold")

    ax = axes[0, 1]
    colours = ["#dc2626" if value < 0 else "#16a34a" for value in by_symbol]
    bars = ax.barh(by_symbol.index, by_symbol.values, color=colours)
    ax.axvline(0, color="#64748b", linewidth=1)
    ax.set_title("Resultado neto por símbolo", loc="left", fontweight="bold")
    ax.set_xlabel("R neto")
    for bar, value in zip(bars, by_symbol.values):
        ax.text(value + (0.08 if value >= 0 else -0.08), bar.get_y() + bar.get_height() / 2, f"{value:+.2f}", va="center", ha="left" if value >= 0 else "right", fontweight="bold")

    ax = axes[1, 0]
    ax.hist(totals, bins=38, color="#93c5fd", edgecolor="#ffffff")
    ax.axvline(0, color="#dc2626", linewidth=2, label="0 R")
    ax.axvline(boot.p05_total_r, color="#ea580c", linewidth=2, linestyle="--", label=f"Percentil 5%: {boot.p05_total_r:+.2f} R")
    ax.axvline(trades["profit_r"].sum(), color="#2563eb", linewidth=2, label=f"Observado: {trades['profit_r'].sum():+.2f} R")
    ax.set_title("Bootstrap de resultados (5.000 remuestreos)", loc="left", fontweight="bold")
    ax.set_xlabel("R total remuestreado")
    ax.set_ylabel("Frecuencia")
    ax.legend(frameon=False, fontsize=9)

    ax = axes[1, 1]
    ax.axis("off")
    stats = [
        ("PF neto", "1.58"),
        ("Win rate", "44.8%"),
        ("Resultado", "+6.39 R"),
        ("Bootstrap P(R > 0)", "86.8%"),
        ("Percentil 5%", "−2.77 R"),
        ("Muestra OOS", "29 / 100 mínimo"),
    ]
    ax.text(0, 1.02, "Lectura correcta", fontsize=14, fontweight="bold", transform=ax.transAxes)
    for idx, (label, value) in enumerate(stats):
        row, col = divmod(idx, 2)
        x, y = col * 0.51, 0.78 - row * 0.28
        ax.text(x, y, label, transform=ax.transAxes, color="#475569", fontsize=10)
        colour = "#dc2626" if label in {"Percentil 5%", "Muestra OOS"} else "#0f172a"
        ax.text(x, y - 0.11, value, transform=ax.transAxes, color=colour, fontsize=17, fontweight="bold")
    ax.text(0, 0.0, "Conclusión: el baseline es positivo, pero aún no robusto.\nNo optimizar parámetros ni activar capital real con esta muestra.", transform=ax.transAxes, color="#991b1b", fontsize=11, bbox={"boxstyle": "round,pad=0.6", "facecolor": "#fee2e2", "edgecolor": "#fecaca"})

    figure.tight_layout(rect=(0.03, 0.03, 0.98, 0.91))
    figure.savefig(output, dpi=180, bbox_inches="tight")
    print(output)


if __name__ == "__main__":
    main()
