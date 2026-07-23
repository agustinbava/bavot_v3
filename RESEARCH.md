# Investigación nocturna de estrategias — 2026-07-14/15

## Objetivo
Encontrar una estrategia con edge REAL para crypto (futuros 10x, margen $100,
fees 0.10% round-trip sobre nocional) usando las 10 cryptos de la watchlist.
Pedido del usuario: probar A3 y otras hasta que dé profit.

## Criterio de éxito (anti-overfitting — NO negociable)
Una variante "gana" sólo si cumple TODO:
1. netR > 0 en TRAIN (primeros 60 días) Y en TEST (últimos 30, fuera de muestra)
2. ≥ 30 trades totales en test agregado
3. test positivo en ≥ la mitad de los símbolos con muestra (col "sym+")
4. Drawdown tolerable (mdd > -15R)

"Encontré profit barriendo hasta que apareció" NO cuenta — eso es ajuste al
ruido. Si ninguna familia lo logra, ese es el resultado y se reporta.

## Protocolo
- Datos: 90 días de 5m por símbolo (Binance), split 2/3 train / 1/3 test.
- Motor: backtest.py (mecánico, 0 tokens). Familias en GRIDS.
- A3 real: llm_backtest.py (API muestreada, tope $8) — corre en background.
- Resultados de cada iteración quedan ACA abajo.

## Estado
- [x] Familia meanrev (StochRSI reversión) en BNBUSDT: **FRACASO** — 108
      variantes, mejor train +7.5R → test -7.3R. Sin edge tras fees.
- [ ] meanrev + breakout + pullback × 10 símbolos (corriendo, bl1zlqu2d)
- [ ] LLM backtest A3 en BNB+ETH (corriendo, b2o1bzup0, tope $8)
- [ ] Familias pendientes según resultados: brackets ATR-relativos, filtro
      horario (sesión US/Asia), donchian con trailing, momentum multi-día

## Hallazgos

### Iteración 1 — meanrev BNBUSDT (23:5x)
Reversión por StochRSI pierde consistentemente después de fees. La única
variante positiva en train colapsó fuera de muestra. Conclusión: los cruces
de StochRSI en 5m/15m no tienen información suficiente para pagar 0.10% RT.

### Iteración 2 — 3 familias × 10 símbolos (00:0x)
**Las tres familias rápidas FRACASAN en todos los frentes:**
- meanrev: mejor variante train -51.6R (911 trades). Ninguna positiva en train.
  (Algunas dan test positivo con train negativo = ruido, no seleccionable.)
- breakout 5m: catástrofe (-314R train / -175R test, 0-1 símbolos positivos).
  Falsos quiebres + fees = trituradora.
- pullback EMA20: todo negativo en ambos períodos.

**Diagnóstico**: arrastre de comisiones. Con sl 0.3-0.75%, cada trade paga
0.13-0.33R de fees; a 900-4000 trades son 100-600R regalados. El edge bruto
de estas señales rápidas ronda cero — las fees lo hunden. La solución no es
otra señal rápida: es BAJAR LA FRECUENCIA y AGRANDAR los brackets.

### Iteración 3 — familias lentas (00:2x)
- slowtrend (Donchian 1h): FRACASA — todo negativo en train y test.
  El trend-following de quiebres no funciona en este régimen de 90 días.
- slowmr (RSI(14) 1h < 25, tp 2.5%, sl 1.2%, hold 24h): **PRIMERA CANDIDATA**
  → train +6.1R (286t), test +32.7R (118t), positiva en 8/10 símbolos.
  Variante sl=0.8: train -0.3R / test +44.5R, 9/10 símbolos.
  CAUTELA: edge en train casi nulo (+0.02R/t) — puede ser dependiente del
  régimen del período de test. Requiere walk-forward.

### Iteración 4 — walk-forward de la candidata (00:2x) → **RECHAZADA**
180 días, 3 folds: train SIEMPRE muy negativo (-82 a -165R), test errático
(+34 / -19 / +32). Por símbolo en 180d completos: **10 de 10 NEGATIVOS**
(BTC -11.4R, BNB -17.7R…), mdd hasta -32R. El resultado "prometedor" de la
iteración 3 era ajuste al régimen del período de test, no edge.
Conclusión parcial dura: 5 familias mecánicas (meanrev, breakout, pullback,
slowtrend, slowmr) probadas con rigor → NINGUNA tiene edge robusto tras
fees. Es el resultado esperable en mercados líquidos: las reglas técnicas
simples sobre precio/volumen no pagan las comisiones de forma persistente.

### Iteración 5 — cross-sectional (00:3x) → **FRACASA**
Reversión de fuerza relativa entre las 10 cryptos (long rezagado / short
líder 24h): negativa en casi todos los folds y parámetros. La única combo
positiva en train (spread 3%, hold 48h) es inconsistente en test con
muestras de 14 trades/fold. Sin edge.

### Iteración 6 — A3 real (LLM) sobre BNBUSDT, parcial (00:3x)
50 decisiones muestreadas del último mes: 16 señales, netR -14.6 (~-0.9R
por señal). El A3 con Sonnet 5 señala demasiado (32% de las lecturas) y
pierde en el replay histórico de BNB. Esperando que complete BNB + ETH
para el veredicto final.

## Cierre de familias mecánicas
6 familias probadas con protocolo train/test/walk-forward: meanrev,
breakout, pullback, slowtrend, slowmr, cross-sectional. NINGUNA con edge
robusto tras fees de futuros (0.10% RT). No se prueban más permutaciones
del mismo set de información (multiple comparisons = encontrar ruido).
Ideas fuera de alcance de esta noche (otra data): carry de funding rates,
order flow, eventos. Anotadas para otro momento.

### Iteración 7 — búsqueda web + edges estructurales (00:4x)
Investigación de edges documentados (fuentes: arXiv 2009.12155 "A Decade of
Evidence of Trend Following in Cryptocurrencies", arXiv 2506.08573, estudios
de funding arbitrage):
- **Funding carry** (spot + short perp): backtest con 329 días de funding
  real de Binance → BTC/ETH apenas 2-3% APR, alts negativas. El edge
  documentado (10-30% en 2024) se comprimió. DESCARTADO por magro.
- **Trend following DIARIO** (la literatura dice: intradía no, diario sí —
  coincide 100% con nuestros hallazgos empíricos):
  → **SMA 10/40 cross long/short, velas 1d, 720 días, 10 símbolos:**
  → Gana al buy & hold en LOS 3 FOLDS de test (+1391 vs +1079 / +52 vs
    -288 / +90 vs -7). En el fold del crash, la estrategia GANA.
  → Por símbolo (2 años): positiva en 9/10, gana al B&H en 7/10.
    BTC +80% (B&H -5%), ETH +75% (B&H -43%), DOGE +257% (B&H -45%).
  → ~20 trades por símbolo en 2 años (posiciones de semanas). Fees
    irrelevantes a esta escala. Fuente académica + confirmación propia.

## ⭐ CANDIDATA GANADORA: A5 "Tendencia Diaria"
SMA 10/40 crossover long/short en velas diarias, las 10 cryptos, mecánica
(0 tokens). NO es scalping: posiciones de semanas, ~1-2 señales/semana en
toda la cartera. Su valor: captura tendencias y esquiva/shortea los crash.
Caveats honestos: (a) el edge viene de la asimetría de los crash — en bull
markets puros rinde menos que holdear; (b) drawdowns intermedios de
semanas; (c) probada en 2 años de historia, no es garantía futura.
Implementada como candidata en a5_daily_trend.py — NO activada en cron.

### Iteración 8 — veredicto final del A3 real (01:0x)
376 decisiones muestreadas (BNB+ETH, último mes, cada 4h), $6.68 de API:
- 129 señales (34% de las lecturas — demasiado gatillo para 10x)
- 35 TP vs 88 SL → win rate 27% (necesita ~44% para empatar a R/R 1.5)
- **netR -82.9 | net USD -$124 sobre margen de $100 → cuenta liquidada**
El A3 tal como está prompteado PIERDE en replay histórico. Coherente con
todo lo demás: el intradía apalancado no paga sus costos, ni con reglas
mecánicas ni con LLM.

## Decisión final (2026-07-15, 01:1x)

**GANADORA: A5 "Tendencia Diaria"** (SMA 10/40 L/S, velas 1d, mecánica,
0 tokens). Única estrategia que pasó el protocolo completo: gana al buy &
hold en los 3 folds fuera de muestra, positiva en 9/10 símbolos en 2 años,
respaldada por literatura académica independiente (arXiv 2009.12155), y la
familia entera es robusta (8 variantes probadas, no un parámetro mágico).
Implementada como candidata en a5_daily_trend.py con journal propio
(tabla a5_positions, 10 posiciones virtuales iniciales al 2026-07-15).
NO activada en cron — pendiente de decisión del usuario.

**Recomendaciones a discutir a la mañana:**
1. ACTIVAR A5 en cron (1 corrida diaria tras el cierre UTC, gratis).
2. REPENSAR A3: el replay dice que pierde (-82.9R/mes). Opciones:
   (a) pausar su cron ($4/día de API por señales de EV negativo),
   (b) endurecer el prompt (exigir confianza high = menos señales),
   (c) dejarlo un mes en paper trading como validación forward del
       backtest. Recomiendo (a) o (b)+(c) con frecuencia reducida.
3. Funding carry: descartado por ahora (2-3% APR majors); revisar si el
   mercado vuelve a régimen de funding alto.
4. Scalping intradiario (cualquier variante): CERRADO como línea de
   investigación — 6 familias mecánicas + LLM, todo negativo tras fees.

## Post-informe (mañana del 15/07)
- A3 desactivada del cron. A5 activada (diaria 21:10 ART).
- **A5 sobre stocks/ETFs (17 símbolos, 2 años, datos Yahoo): NO FUNCIONA.**
  L-only pierde contra buy & hold en 17/17 (+13% vs +673% con fees reales;
  +285% vs +673% incluso con fees despreciables). L/S es catastrófico
  (-645%): shortear acciones en un bull market estructural. Motivo: el
  período fue un bull sostenido con pullbacks cortos — los cruces de SMA te
  sacan y te hacen perder los rebotes; el edge del trend following en crypto
  viene de sus crashes violentos, que las acciones no tuvieron. En equities
  la literatura ubica el momentum en horizontes de ~12 meses, no en cruces
  diarios. VEREDICTO: A5 es crypto-only; para stocks, a esta escala, buy &
  hold + selección manual le gana a cualquier timing mecánico probado.


## Stress-test de A5 en regímenes extremos (2026-07-16)
SMA 10/40 L/S sobre las 9 monedas con historia desde 2020:
- COVID crash (feb-jun 2020): estrategia +229% vs B&H -235% → gana 9/9
- Bull 2021: +2993% vs B&H +5321% → captura ~56% del bull (gana 2/9)
- Bear 2022 (LUNA/FTX): +197% vs B&H -538% → gana 7/9
Conclusión: el perfil es el documentado — POSITIVA EN LOS TRES REGÍMENES,
incluidos los dos peores desastres de la historia de crypto. Cede parte de
los bull markets a cambio de ganar (no sólo proteger) en los crashes. Con
esto A5 queda evaluada sobre ~6 años y todos los regímenes conocidos.


## Veredicto A4 + decisión final de stocks (2026-07-16)
Backtest de A4 en la ventana más volátil de 2 años (crash de aranceles,
2025-03-11→04-25, TQQQ/TSLA/MU/AMD/PLTR, 60 decisiones vía Batch API, $0.56):
28 señales, win rate 28%, R bruto +2.2 (≈cero estadístico), NETO -$50.82
(las fees de IBKR sobre $100 se comen el empate bruto).
Cuadro completo de stocks: A5 mecánica pierde vs B&H 17/17, A2 perdió en
vivo, A4 empata bruto y pierde neto en su mejor escenario.
**DECISIÓN DEL USUARIO: detener stocks. Foco en crypto (A5) hasta encontrar
un modelo de stocks que merezca implementarse.** A4 desactivada del cron;
sus 4 señales abiertas se dejan resolver para el learn.py.

## Ampliación de universo + estudio de regímenes (2026-07-16, gratis)

**Universo 19 → 29.** A pedido del usuario, +10 monedas por volumen promedio
30d con >=400d de historia (criterio ex-ante, no por backtest individual):
ADA, TON, AAVE, UNI, AVAX, LINK, BCH, TRUMP, HBAR, INJ. Validación en 720d
/ 3 tramos vs B&H: 19 solas 2/3 ✓, 10 nuevas 2/3 ✓, 29 combinado 2/3 ✓ —
mismo estándar, sin degradación. Nota: en ventana ~999d TODOS los grupos
pierden vs B&H (el tramo inicial es una bull run donde B&H hizo +103%);
consistente con el perfil conocido de A5 (gana en bear/lateral, queda
atrás en bull vertical).

**Variantes descartadas con datos (9 majors, ~960d):**
- TP al ganar +1.5% ($1.50): win rate sube de 35% a 80% pero neto cae de
  +20% a +1% (corta los ganadores gigantes que pagan las pérdidas).
- TP +1.5% con re-entrada diaria: -22% (fees + whipsaw, 120+ trades).
- Confirmación MACD(12,26,9): +14% vs +20% base — mide lo mismo que el
  cruce con otro lag, solo agrega churn.

**Regímenes (BTC vs SMA200 ±5%, 958d, portfolio 19):** la intuición del
usuario es direccionalmente cierta — el rendimiento de A5 difiere mucho
por régimen: lateral +0.30%/día, bajista +0.09, alcista +0.04 (mientras
B&H de BTC hace +0.26%/día en alcista). PERO las reglas de switching
probadas no pasan select-by-TRAIN/judge-by-TEST:
- base: TRAIN +139.7% / TEST +9.2%  ← ganadora en TRAIN (se elige esta)
- alcista→long fijo: TRAIN +8.8% / TEST +47.9% (inestable entre mitades:
  elegirla ahora sería hindsight)
- alcista→sólo longs: TRAIN +68.7% / TEST +30.2%
VEREDICTO: no tocar A5. El régimen queda como línea de investigación
abierta (probar clasificadores por moneda en vez de por BTC, más datos).

## Régimen por moneda + salidas anticipadas (2026-07-16, gratis)

Pedido del usuario tras ver ZEC -5%: filtro de régimen por moneda y
"salida antes". Protocolo: 29 monedas, 958d, elegir en TRAIN (1ra mitad),
juzgar en TEST (2da).

| variante                          | TRAIN    | TEST    |
|-----------------------------------|----------|---------|
| base (A5 L/S puro)                | +114.5%  | +16.1%  |
| agree200 (régimen por moneda)     | -18.6%   | +14.6%  |
| lat_flat (flat en banda ±5% SMA200)| +14.9%  | +14.9%  |
| stop -5% desde entrada            | +37.4%   | -2.4%   |
| stop -8% desde entrada            | +51.5%   | +4.7%   |
| trailing stop -10% desde el mejor | +58.2%   | +17.0%  |

GANADORA EN TRAIN: base, por paliza → no se toca A5. Los stops EMPEORAN
el sistema (convierten retrocesos temporales en pérdidas realizadas: el
cruce de salida ya ES el stop, uno adaptativo). El trailing 10% empató en
TEST pero perdió por mitad en TRAIN → sin evidencia de mejora.

Lección ZEC: el trade del sistema (cruce 2026-07-10 @ 499) iba +18% en el
pico y sigue +5.4%; el -5% del journal es artefacto de haber entrado a
mitad de tendencia en la inception (2026-07-15 @ 556). No generalizar
desde posiciones heredadas.

Línea que sigue abierta y SIN testear: sizing por volatilidad (el
problema real de "ZEC -5" es cuánto capital lleva una moneda volátil,
no cuándo salir).

## Sizing por volatilidad — PRIMERA MEJORA VALIDADA (2026-07-16, gratis)

Regla: al abrir cada pierna, usd = 100 * clip(0.03 / vol30_moneda, caps),
tamaño fijo durante la pierna, vol30 = std de retornos diarios previos
(sin lookahead). 29 monedas, 958d, selección por Sharpe en TRAIN.

| variante              | TRAIN $  | TRAIN shp | TEST $ | TEST shp | TEST dd | peor trade |
|-----------------------|----------|-----------|--------|----------|---------|------------|
| base ($100 fijos)     | +2303    | 1.33      | +834   | 0.48     | -840    | -70.95     |
| vs_2x (caps 0.5-2x)   | +2191    | 1.56      | +893   | 0.65     | -627    | -36.19     |
| vs_3x (caps 0.33-3x)  | +2212    | 1.58      | +928   | 0.69     | -600    | -36.19     |

GANADORA EN TRAIN: vs_3x → y su TEST confirma en TODAS las métricas:
más P&L (+928 vs +834), mejor Sharpe (0.69 vs 0.48), menos drawdown
(-600 vs -840), peor trade a la mitad (-36 vs -71). Efecto monótono con
los caps (vs_2x también mejora) → señal robusta, no ruido.

DECISIÓN PENDIENTE DEL USUARIO: implementar como "A5.1" en
a5_daily_trend.py (aplica sólo a piernas NUEVAS; las abiertas conservan
su tamaño de entrada). Posiciones quedarían entre $33 y $300 según la
volatilidad de la moneda.

DECISIÓN DEL USUARIO (2026-07-16): A5.1 ACTIVADA. Sizing por volatilidad
implementado en a5_daily_trend.py (columna position_usd, migración
automática). Aplica a piernas nuevas; las 29 abiertas conservan $100.

## Ensamble de velocidades (2026-07-16, gratis) — NO SE ACTIVA

Hipótesis pre-registrada: sumar velocidades lentas al 10/40 diversifica el
timing (práctica CTA estándar). 29 monedas, 798 días comparables (warm-up
SMA200 igual para todas), sizing A5.1 en todas, fees sobre turnover.

| variante                     | TRAIN $ | TRAIN shp | TEST $ | TEST shp | TEST dd |
|------------------------------|---------|-----------|--------|----------|---------|
| v1 = A5.1 actual (10/40)     | +726    | 0.68      | +829   | 0.75     | -570    |
| ens2 (10/40 + 20/100)        | +763    | 0.75      | +793   | 0.75     | -493    |
| ens3 (+ 50/200)              | +87     | 0.12      | +668   | 0.67     | -521    |

ens2 ganó TRAIN pero su TEST empata en Sharpe (0.75 = 0.75), gana menos
plata (+793 vs +829) y sólo mejora el drawdown. Sin mejora decisiva no se
reemplaza un sistema validado: más complejidad sin edge demostrado.
El 50/200 directamente no funciona en crypto (los ciclos son más cortos).
VEREDICTO: A5.1 queda como está. No re-probar ensambles con otras
velocidades sueltas (sería grid-search = overfitting); sólo volver acá
con una hipótesis nueva o más historia.

## Momentum cross-sectional — CANDIDATA B1 VALIDADA (2026-07-16, gratis)

Estrategia B (familia distinta a A5: apuesta RELATIVA fuertes-vs-débiles,
no direccional). Cada 7 días: rank por retorno 30d; LONG top-7, SHORT
bottom-7, $100/pata, fees sobre turnover. Protocolo TRAIN/TEST, 29 monedas:

| variante            | TRAIN $ | TRAIN shp | TEST $ | TEST shp | corr A5.1 |
|---------------------|---------|-----------|--------|----------|-----------|
| xs30 (lookback 30d) | +855    | 1.58      | +405   | 0.85     | +0.51     |
| xs90                | +387    | 0.78      | -308   | -0.70    | +0.25     |
| xs30 + vol sizing   | +186    | 0.59      | +291   | 0.94     | +0.22     |

GANADORA EN TRAIN: xs30 → TEST confirma (+$405, Sharpe 0.85, mejor que
la propia A5.1 en la misma ventana: 0.66). Momentum de 90d NO existe en
crypto (TEST -$308): el momentum cripto es de ciclo corto.

Cartera combinada (TEST): A5.1 sola +$875 shp 0.66 dd -$569;
A5.1 + xs30 +$1,317 shp 0.79 (dd -$833 por mayor capital desplegado).

Stress test (9 monedas 2020-2022, Q=3): crash COVID -$72 (pierde poco y
rápido: en un crash todo cae junto y el ranking se revuelve — lo cubre
A5.1, que en crashes brilla), bull 2021 +$147, bear 2022 +$24. No explota
en ningún régimen: al ser market-neutral, la dirección del mercado le es
casi indiferente.

PENDIENTE DECISIÓN USUARIO: activar como B1 "Momentum Relativo" (journal
propio, rebalanceo semanal, ~$1,400 bruto adicional en paper).

DECISIÓN DEL USUARIO (2026-07-16): B1 ACTIVADA. Implementada en
b1_xsect.py (tabla b1_positions + b1_state para el gate semanal), cron
diario 21:20, primer rebalanceo ejecutado (14 posiciones). Dashboard con
sección propia. Las posiciones persisten entre rebalanceos si la moneda
sigue en su cuartil (sin churn).

## Recolección de datos de futuros iniciada (2026-07-16, gratis)

collect_futures_data.py junta funding rates y open interest de las 29
monedas (cron horario). Backfill inicial: 74,992 funding rates (~2 años,
cada 8h) y 14,153 snapshots de OI 1h (Binance sólo publica los últimos
30 días de OI — la serie propia crece desde hoy). Hipótesis a testear
cuando haya muestra: (a) funding extremo como señal contraria, (b) OI
creciente como confirmación de tendencia para A5.1. NADA de esto se
opera hasta pasar el protocolo TRAIN/TEST de siempre — el funding ya
tiene historia suficiente para backtestear ahora; el OI necesita meses
de recolección.

## Hipótesis del funding — RECHAZADA en todas sus formas (2026-07-16, gratis)

Backtest con los datos propios (74,992 funding rates, 29 monedas, 730
días solapados con velas). P&L incluye accrual de funding (long paga /
short cobra). Señal sin lookahead. TRAIN/TEST:

| variante                  | TRAIN $ | shp   | TEST $ | shp   |
|---------------------------|---------|-------|--------|-------|
| contraria z>2 (90d)       | -586    | -1.19 | -10    | -0.03 |
| contraria abs >0.15%/día  | +145    | +0.67 | +7     | +0.05 |
| A5.1 + filtro funding     | +902    | +1.07 | +402   | +0.48 |
| A5.1 c/accrual (referencia)| +1576  | +1.58 | +762   | +0.77 |
| A5.1 pura (referencia)    | +1633   | +1.63 | +706   | +0.72 |

- La contraria pura no tiene edge (el "todos long → short" no paga en
  este período: el funding extremo suele acompañar tendencias que SIGUEN).
- El filtro sobre A5.1 la EMPEORA incluso en TRAIN (le corta trades
  buenos: funding alto y tendencia larga van juntos).
- HALLAZGO ÚTIL LATERAL: A5.1 con accrual de funding ≈ A5.1 pura (TEST
  incluso mejor: +762 vs +706, los shorts cobran). Ejecutar A5.1 en
  futuros reales NO sufriría por el funding — despeja una duda clave
  del puente a ejecución real.
- El OI espera meses de recolección antes de poder testearse.

## Grilla exhaustiva SL x TP + suspensión de longs (2026-07-17, gratis)

Pedido del usuario tras un día rojo (shorts ganando, longs perdiendo):
121 combos de SL (2-20% y sin) x TP (2-75% y sin) sobre A5.1, y overlay
"suspender longs si el lado long viene perdiendo" (rolling K días, modos
bloquear-nuevas / cerrar-todas, K=3/7/14).

RESULTADO GRILLA: base TRAIN shp 1.42 / TEST $971 shp 0.72.
- Sólo 3/121 combos superan a la base en TRAIN (por centésimas).
- La ganadora en TRAIN (SL 3%, sin TP) COLAPSA en TEST: $460 shp 0.53.
- 1/121 gana en ambos (SL 20%/TP 15%) pero con TRAIN$ menos de la mitad
  que la base — elegirla por su TEST sería hindsight puro.
- 118/121 son peores que no hacer nada YA EN TRAIN.

RESULTADO OVERLAY: la ganadora en TRAIN (K=7, bloquear nuevas, shp 1.57)
pierde en TEST vs base ($921/0.68 vs $971/0.72). Los modos cerrar-todas
se desangran en TEST ($276-$338). El overlay suspende ~50% de los días:
es un market-timing del propio sistema, y no funciona.

VEREDICTO DEFINITIVO: la superficie SL/TP está agotada — queda PROHIBIDO
volver a proponer stops/TP/suspensiones sobre A5.1 salvo hipótesis con
datos NUEVOS (no precio). La respuesta a "las long pierden en día rojo"
ya está dentro del sistema: los cruces migran el libro al lado corto
solos (2026-07-17: 16 short vs 13 long, flotante total +$18.62 con
longs -$35.94 y shorts +$54.56).

Lección de multiple-testing documentada: con 121 apuestas, 3 lucen bien
en TRAIN por azar y ninguna sostiene la mejora fuera de muestra.

## TP + re-entrada por pullback a la media (2026-07-17, gratis) — NO SE ACTIVA

Idea del usuario, mecánica NUEVA (distinta del TP-y-esperar-cruce ya
enterrado): cobrar al +X% y re-entrar EN LA MISMA dirección cuando el
precio retrocede a <=1% de la SMA. TP {5,10,20%} x pullback {SMA10,SMA40}:

| variante              | TRAIN $ | shp   | TEST $ | shp   |
|-----------------------|---------|-------|--------|-------|
| base A5.1             | +1938   | 1.42  | +971   | 0.72  |
| TP5 → SMA10           | +462    | 0.63  | +399   | 0.50  |
| TP5 → SMA40           | -18     | -0.05 | +10    | 0.02  |
| TP10 → SMA10          | +694    | 0.89  | +429   | 0.48  |
| TP10 → SMA40          | +379    | 0.86  | +272   | 0.48  |
| TP20 → SMA10          | +936    | 1.05  | +1069  | 1.06  |
| TP20 → SMA40          | +752    | 1.20  | +1017  | 1.40  |

GANADORA EN TRAIN: la base, otra vez → no se toca A5.1.
NOTA PARA EL FUTURO (no accionable hoy): TP20+pullback pierde en TRAIN
pero gana en TEST (patrón "mitad reciente favorece toma de ganancias",
igual que alcista→long-fijo del estudio de regímenes). Elegirla ahora
sería hindsight. QUEDA REGISTRADA COMO HIPÓTESIS para re-testear con
DATOS NUEVOS (~3 meses de vela diaria fresca, 2026-10): si el patrón
sostiene en un tercer fold que hoy no existe, ahí sí hay caso.

## Crash de DEXE y regla "sin crashers" — NO SE ACTIVA (2026-07-21, gratis)

DEXE -83% en un día (evento token-específico); ambos motores estaban LONG
$100 (superposición A5.1+B1) → ~-$164 combinados. El libro sin DEXE
estaría +$110 (mejor momento histórico).

Hipótesis del usuario testeada ex-ante (sin lookahead): excluir 365d a
toda moneda con caída diaria >30%. Resultado: TRAIN idéntico, TEST $901
vs $903 (shp 0.68 vs 0.67) — CERO efecto. Razón: en un sistema L/S los
crashes cortan para ambos lados. DEXE ya había crasheado -51% (2025-10)
y -32% (2025-06) y el sistema estaba SHORT en el de octubre (cobró);
HOME crasheó -45% y -39% en vivo y lo cobramos short. La regla elimina
tanto los cobros como los golpes → neto nulo.

Contexto: 5 días con caídas >30% en 2.7 años / 29 monedas; DEXE tiene 3
(reincidente) y HOME 2. Un -60/80% diario en microcaps NO es
extraordinario: es la cola conocida de la clase de activo, ya contenida
en la distribución que validó A5.1.

VEREDICTO: no se toca el universo por resultados recientes. Las salidas
las hacen los cruces (A5.1 flipea DEXE a short ~21:10, sizing mínimo $33
por vol; B1 lo rota el miércoles). El refresh trimestral ex-ante de
octubre decide su permanencia. Realizado esperado del evento: ~-$130
entre ambos motores — el peor trade del journal y el precio presupuestado
del diseño sin stops (que la grilla de 121 combos validó como óptimo en
distribución, no en cada evento).

## Post-DEXE: dos hipótesis corridas (2026-07-21/22, gratis)

1. FILTRO DE SOBREEXTENSIÓN (no abrir si |px-SMA40|/SMA40 > X):
   ext30 gana TRAIN (shp 1.47 vs 1.46) y TEST (+935/0.70 vs +903/0.67).
   CONFIRMED formal — pero margen mínimo. Decisión usuario pendiente:
   activar como A5.2 o re-test octubre (recomendado octubre).
2. CAP DE SUPERPOSICIÓN A5.1+B1: RECHAZADA. skip gana TRAIN (1.50) y
   colapsa en TEST (0.62 vs 0.71, -$440). El cap reduce dd (-541 vs
   -845) pero la superposición paga más de lo que cobra en distribución.
   Tercera aparición de la misma lección: las protecciones de cola
   cuestan más prima de la que devuelven (stops, anti-crashers, cap).

DEXE: el usuario pidió sacarla de la lista y borrar su historia. Se
explicó el costo (falsificar el track record que alimenta los gates de
go-live) y NO se borró nada. Pendiente su decisión sobre la excepción
de lista firmada; mientras, las reglas la manejan (B1 rota 22/7, cruce
A5.1 en días, refresh de octubre por volumen).

## ORB "first candle rule" (video YouTube) — RECHAZADA (2026-07-23, gratis)

Mecanizada fiel al video: OR 15m apertura NY, confirmación cierre 5m,
stop en la vela de señal, TP 1:2, 1 trade/día. QQQ 60d: bruto -$3.77
(wr 27%). BTC/ETH sesión NY 2 años (1,453 trades): bruto ~$0 con wr 33%
= EXACTAMENTE el breakeven matemático de un RR 1:2 → las reglas no
contienen información. Con fees, pérdida pareja e idéntica en TRAIN y
TEST (~-$0.10/trade). El "backtest" del video es cinta de semanas
elegidas + descargo final. Nota: el ORB académico (Zarattini & Aziz
2023) usa otro diseño (OR 5m, hold a cierre, sizing por vol) — sería
hipótesis aparte si algún día interesa.

## Stop de catástrofe — RECHAZADO en TODAS sus formas (2026-07-23)

Pedido tras DEXE (-90% sin salir). 3 formas, 29 monedas, TRAIN/TEST:
1. Diaria (salir al cierre del día -40%): peor trade IGUAL — DEXE cayó
   -82% dentro de una vela diaria, salir al cierre llega tarde.
2. Intradía por low diario (salir al nivel -X%): EMPEORA TEST y agranda
   el peor trade — vende el piso de mechazos que rebotan.
3. -X% en 6h/12h (data horaria, la más selectiva): base gana TRAIN
   (0.88 vs ≤0.87 todas); peor trade IGUAL -$31.6. cat-40%-6h "gana"
   TEST (1.75) pero PERDIÓ TRAIN = mirage de multiple-testing.
Path real de DEXE: 2 días calma (34-36), pico +14% (squeeze), luego
-44% en 12h a las 09:00 UTC y SIGUIÓ cayendo a -83%. No rebotó — por eso
un stop la habría ayudado. Pero de 6-13 disparos DEXE fue 1; los demás
rebotaron. No se distingue en el momento.
VEREDICTO: ningún stop rescata A5.1. DEXE estuvo calma antes de reventar
(tail-risk invisible para vol30). Camino constructivo: hipótesis
tail_risk_sizing (achicar en la ENTRADA a monedas de cola gruesa, no
salir durante el crash). NO reproponer stops en ninguna forma.

## tail_risk_sizing — RECHAZADA (2026-07-23)

Penalizar en la entrada monedas de cola gruesa (peor día 365d) además del
vol-sizing. TRAIN/TEST 29 monedas: base $887/0.66 vs tail $867/0.65; peor
trade IGUAL -$62.8; drawdown ~igual. Lección: no se puede dimensionar para
el PRIMER crash de una moneda (el tail histórico es ciego a un crash
virgen); para reincidentes, el vol-sizing (vol post-crash enorme) o el
flip del cruce ya actúan. La protección real ya está desplegada:
vol-sizing + tope estructural ~3% por moneda + cruce de salida. El riesgo
de primer-crash es irreducible y ya acotado al ~3% del libro. NO reproponer.

## Búsqueda multi-agente de variantes de A5.1 — 0/13 sobreviven (2026-07-23)

Workflow Ultracode: 13 familias de herramientas técnicas NUEVAS (no en
lista negra) × grillas = 62 combinaciones, sobre A5.1. Protocolo estricto
(elegir por TRAIN, juzgar por TEST) + verificación adversarial
(walk-forward 3-fold + robustez de grilla, agentes instruidos para refutar).
Base: trainSh 1.567 / testSh 0.552.

Familias: adx_filter, donchian, supertrend, bb_width_filter, rsi_filter,
volume_confirm, keltner, aroon_confirm, choppiness_filter, roc_confirm,
ma_slope_filter, donchian_exit, adx_plus_volume (combinación).

Multiple-testing visible en TRAIN: con 62 intentos, varias baten a base por
azar (roc_confirm 1.793, adx_filter 1.599, choppiness 1.597, donchian_exit
1.589, supertrend 1.586). Sólo 2 pasaron el gate preliminar (n_beat_both>=1):
adx_filter y donchian_exit. AMBAS desenmascaradas como MIRAGE:
- adx_filter: gana 2/3 folds pero por márgenes al filo; robustez falla
  (grid_frac 0.11 — sólo 1 de 9 combos bate a base en test, justo el
  optimizado en train). Espejismo clásico.
- donchian_exit: headline pasaría los gates mecánicos (folds 3/3, grid_frac
  1.0) PERO el verificador adversarial lo refutó: corr 0.9992 con base
  (casi un clon, difiere 16.8% de días), edge estadísticamente insignificante
  (t full 1.64, TEST 1.63, per-fold 1.02/1.26/0.72, todos <1.96), inversión
  train/test (mejor en train = peor en test), edge concentrado en outliers
  (top-5 días = 65% del diff). Regime/outlier luck, no edge robusto.

VEREDICTO: 0/13 CONFIRMED. A5.1 sigue en su frontera eficiente — NINGUNA
herramienta técnica (ADX/Donchian/Supertrend/Bollinger/RSI/volumen/Keltner/
Aroon/Choppiness/ROC/pendiente/salida-Donchian/combinaciones) agrega edge
real. Agregarlas sumaría complejidad y riesgo de sobreajuste sin retorno.
LECCIÓN: sin walk-forward + robustez estadística, 2 falsos positivos de 62
(~3%) se habrían aceptado como reales — y donchian_exit habría pasado hasta
los gates mecánicos. La verificación adversarial (no sólo el umbral) es lo
que la mató. NO re-testear estas 13 familias sin datos nuevos.
