"""Parámetros numéricos del sistema.

Cada umbral que en la estrategia aparece como concepto ("volumen alto",
"desplazamiento", "equal highs"...) se fija aquí con un número, para que
el backtest sea reproducible y se pueda ajustar sin tocar la lógica.
"""
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- Temporalidades ---------------------------------------------------
    tf_bias: str = "4h"          # sesgo / tendencia dominante
    tf_context: str = "1h"       # segunda temporalidad para alineación
    tf_exec: str = "15min"       # temporalidad de ejecución
    tf_ltf: str = "1min"         # confirmación de entrada (1min o 5min)

    # --- Confirmación en temporalidad menor ---------------------------------
    ltf_confirm: bool = True      # False = orden límite directa en el POI
    ltf_swing_len: int = 2        # fractal de la temporalidad menor
    ltf_lookback: int = 30        # el swing a romper puede formarse hasta N velas antes de entrar en el POI
    ltf_max_wait: int = 60        # velas menores máximas en la zona sin confirmar
    ltf_stop: str = "setup"       # "setup": bajo la mecha del barrido; "ltf": bajo el retroceso menor

    # --- Estructura -------------------------------------------------------
    swing_len: int = 3            # velas a cada lado para confirmar un swing (fractal)
    atr_len: int = 14
    # Desplazamiento: cuerpo >= k·ATR y cuerpo >= 60% del rango de la vela
    displacement_atr: float = 1.2
    displacement_body_ratio: float = 0.6
    # Volumen "alto": >= x veces la media de las últimas N velas
    vol_len: int = 20
    vol_high: float = 1.5
    # Equal highs / lows: dos swings separados menos de k·ATR
    eq_tolerance_atr: float = 0.1
    # Un barrido debe revertir (cerrar de vuelta) en como mucho N velas
    sweep_max_bars: int = 2

    # --- Wyckoff -----------------------------------------------------------
    range_min_bars: int = 30      # duración mínima de un rango de trading
    range_max_width_atr: float = 8.0  # ancho máximo del rango en ATR
    spring_max_bars_outside: int = 2  # velas máximas por debajo del soporte
    spring_test_vol_ratio: tuple = (0.4, 0.6)  # test con 40-60% menos volumen
    spring_test_vol_lookback: int = 25  # tercio bajo de volumen de las últimas 20-30

    # --- Pruebas 1 y 8 --------------------------------------------------------
    pf_box_atr: float = 0.5       # caja del punto y figura = 0.5 × ATR mediano del rango
    pf_reversal: int = 3          # reversión clásica de 3 cajas

    # --- Order blocks y FVG ------------------------------------------------
    ob_max_touches: int = 3       # a partir de 3 toques, OB débil
    fvg_min_atr: float = 0.2      # tamaño mínimo del hueco en ATR
    poi_max_age_bars: int = 96    # un POI caduca a las 96 velas de ejecución (24h en 15m)

    # --- Fibonacci ---------------------------------------------------------
    fib_zone: tuple = (0.618, 0.79)
    fib_zone_deep: tuple = (0.79, 1.0)  # oro a veces llega al 88-100%

    # --- RSI ----------------------------------------------------------------
    rsi_len: int = 14
    div_lookback_swings: int = 2

    # --- Filtro horario (GMT) ---------------------------------------------
    killzones: tuple = ((13, 17), (7, 10))  # solapamiento Londres-NY, apertura Londres

    # --- Scoring -----------------------------------------------------------
    weights: dict = field(default_factory=lambda: {
        "spring_upthrust": 3.0,
        "sos_sow": 2.0,
        "bos_displacement": 2.0,
        "wyckoff_phase": 2.0,
        "liquidity_swept": 2.0,
        "poi_return": 1.5,
        "mtf_alignment": 1.5,
        "fib_confluence": 1.0,
        "rsi_volume": 1.0,
        "killzone": 1.0,
    })
    threshold_a: float = 12.0
    threshold_b: float = 14.0
    min_rr: float = 3.0
    wyckoff_tests_min_b: int = 6   # de las nueve pruebas (la estrategia pide 5-6)

    # --- Riesgo ------------------------------------------------------------
    initial_capital: float = 100_000.0
    risk_base: float = 0.005      # 0.5% en umbral..16
    risk_max: float = 0.01        # 1% con puntaje 16-17
    risk_max_score: float = 16.0
    max_open_risk: float = 0.03
    max_trades_day: int = 2
    max_losses_day: int = 2
    weekly_loss_stop: float = 0.03
    internal_max_dd: float = 0.05
    sl_buffer_atr: float = 0.1    # colchón bajo la mecha
    # Gestión: al llegar al TP1 se cierra una parte y el resto corre con trailing
    tp1_close_fraction: float = 0.5
    spread: float = 0.30          # USD por onza, coste por operación (ida+vuelta)
