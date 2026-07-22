# Memoria de LEARN (el agente la reescribe cada día)

## Estado (2026-07-22, día 4)
- A5.1: 9/30 cerradas, P&L -28.19 USD, wr 11% (backtest esperado 35-50%). Perfil sano preservado: ganancia prom +0.02 vs pérdida prom -3.53. n bajo, sin conclusiones. Estrategia validada, NO se toca hasta n>=30. Todavía sin ninguna ganancia >+5.
- Bucket general (A2/A3/A4/T1): sigue clavado en 13 cierres, ya van 2+ días sin ningún cierre nuevo. Empieza a pesar como falla operativa probable (posible corte en la fuente de señales), no sólo muestra chica. Vigilar 1-2 días más antes de escalarlo con más fuerza.
- Concentración entre motores: el foco cambió — HOME ya no aparece como dominante (bajó de la lista), pero surgió DEXEUSDT como nueva moneda repetida en ambos motores (A5.1+B1), -165.90 USD = 273% del flotante del libro. Mismo patrón de siempre (motores coincidiendo en un nombre), sólo cambió el nombre. El hilo 'HOME se desconcentra y se cierra' queda inconcluso — nunca vimos el cierre/rebalanceo real, así que no se puede sacar lección de P&L realizado ahí. Abro hilo nuevo para DEXEUSDT con la misma pregunta pendiente: ¿se cierra/rebalancea y qué P&L deja?
- B1: estable, exposición neta ~0, diseño neutral funcionando (14 abiertas).
- 'stock' n=4, P&L -4.12, R +1.5 — sin cambio, sigue siendo anécdota de muestra ínfima.

## Hilos a seguir
- T1/bucket general en silencio: 13 cierres estancados. Si mañana sigue en 13, escalar explícitamente como posible falla operativa (revisar canales/config), separado de la narrativa 'muestra chica'.
- Concentración: seguir DEXEUSDT día a día (si sigue subiendo % o si se cierra/rebalancea, primero registrar el P&L real de ESE evento como test de la lección del 18/7, ya que HOME nunca lo dio).
- A5.1 wr real vs backtest (11% vs 35-50%, n=9): esperar n>=30. Vigilar que no llegue a n=15-20 sin ninguna ganancia >+5 — si eso pasa, escalar preocupación pese a validación previa.

## Predicciones para chequearme
- Si el bucket general sigue en 13 cierres un día más, marco falla operativa probable en la próxima entrada (no ruido de estrategia).
- Si DEXEUSDT se cierra/rebalancea con P&L fuertemente negativo, confirma que la concentración es riesgo real recurrente (no anécdota de HOME); si es neutral/positivo, matiza la lección para ambos casos.
- Si A5.1 llega a n=15-20 sin ninguna ganancia >+5, escalar preocupación aun con validación previa.

(sin hipótesis nuevas hoy: no hay datos nuevos —no derivados del precio de cierre— que las motiven)
## Caso DEXE (anotado por el humano, 2026-07-21)
- DEXE -83% en un día con AMBOS motores long $100 → ~-$164 flotante.
  Espejo exacto de HOME (+$63 con ambos short). La superposición
  A5.1+B1 duplica las colas EN AMBOS SENTIDOS.
- Testeado ya (no re-proponer): excluir crashers ex-ante da efecto NULO
  (los crashes pagan shorts tanto como golpean longs). Ver RESEARCH.md.
- Hipótesis abierta en el lab: cap_superposicion_motores (esperando
  simulador de cartera combinada). Mi trabajo: medir en vivo cuántas
  veces la superposición paga vs cobra, con los snapshots de
  concentración — ese dato decidirá si el cap merece testearse.
