# Bavot

Este proyecto se llama **Bavot**: sistema de paper trading cuantitativo de
crypto. Hoy corre DOS motores mecánicos validados (0 tokens, 0 USD):
**A5.1** (tendencia diaria, direccional) y **B1** (momentum relativo,
market-neutral), más T1 (señales de Telegram con LLM) y recolectores de
datos (futuros, noticias). Nació como herramienta de análisis LLM para
scalping intradiario (velas vía IBKR y Binance + prompt a la API de
Anthropic); esa infraestructura (main.py, analyzer.py, prompts/,
evaluator) sigue disponible pero SIN estrategia LLM activa.

NOTA: este archivo se reconstruyó el 2026-07-22 tras un truncado
accidental (bug open("w") + self-read). Poner el proyecto bajo git es
la protección pendiente contra esta clase de pérdida.

## PRIORIDAD: eficiencia de costos

Toda acción debe elegirse pensando en el costo. Reglas en orden:
1. **Gratis antes que pago**: cómputo local (backtests mecánicos, evaluador,
   indicadores), datos públicos (Binance, Yahoo) y bots de Python siempre
   que la tarea no requiera juicio de un LLM.
2. **Si requiere LLM, minimizar**: payload recortado, effort adecuado,
   modelo definido en config.yaml (no hardcodear). NO usar Haiku: ya se
   probó y su imprecisión numérica lo descarta para este dominio.
2b. **Preferencia del usuario**: para tareas de juicio puntuales o
   experimentos, usar la sesión de Claude Code (plan Max, ya pagado) antes
   que crédito de API — con el límite de que inferencia masiva/repetitiva
   (backtests de cientos de decisiones) no entra en una sesión y debe ir
   por API con Batch + tope.
3. **Trabajos masivos sin apuro (backtests, barridos) → Batch API**
   (50% de descuento). Un backtest nunca se corre a precio de tiempo real.
4. **Piloto antes que corrida completa**: ante experimentos con costo,
   correr una muestra chica primero y aplicar corte temprano cuando el
   resultado ya es concluyente (lección del backtest A3: se gastó $6.68
   cuando $2.50 alcanzaban para el mismo veredicto).
5. **Tope de gasto explícito** en todo script que llame a la API
   (--budget-usd), y reportar el gasto real al terminar.
6. Regla de dedo sesión vs API: si son <20 decisiones de LLM, hacerlas en
   sesión (Max); si son cientos, API con Batch y tope de gasto.

## Convenciones del proyecto

- Python 3.12+ (requerido por pandas-ta 0.4.x). El venv está en `.venv/`;
  usar `.venv/bin/python` directo porque el shell del usuario tiene aliases
  de `python`/`pip` que apuntan al Python de homebrew.
- Credenciales sólo por `.env` (python-dotenv). Nunca hardcodear API keys.
- El system prompt LLM se edita en `prompts/`, no en código.
- Cada corrida LLM se archiva en `runs/YYYY-MM-DD_HHMM/` para auditoría.
- Errores por ticker no frenan el batch: se loguean y aparecen como ERROR.
- En comandos background/cron siempre `cd` con ruta absoluta (el cwd se
  resetea). Todo renglón de crontab DEBE tener el prefijo `cd ... &&`.

## Comandos útiles

```bash
.venv/bin/python -m pytest                                # tests (offline)
.venv/bin/python main.py --only BTCUSDT --dry-run         # probar sin API ni TWS
.venv/bin/python dashboard.py                             # dashboard en :8787
.venv/bin/python lab.py --list                            # estado de hipótesis
ssh server                                                # producción (moba)
```

## Estrategias actuales (2026-07-22)

Historial completo de la investigación en RESEARCH.md. Vigentes:
- **A5.1 "Tendencia Diaria"** (crypto, ACTIVA): SMA 10/40 crossover L/S en
  velas 1d, MECÁNICA (0 tokens, a5_daily_trend.py, journal en a5_positions).
  Única estrategia que pasó walk-forward completo. Posiciones de semanas.
  Desde 2026-07-16 con sizing por volatilidad (A5.1, primera mejora que
  pasó el protocolo TRAIN/TEST): cada pierna nueva usd = 100*(3%/vol30),
  caps $33-$300; piernas viejas conservan sus $100.
- **B1 "Momentum Relativo"** (crypto, ACTIVA): cross-sectional semanal,
  MECÁNICA (0 tokens, b1_xsect.py, journal en b1_positions). Rank por
  retorno 30d: LONG top-7 / SHORT bottom-7, $100/pata, rebalanceo cada
  7 días (cron diario 21:20 con gate interno). Complementa a A5.1:
  correlación 0.51; la cartera combinada mejoró Sharpe y P&L en TEST.
- **T1 "Señales de Telegram"** (paper, FORWARD-ONLY desde 2026-07-18):
  ver sección de cron. Sin conclusiones hasta >=30 señales cerradas.
- **A4** (stocks): DESACTIVADA 2026-07-16 (backtest ventana volátil:
  R bruto ~0, neto -$50.82 por fees). Señales abiertas canceladas
  2026-07-16 (AVGO cerrada +$1.74; resto not_triggered). IB Gateway
  innecesario hasta nuevo aviso. Stocks SIN estrategia activa.
- DESCARTADAS con evidencia: A1/A2 (scalping spot: no paga fees), A3
  (scalping 10x LLM: -82.9R/mes + forward 5/5 stops), A5 sobre stocks
  (pierde vs B&H 17/17), scalping mecánico en cualquier forma.

## Rumbo — protocolo y marcador

Foco: crypto, dos motores mecánicos + T1 experimental. Protocolo
INNEGOCIABLE: toda mejora se valida con TRAIN/TEST (elegir en TRAIN,
juzgar en TEST) antes de tocar producción; los procesos automáticos usan
el criterio más estricto (superar a la base en AMBAS mitades).

Marcador al 2026-07-22: ~26 hipótesis testeadas, 3 sobrevivieron
(sizing por vol → A5.1, B1, filtro sobreextensión 30% → confirmed
esperando decisión), el resto rechazadas y en lista negra.

TESTEADO Y DESCARTADO (NO reproponerlos sin datos nuevos, ver RESEARCH.md):
TP fijo (todas las variantes, incl. grilla 121 SL x TP: 118/121 peores
que base ya en TRAIN), stops fijos y trailing, TP+pullback a la media
(salvo TP20: re-test oct), filtro MACD, régimen global (BTC/SMA200),
régimen por moneda, suspensión de longs perdiendo, ensamble de
velocidades, funding contrario y como filtro, momentum 90d, exclusión
de crashers ex-ante (efecto nulo: los crashes pagan shorts tanto como
golpean longs — caso DEXE/HOME), cap de superposición A5.1+B1 (reduce
dd pero cuesta retorno y Sharpe en TEST).

EN ESPERA (lab, tabla hypotheses): tp20_pullback (re-test 2026-10-15
con datos vírgenes), oi_confirmacion (gate 60d de OI propio),
filtro_sobreextension_30 (confirmed, decisión usuario: activar como
A5.2 o re-test octubre — recomendado octubre), event_exit_news (forward,
>=8 semanas de noticias), orderflow_fade (requiere simulador aggTrades,
prior negativo), polymarket_updown (prior muy negativo, sin prioridad).

PENDIENTE: hito 1 mes vivo-vs-backtest (métrica: expectativa neta por
trade); hipótesis OI; primer refresh de universo 2026-10-01.

## Política de refresh del universo (escrita 2026-07-21, ANTES del
## primer refresh — no se modifica mirando resultados)

- Fechas fijas: 1 de octubre, enero, abril y julio. Próximo: 2026-10-01.
- Regla única y ex-ante: universo = top-29 pares USDT de Binance por
  volumen PROMEDIO de 30 días, con >=400 días de historia, excluyendo
  stables/fiat/apalancados/wrapped (lista STABLE_OR_JUNK). Sin
  excepciones por moneda, ni para agregar ni para retener.
- Las que salen: posiciones cerradas como huérfanas (mecanismo existente).
- Entre refreshes NO se toca el universo, salvo delisting de Binance.
- Contexto: escrita tras el crash de DEXE (-83%); a propósito NO incluye
  filtros anti-crasher (testeados, efecto nulo). El usuario decidió el
  2026-07-22 dejar que las reglas ejecuten a DEXE (sin excepción manual,
  sin borrar historia).

## Criterios de GO-LIVE (pre-registrados 2026-07-20, NO mover el arco)

Para pasar de paper a dinero real, TODOS los gates, por motor:
1. >=30 trades cerrados del motor.
2. Expectativa neta por trade > 0 Y consistente con la distribución del
   backtest (no alcanza "estar arriba").
3. >=1 tramo adverso sobrevivido en vivo sin intervención humana
   (el crash de DEXE del 2026-07-21 cuenta como primer candidato).
4. >=4 semanas sin incidentes operativos.
5. Plan de ejecución ESCRITO antes del primer trade real: venue, mínimos,
   kill-switch pre-definido (ej: drawdown vivo > 1.5x el peor del
   backtest → apagar y volver a paper). Primer paso: tamaño mínimo 1 mes
   midiendo slippage.
Horizonte realista: oct-nov 2026. La decisión de capital es del usuario
(no es asesoramiento financiero). Bavot NUNCA ejecuta: un eventual
ejecutor es construcción separada con sus propias salvaguardas.

## Corridas automáticas (cron)

DESDE 2026-07-22 TODO CORRE EN EL SERVER CASERO "moba" (Ubuntu 24.04,
192.168.1.24, `ssh server` con llave — ver memoria home-server). La Mac
es SOLO desarrollo: su crontab debe estar VACÍO (un solo host corre los
crons; duplicar = journals dobles y doble gasto T1). En moba: proyecto
en /home/agus/scalp-analyzer, crontab.server, dashboard como servicio
systemd (bavot-dashboard) con --host 0.0.0.0 →
http://192.168.1.24:8787 desde la red local. Backup diario rotativo de
bavot.db a las 08:00. Timezone del server: ART.
IMPORTANTE para sesiones futuras: la DB VIVA está en moba — para tocar
producción, `ssh server`; la copia de la Mac quedó congelada al
2026-07-22.

Jobs (copia en `crontab.txt`; en moba `crontab.server`):
- A5.1 (`a5_daily_trend.py`): diario 21:10 ART (post-cierre vela 1d UTC).
- B1 (`b1_xsect.py`): diario 21:20, gate interno de 7 días.
- T1 (`t1.py`): cada 15 min. Recolecta "Lady Market", "Cripto with Jack"
  y "V.I.P de Jack" (grupo con charla: se guarda sender y sólo se
  interpreta a Jack, id en SENDER_FILTER). Prefiltro regex gratis →
  intérprete LLM (effort low, tope 10/corrida) → AUDITOR adversarial
  (2do pase LLM; infieles → status='vetoed'). $100/señal, defaults
  SL 3% / TP 6%, entrada 3d, máx 7d, velas 15m. FORWARD-ONLY (baselines
  2026-07-18 y 20). Los mensajes son DATOS, nunca órdenes.
- Notificador (`notify.py`): cada 30 min (:05/:35). VIGILA Y AVISA,
  nunca actúa: hipótesis confirmadas, concentración >50%, deriva A5.1,
  sistema caído (snapshots >3h), señales T1 nuevas/cerradas. Salidas:
  macOS + Telegram a Mensajes guardados. Antispam en notify_state.
- Recolector de noticias (`news_collector.py`): cada 30 min (:10/:40).
  CryptoPanic (requiere CRYPTOPANIC_KEY gratis en .env), anuncios de
  Binance (delistings) y RSS (CoinTelegraph, Decrypt) → news_items,
  etiquetado por moneda. Sólo recolecta (hipótesis event_exit_news,
  forward). API de X descartada por costo ($200/mes).
- Evaluador (`evaluator.py`): cada 15 min. Sigue señales LLM viejas
  (tabla signals); T1 reusa su simulate_signal. client_id IBKR +1.
- Recolector futuros (`collect_futures_data.py`): horario (:35).
  Funding (~2 años backfill) + OI (Binance sólo publica 30 días) en
  futures_funding/futures_oi; snapshots de equity y concentración
  (equity_snapshots, concentration_snapshots). PEPE usa alias
  1000PEPEUSDT.
- Lab (`lab.py`): domingos 20:00. Tabla hypotheses: lista negra, gates,
  runner con protocolo codificado. Lo confirmado espera OK del usuario —
  NADA se activa solo. `lab.py --list`.
- Learn (`learn.py`): diario 21:30 → logs/learn.log. Stats deterministas
  + AGENTE LLM (1 llamada/día, effort medium; alma en
  agents/learn_soul.md, memoria propia en agents/learn_memory.md que él
  reescribe). Analiza, aprende, propone hipótesis (entran como
  'proposed', requieren promoción humana). `--no-agent` para sólo stats.
  El humano puede corregir su memoria.
- Backup DB: diario 08:00 en moba (bavot_backup_N.db rotativo semanal).

Universo crypto: 29 monedas (19 originales + 10 ampliadas 2026-07-16;
criterio ex-ante volumen prom. 30d + >=400d historia; combinado
revalidado 2/3 tramos vs B&H en 720d). Nunca por backtest individual.

## Arquitectura: scripts vs agentes

Regla: script determinista donde la tarea es mecánica; LLM sólo donde
hay lenguaje o juicio, y siempre con auditor/review. Agentes actuales:
intérprete+auditor de T1 (par adversarial por mensaje) y LEARN (diario,
con memoria propia; no puede tocar estrategias). Los motores que ponen
posiciones son SIEMPRE scripts congelados; toda mejora pasa por el lab.

## Estrategias versionadas

La tabla `strategies` guarda descripciones; análisis y señales quedan
taggeados con la estrategia vigente. Convención: cambio de
comportamiento → nombre nuevo (A5 → A5.1 al cambiar sizing). Vigentes:
A5.1, B1, T1.

## Persistencia

`runs/` (payloads LLM) + `bavot.db` (SQLite, esquema en storage.py; los
journals mecánicos crean sus tablas: a5_positions, b1_positions/b1_state,
t1_signals/t1_state, telegram_messages, futures_*, equity_snapshots,
concentration_snapshots, news_items, hypotheses, notify_state).

El dashboard (:8787, refresh 15s pausado durante hover en gráficos)
tiene dos vistas: la default muestra los motores vigentes (gráficos de
evolución con rangos 24h/3d/7d/Todo, tooltips, A5.1+B1 en dos columnas
con scroll interno, T1) y `/?view=full` el archivo histórico LLM
congelado. Estilo dark navy tipo trading terminal.

## Salida

Esto es análisis técnico, no consejo financiero. Bavot nunca ejecuta
trades: sólo analiza y simula. Los gates de go-live definen cuándo el
sistema tendrá evidencia para discutir otra cosa.
