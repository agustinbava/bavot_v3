"""Configuration loading: merges config.yaml with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    use_rth: bool = True


@dataclass
class AnthropicConfig:
    model: str = "claude-sonnet-5"
    max_tokens: int = 3000
    effort: str | None = None  # low | medium | high (None = API default)
    prompt_path: str = "prompts/scalping_analyst.txt"


@dataclass
class IndicatorParams:
    stochrsi_k: int = 3
    stochrsi_d: int = 3
    stochrsi_rsi_length: int = 14
    stochrsi_stoch_length: int = 14
    volume_sma: int = 20
    swing_lookback: int = 2


@dataclass
class StrategyConfig:
    name: str = "A1"
    description: str = ""


@dataclass
class StrategyDef:
    prompt: str
    description: str = ""
    leverage: float = 1.0            # 1 = spot/cash; 10 = futuros 10x
    timeframes: list[str] | None = None  # None = usa los globales
    entry_window_min: int | None = None  # None = usa evaluation.*
    max_duration_min: int | None = None
    fee_pct_per_side: float | None = None  # None = fee por tipo (evaluation.*)


@dataclass
class PayloadConfig:
    timing_candles: int = 50
    context_candles: int = 35


@dataclass
class EvaluationConfig:
    position_usd: float = 10.0
    entry_window_min: int = 240
    max_duration_min: int = 480
    crypto_fee_pct: float = 0.10   # % per side (Binance spot taker)
    stock_fee_pct: float = 0.0     # % per side
    stock_fee_min_usd: float = 1.0 # minimum per order (IBKR Pro fixed)


@dataclass
class Instrument:
    symbol: str
    type: str  # "stock" | "crypto"


@dataclass
class Config:
    watchlist: list[Instrument]
    timeframes: list[str] = field(default_factory=lambda: ["5m", "15m", "30m"])
    num_candles: int = 50
    price_decimals: int = 2
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    strategies: dict[str, StrategyDef] = field(default_factory=dict)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    payload: PayloadConfig = field(default_factory=PayloadConfig)

    def activate_strategy(self, name: str) -> None:
        """Switch the active strategy to one from the catalog."""
        if name not in self.strategies:
            raise ValueError(
                f"strategy '{name}' not in config catalog "
                f"(available: {', '.join(self.strategies) or 'none'})"
            )
        sdef = self.strategies[name]
        self.strategy = StrategyConfig(name=name, description=sdef.description)
        self.anthropic.prompt_path = sdef.prompt
        if sdef.timeframes:
            self.timeframes = list(sdef.timeframes)

    def active_strategy_def(self) -> "StrategyDef | None":
        return self.strategies.get(self.strategy.name)


def _apply_env_overrides(cfg: Config) -> None:
    """Env vars win over YAML for connection + model settings."""
    if v := os.getenv("IBKR_HOST"):
        cfg.ibkr.host = v
    if v := os.getenv("IBKR_PORT"):
        cfg.ibkr.port = int(v)
    if v := os.getenv("IBKR_CLIENT_ID"):
        cfg.ibkr.client_id = int(v)
    if v := os.getenv("ANTHROPIC_MODEL"):
        cfg.anthropic.model = v


def load_config(path: str | Path) -> Config:
    """Load and validate the YAML config, then apply env overrides."""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())

    watchlist = [
        Instrument(symbol=item["symbol"], type=item["type"].lower())
        for item in raw.get("watchlist", [])
    ]
    for inst in watchlist:
        if inst.type not in ("stock", "crypto"):
            raise ValueError(
                f"{inst.symbol}: type must be 'stock' or 'crypto', got '{inst.type}'"
            )

    cfg = Config(
        watchlist=watchlist,
        timeframes=raw.get("timeframes", ["5m", "15m", "30m"]),
        num_candles=int(raw.get("num_candles", 50)),
        price_decimals=int(raw.get("price_decimals", 2)),
        ibkr=IBKRConfig(**(raw.get("ibkr") or {})),
        anthropic=AnthropicConfig(**(raw.get("anthropic") or {})),
        indicators=IndicatorParams(**(raw.get("indicators") or {})),
        strategy=StrategyConfig(**(raw.get("strategy") or {})),
        strategies={
            name: StrategyDef(**d)
            for name, d in (raw.get("strategies") or {}).items()
        },
        evaluation=EvaluationConfig(**(raw.get("evaluation") or {})),
        payload=PayloadConfig(**(raw.get("payload") or {})),
    )
    _apply_env_overrides(cfg)
    return cfg
