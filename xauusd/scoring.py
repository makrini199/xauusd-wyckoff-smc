"""Puntuación de confluencia (máximo 17) y riesgo asociado al puntaje."""
from .config import Config

LABELS = {
    "spring_upthrust": "Spring/Upthrust válido",
    "sos_sow": "Sign of Strength / Weakness confirmado",
    "bos_displacement": "Break of Structure con desplazamiento",
    "wyckoff_phase": "Fase Wyckoff clara identificada",
    "liquidity_swept": "Liquidez barrida antes del movimiento",
    "poi_return": "Retorno a punto de interés (OB o 50% FVG)",
    "mtf_alignment": "Tendencia alineada en varias temporalidades",
    "fib_confluence": "Confluencia con Fibonacci (zona de oro)",
    "rsi_volume": "Divergencia de RSI o volumen coherente",
    "killzone": "Ventana horaria favorable (kill zone)",
}


def score(checks: dict[str, bool], cfg: Config) -> float:
    return sum(w for k, w in cfg.weights.items() if checks.get(k))


def max_score(cfg: Config) -> float:
    return sum(cfg.weights.values())


def threshold(system: str, cfg: Config) -> float:
    return cfg.threshold_a if system == "A" else cfg.threshold_b


def risk_fraction(points: float, cfg: Config) -> float:
    """0.5% desde el umbral; 1% solo con un puntaje prácticamente perfecto.

    La estrategia dice "12-16 → 0.5%" y "16-17 → 1%" (se solapan en 16);
    aquí el 1% empieza en `risk_max_score` (por defecto por encima de 16).
    """
    return cfg.risk_max if points > cfg.risk_max_score else cfg.risk_base
