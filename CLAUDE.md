# CLAUDE.md

Contexto de este proyecto para retomarlo con Claude Code. Este archivo
es para vos, Claude: leelo antes de tocar código en este repo.

## Qué es esto

Sistema de pronósticos para el prode del **Torneo Clausura 2026** de la
Liga Profesional Argentina (fútbol). El dueño del repo completa a mano
un prode con amigos y quiere un sistema que le calcule el pronóstico de
cada partido de la fecha, en vez de tirar resultados al azar.

No es una herramienta de apuestas ni maneja dinero — es un juego de
predicción tipo quiniela/prode entre amigos.

## Modelo estadístico usado

Distribución de **Poisson** sobre goles esperados de cada equipo:

```
exp_goles_local     = ataque_local * defensa_visitante * promedio_liga * ventaja_localía
exp_goles_visitante  = ataque_visitante * defensa_local * promedio_liga
```

Con matriz de probabilidad para goles 0-6 por lado, de donde se derivan:
- probabilidad de 1 / X / 2,
- marcador más probable (la celda de mayor probabilidad en la matriz).

Sobre esa base se aplican tres ajustes, todos intencionalmente chicos
para no tapar la diferencia real de nivel entre los equipos:

1. **Posición en la tabla de zona** — un pequeño empuje si hay
   diferencia de posición entre los rivales.
2. **`stakes_multiplier()`** (factor de "necesidad de puntos"),
   combina:
   - **Descenso** (tabla de promedios): boost fuerte si el equipo
     está en zona de descenso.
   - **Playoffs de zona** (`playoff_zone_stakes()`): cada zona tiene
     15 equipos, clasifican los primeros 8. Si el equipo está a ±6
     puntos del 8° puesto → pelea directa, boost. Si está
     matemáticamente eliminado de playoffs → resta (posible
     rotación de titulares, "dead rubber").
   - **Copa internacional** (tabla anual): boost si está en la pelea
     por el último cupo (posiciones 5 a 9 de la tabla anual).
   - El total queda topeado en ±12% (`max_boost * 1.5`).

Estos son ajustes heurísticos, no salidos de un fit estadístico contra
resultados históricos — si en algún momento se junta suficiente
historial en `matches`, valdría la pena calibrar los pesos (`max_boost`,
el 0.7 y 0.6 de los sub-factores, el ±6 puntos del corte de playoffs)
contra resultados reales en vez de dejarlos a ojo.

## Arquitectura / archivos

```
prode_db.py           -> capa de datos (SQLite). Esquema, imports desde
                          CSV, y las funciones de consulta:
                            home_form() / away_form()   -> rendimiento
                              de local/visitante por separado, últimos
                              N partidos (N = LAST_N_GAMES)
                            zone_position()
                            playoff_zone_stakes()
                            stakes_multiplier()
prode_predictor.py     -> script principal. Flujo:
                            1) sync_recent_results(): pide a la API
                               SOLO los resultados de los últimos
                               DAYS_BACK días y los guarda en matches
                               (evita re-pedir historial completo)
                            2) get_upcoming_fixtures(): pide a la API
                               el fixture de los próximos DAYS_AHEAD
                               días (esto sí se pide siempre, puede
                               cambiar por reprogramaciones)
                            3) predict_match(): calcula con los datos
                               YA guardados en la base (no pega más a
                               la API para esto)
                            4) build_report() + send_telegram()
plantilla_*.csv         -> CSV de carga inicial manual (ver README.md)
.env.example            -> template de variables de entorno
requirements.txt        -> requests, python-dotenv
README.md               -> instrucciones paso a paso para un humano
```

## Decisión de diseño clave: por qué SQLite local

El dueño del proyecto quería evitar gastar cuota de la API de datos
(plan free, ~100 req/día) recalculando todo desde cero cada semana. La
solución fue:
- **Carga inicial a mano** (CSV) de todo el historial ya jugado, sin
  gastar ni un request de API.
- **Sync incremental**: cada corrida semanal solo trae lo nuevo
  (resultados de la última fecha + fixture de la próxima), y el resto
  de los cálculos usan lo que ya está en `prode.db`.
- Las 3 tablas de posiciones (zona, promedios, anual) se actualizan a
  mano con los CSV una vez por semana porque cambian poco y no vale la
  pena automatizar ese scraping todavía.

## Fuente de datos externa

**API-Football** (https://www.api-football.com/), plan free. Falta
confirmar el `LEAGUE_ID` correcto para la Liga Profesional Argentina /
Clausura 2026 (hay un valor placeholder en el código, `128`, sin
verificar contra la API real — es lo primero para chequear si algo no
trae datos).

## Estado actual / qué falta

- [ ] Confirmar `LEAGUE_ID` real contra `GET /leagues?country=Argentina`.
- [ ] Cargar el historial real completo de resultados del Clausura 2026
      en `plantilla_resultados.csv` (por ahora solo tiene 5 filas de
      ejemplo).
- [ ] Cargar las 3 tablas reales completas (zona A y B completas, no
      parciales — `playoff_zone_stakes()` necesita las 15 filas de
      cada zona para calcular bien el corte del 8° puesto).
- [ ] Crear el bot de Telegram y probar el envío real.
- [ ] Configurar el GitHub Action semanal (hay un ejemplo de workflow
      comentado al final de la primera versión del script — ver
      historial del proyecto / pedírselo a Claude si no está).
- [ ] Nunca calibrado contra resultados reales — considerar guardar,
      por cada fecha, el pronóstico emitido junto con el resultado real
      una vez jugado, para poder medir accuracy con el tiempo (podría
      ser una tabla nueva `predictions_log`).

## Convenciones del proyecto

- Todo el código y los comentarios están en español (así lo pidió el
  dueño del proyecto).
- Los nombres de equipos en la base son los que devuelve la API o los
  que carga el usuario a mano en los CSV — no hay normalización de
  nombres todavía (ej. "Gimnasia (Mza)" vs el nombre exacto que use
  API-Football podrían no matchear: si eso pasa, se crean como equipos
  duplicados. Si se automatiza más, conviene agregar un mapeo de alias).
- Los ajustes de motivación (`stakes_multiplier`) están pensados para
  ser chicos y editables, no para dominar el modelo. Al tocarlos,
  mantené esa filosofía.
- Nunca commitear `.env` ni ninguna key real (ver `.gitignore`).

## Cómo correrlo (resumen rápido)

```
pip install -r requirements.txt
cp .env.example .env   # completar con keys reales
python prode_db.py --init
python prode_db.py --import-csv
python prode_predictor.py
```

Ver `README.md` para el detalle paso a paso.