# Wyckoff + Smart Money — Oro (XAUUSD)

Implementación en Python de la estrategia *Wyckoff + Smart Money* para XAUUSD:
detección de estructura, puntos de interés y fases Wyckoff, puntuación de
confluencia sobre 17 puntos, gestión de riesgo, backtest vela a vela y
gráficos de cada operación al estilo TradingView sobre el esquema Wyckoff.

> Wyckoff y Smart Money no tienen validación académica ni backtests
> independientes. Este código sirve para medir la estrategia con reglas
> objetivas antes de arriesgar dinero, no para garantizar resultados.

## Uso rápido

```bash
pip install -r requirements.txt

# 1. Datos: velas de 1 minuto de Dukascopy (gratis, requiere Node.js)
scripts/descargar_datos.sh 2023-01-01 2024-12-31

# 2. Backtest + informes + gráficos
python -m xauusd backtest --data data/<archivo>.csv --out resultados/

# Variantes: confirmación en 5 min, o sin confirmación (orden límite en el POI)
python -m xauusd backtest --data data/<archivo>.csv --ltf 5min
python -m xauusd backtest --data data/<archivo>.csv --sin-confirmacion

# Sin datos reales, para probar que todo funciona
python -m xauusd backtest --sintetico --out resultados/

# Tests
python -m pytest
```

Salida en `resultados/`:

| Archivo | Contenido |
| --- | --- |
| `resumen.txt` | operaciones, operaciones/semana, acierto, R medio, profit factor, drawdown |
| `operaciones.csv` | cada operación con puntaje, criterios cumplidos, fase Wyckoff, POI, Fibonacci, resultado en R |
| `setups_rechazados.csv` | cada setup descartado y el motivo (RR, puntaje, límites de riesgo...) |
| `equity.csv`, `equity.png` | curva de capital y drawdown |
| `operaciones/*.png` | gráfico de cada operación |

## Cómo funciona

Flujo por cada vela de ejecución (15 min por defecto), sin mirar al futuro:

1. **Contexto multi-temporal.** Tendencia estructural en 4H (sesgo) y 1H.
   Una vela alta solo cuenta cuando ya ha cerrado.
2. **Estructura** (`structure.py`). Swings fractales, BOS, CHoCH, equal
   highs/lows y barridos de liquidez. Un CHoCH solo es válido si rompe el
   swing que protegía la tendencia; romper un swing interno es ruido.
3. **Wyckoff** (`wyckoff.py`). Rangos de trading, Spring/Upthrust con los
   criterios objetivos, test del Spring, SOS/SOW, fase vigente y las nueve
   pruebas. Si la fase Wyckoff marca el lado contrario, el setup se bloquea.
4. **Setup.** Tras cada BOS/CHoCH se marca el POI: el 50% de un FVG o el
   order block, prefiriendo el que cae en la zona Fibonacci 0.618–1. El stop
   va bajo la mecha del barrido más un colchón, y el TP1 en la siguiente
   liquidez externa. Si el RR a TP1 es menor de 1:3, se descarta.
5. **Confirmación en 1 min** (o 5 min con `--ltf 5min`). Cuando el precio
   entra en el POI, se espera un mini quiebre de estructura a favor: una
   vela menor que cierra más allá del último swing menor del retroceso
   (fractal de 2 velas, formado hasta 30 velas antes de entrar en la zona).
   Se entra a mercado al cierre de esa vela y se vuelve a exigir RR ≥ 1:3
   con ese precio. El setup se cancela si, antes de confirmar:
   - cierra más allá del stop o atraviesa el order block;
   - alcanza el objetivo sin haber vuelto al POI;
   - pasa 60 velas menores en la zona.

   Con `--sin-confirmacion` se vuelve a la orden límite directa en el POI.
6. **Sistema A o B.** A si la ruptura va a favor del sesgo 4H. B si va en
   contra, y además exige al menos 5 de las pruebas Wyckoff evaluables.
7. **Puntaje** (`scoring.py`). Se calcula en el momento de la entrada. Umbral: 12/17
   (A) o 14/17 (B). Riesgo del 0.5%, o del 1% por encima de 16.
8. **Riesgo** (`risk.py`):
   - 1 posición A + 1 B como máximo.
   - Exposición agregada ≤ 3%.
   - Como mucho 2 operaciones al día; con 2 pérdidas se para el día.
   - Stop semanal del 3%.
   - Drawdown interno máximo del 5%, que detiene el sistema.
9. **Gestión.** En TP1 se cierra el 50% y el stop pasa a break-even. El resto
   corre con un stop que sube a cada nuevo swing a favor (LPS/LPSY), sin
   techo de beneficio.

## Qué significa cada término en números

Todos los umbrales están en `xauusd/config.py` para poder ajustarlos.

| Concepto de la estrategia | Definición en el código |
| --- | --- |
| Swing | fractal de 3 velas a cada lado (se conoce 3 velas después) |
| Desplazamiento | cuerpo ≥ 1.2·ATR(14), cuerpo ≥ 60% del rango, volumen ≥ media |
| Volumen alto | ≥ 1.5 × media de las 20 velas anteriores |
| Equal highs / lows | dos swings separados menos de 0.1·ATR |
| Barrido de liquidez | la mecha supera un swing no roto y la vela cierra de vuelta |
| Rango de trading | 30+ velas con amplitud ≤ 8·ATR |
| Spring válido | fuera del soporte ≤ 2 velas, volumen de ruptura bajo o decreciente, sin spread amplio + volumen alto, cierre de vuelta dentro, sin velas amplias en contra después |
| Test del Spring | volumen ≤ 60% del Spring y en el tercio bajo de las últimas 25 velas |
| SOS / SOW | spread ≥ 1.2·ATR, volumen alto, cierre en el 25% extremo, sin volver a cerrar dentro del rango |
| Order block débil | 3 o más toques; mitigado si una vela cierra al otro lado |
| FVG | hueco de tres velas ≥ 0.2·ATR; entrada en su 50% |
| Divergencia RSI | extremo de origen más bajo que el swing anterior con RSI(14) más alto (y al revés) |
| Mini BOS (confirmación) | cierre de una vela de 1 min más allá del último fractal menor (2 velas) del retroceso hacia el POI |
| Kill zones (GMT) | 13–17 (solapamiento Londres-NY) y 7–10 (apertura de Londres) |
| Coste | 0.30 USD/oz por operación |

## Pendiente o aproximado

- **Stop tras la confirmación.** Por defecto se queda bajo la mecha del
  barrido, como dice la estrategia. Con `ltf_stop="ltf"` iría bajo el
  retroceso en 1 min: stop más corto y RR mayor, pero más fácil de saltar.
- **Prueba 1** (objetivo de punto y figura) y **prueba 8** (fuerza relativa
  frente al dólar) no se evalúan. Harían falta un conteo P&F y datos de DXY.
  El Sistema B exige 5 de las 7 restantes.
- **Etiquetas PS/SC/AR/ST y bandas de fase A–E del gráfico.** Son
  aproximadas: se sitúan en los extremos del rango. Spring, Test y SOS sí
  salen de la detección.
- **Riesgo con 16 puntos.** El doc dice "12–16 → 0.5%" y "16–17 → 1%", que
  se solapan. Aquí el 1% empieza por encima de 16 (`risk_max_score`).
- **Frecuencia de referencia:** 2–3 operaciones válidas por semana. Si el
  backtest da muchas más, el umbral está demasiado laxo.
- Solo backtest. Las alertas en vivo y el despliegue en un VPS vienen después
  de validarlo.
