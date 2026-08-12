import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
# V14 est autonome : son préfixe de configuration est ``TITANIUM_``.
# ``TRADINGAGENTS_`` reste lu en second, pour que le socle forké et sa suite de
# tests continuent de fonctionner sans réécriture. UNE seule table, UN seul
# chemin de code — pas deux systèmes de config en parallèle.
_PREFIXES = ("TITANIUM_", "TRADINGAGENTS_")

# Clé = suffixe de variable d'environnement, valeur = clé de DEFAULT_CONFIG.
_ENV_OVERRIDES = {
    "LLM_PROVIDER":         "llm_provider",
    "DEEP_THINK_LLM":       "deep_think_llm",
    "QUICK_THINK_LLM":      "quick_think_llm",
    "LLM_BACKEND_URL":      "backend_url",
    "OUTPUT_LANGUAGE":      "output_language",
    "MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "BENCHMARK_TICKER":     "benchmark_ticker",
    "TEMPERATURE":          "temperature",
    "LLM_MAX_RETRIES":      "llm_max_retries",
    "LLM_TIMEOUT":          "llm_timeout",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "ANTHROPIC_EFFORT":        "anthropic_effort",
    # ── Exécution MT5 (V14). Le mur démo↔réel vit ici, avec le reste de la
    #    configuration : un seul objet à lire pour savoir ce que fait le bot.
    "EXEC_ENABLED":            "exec_enabled",
    "DEMO_LOGIN":              "demo_login",
    "ALLOW_REAL_ACCOUNT":      "allow_real_account",
    "ALLOW_MIN_LOT_OVERRISK":  "allow_min_lot_overrisk",
    "MAGIC":                   "magic",
    "DEVIATION_POINTS":        "deviation_points",
    # Gestion dynamique du stop, en multiples du R initial.
    "MANAGE_BREAKEVEN_R":      "manage_breakeven_r",
    "MANAGE_TRAIL_START_R":    "manage_trail_start_r",
    "MANAGE_TRAIL_DIST_R":     "manage_trail_dist_r",
}


def env_var_names() -> tuple[str, ...]:
    """Toutes les variables d'environnement reconnues, tous préfixes confondus.

    Sert aux tests à repartir d'un environnement propre : itérer
    ``_ENV_OVERRIDES`` ne suffit plus, ses clés sont des suffixes depuis que V14
    accepte deux préfixes. Oublier ``TITANIUM_*`` laisse le `.env` du projet
    fuiter dans les tests.
    """
    return tuple(prefixe + suffixe
                 for suffixe in _ENV_OVERRIDES
                 for prefixe in _PREFIXES) + tuple(
        prefixe + suffixe
        for suffixe in ("RESULTS_DIR", "CACHE_DIR", "MEMORY_LOG_PATH")
        for prefixe in _PREFIXES)


def _env_lookup(suffix: str, env=None):
    """Valeur d'une option, ``TITANIUM_`` prioritaire sur ``TRADINGAGENTS_``."""
    e = env if env is not None else os.environ
    for prefixe in _PREFIXES:
        brut = e.get(prefixe + suffix)
        if brut not in (None, ""):
            return brut, prefixe + suffix
    return None, None


def _env_path(suffix: str, defaut: str) -> str:
    """Chemin configurable, même règle de préfixe que le reste."""
    valeur, _ = _env_lookup(suffix)
    return valeur if valeur else defaut


_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TITANIUM_* (then TRADINGAGENTS_*) env vars to the config in-place."""
    for suffix, key in _ENV_OVERRIDES.items():
        raw, nom = _env_lookup(suffix)
        if raw is None:
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {nom}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": _env_path("RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": _env_path("CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": _env_path(
        "MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")
    ),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # SDK retry budget forwarded to every provider chat client. None leaves each
    # provider/SDK at its own default (usually 2). Raise it to ride out bursty
    # 429 throttling on rate-limited deployments instead of aborting a run (#1091).
    "llm_max_retries": None,
    # Per-request provider timeout in seconds. None keeps the SDK default.
    # The asynchronous V14 analyst worker supplies its own bounded default so
    # one stalled provider call cannot consume the full opinion TTL.
    "llm_timeout": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    # V14 : MT5 PRIORITAIRE, yfinance en SECOURS. La chaîne est ordonnée et
    # explicite — le routeur essaie mt5, et bascule sur yfinance dès que MT5
    # lève NoMarketDataError (action non couverte par le courtier) ou
    # Mt5NotAvailableError (terminal fermé).
    # Fondamentaux / news / macro restent sur yfinance & co : un courtier CFD
    # n'en fournit pas. Ce n'est pas un manque, c'est la bonne répartition.
    "data_vendors": {
        "core_stock_apis": "mt5,yfinance",       # Options: mt5, alpha_vantage, yfinance
        "technical_indicators": "mt5,yfinance",  # Options: mt5, alpha_vantage, yfinance
        "fundamental_data": "yfinance",          # Options: alpha_vantage, yfinance
        "news_data": "yfinance",                 # Options: alpha_vantage, yfinance
        "macro_data": "fred",                    # Options: fred (needs FRED_API_KEY)
        "prediction_markets": "polymarket",      # Options: polymarket (keyless)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    # ── Exécution MT5 (V14) — le mur démo↔réel, dans LA config.
    # Défauts = défauts sûrs : désarmé, aucun login déclaré, réel interdit.
    # Le type de chaque valeur pilote la coercition des variables d'env
    # (`_coerce`), donc `exec_enabled` doit rester un bool et `magic` un int.
    "exec_enabled": False,
    "demo_login": 0,                  # 0 = aucun login démo déclaré → refus
    "allow_real_account": "",         # n'ouvre le réel que sur la phrase exacte
    "allow_min_lot_overrisk": False,
    "magic": 14_000,
    "deviation_points": 20,
    # Gestion dynamique du stop. Valeurs issues du post-mortem V12 : 23 % des
    # pertes avaient atteint +0.8 R avant de repartir au stop.
    "manage_breakeven_r": 0.8,
    "manage_trail_start_r": 1.2,
    "manage_trail_dist_r": 0.8,

    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
})
