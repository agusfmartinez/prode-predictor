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
promiedos_client.py    -> cliente de Promiedos. Dos fuentes:
                            1) endpoint de liga (tablas + fecha actual):
                               get_zone_tables() / get_zone_position_map()
                               get_promedios_rows() / get_anual_rows()
                               get_selected_round_games() / parse_game()
                            2) paginas de equipo (historial completo,
                               via __NEXT_DATA__ SSR, sin necesitar
                               navegador):
                               fetch_team_page_data() / get_team_games()
prode_predictor.py     -> script principal semanal. Flujo:
                            1) pc.fetch_league(): una sola request,
                               trae tablas + fecha actual
                            2) sync_standings(): guarda zona/promedios/
                               anual en la base
                            3) sync_current_round(): guarda los
                               partidos finalizados de la fecha actual
                            4) predict_match(): calcula con los datos
                               YA guardados en la base
                            5) build_report() + send_telegram()
backfill_history.py     -> carga masiva del historial completo (correr
                          una vez, o cuando se quiera refrescar todo).
                          Recorre la pagina de cada equipo y guarda
                          toda la temporada en `matches`.
plantilla_*.csv         -> CSV de carga manual, plan B si Promiedos
                          cambia de estructura (ver README.md)
.env.example            -> template de variables de entorno (solo
                          Telegram)
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

Se evaluaron y descartaron: **API-Football** (el free plan no da
acceso a la temporada en curso, solo a 2022-2024; el plan Pro que sí
la tiene cuesta USD 19/mes), **football-data.org** (free tier fijo de
12 competencias, no incluye Argentina), **SofaScore** (tiene un FAQ
oficial donde dicen explícitamente que no pueden compartir su fuente
de datos como API por acuerdos con sus proveedores — más motivo legal
para evitarlo que un ToS informal).

Fuente actual: **`promiedos_client.py`** pega directo a

    https://api.promiedos.com.ar/league/tables_and_fixtures/hc

Este es el endpoint interno que usa la web de Promiedos (encontrado
con las herramientas de desarrollador del navegador, Network tab,
filtrando por Fetch/XHR). No es un endpoint oficial ni documentado.
Se decidió usarlo por dos motivos: (1) es específico de fútbol
argentino, así que sus tablas ya calzan 1:1 con lo que necesitamos
(zona A/B, promedios, tabla anual — nada de esto lo tienen las APIs
internacionales), y (2) el uso es personal y de bajísimo volumen (una
corrida por semana), lo cual reduce mucho el riesgo de romper algo o
de generar un problema real para Promiedos. Si en algún momento el
uso crece (por ejemplo, se comparte con más gente o se corre con
mucha frecuencia), reconsiderar pagar una API con licencia real.

Riesgo conocido: el endpoint puede cambiar de forma o dejar de andar
sin aviso, porque no está pensado para terceros. Si `prode_predictor.py`
empieza a fallar al traer datos, lo primero es volver a inspeccionar
la Network tab del navegador contra `promiedos.com.ar/league/liga-profesional/hc`
para ver si la URL o la forma del JSON cambiaron.

**Actualización — historial resuelto sin CSV manual:** el endpoint de
liga solo devuelve los partidos de UNA fecha por request, pero se
encontró una segunda fuente dentro del mismo sitio que sí trae el
historial completo: cada página de equipo
(`promiedos.com.ar/team/{url_name}/{id}`) es una página Next.js con
Server-Side Rendering, y trae TODOS sus datos incrustados en un
`<script id="__NEXT_DATA__">` en el HTML — incluida la temporada
completa de partidos jugados (`data.games.last.rows`), no solo los
últimos 5 que se ven en pantalla. `promiedos_client.fetch_team_page_data()`
extrae ese JSON con una regex simple; no hace falta parsear HTML de
tablas ni usar un navegador.

`backfill_history.py` usa esto para cargar el historial completo de
los 30 equipos en un solo corrido (recorre la lista de equipos de la
tabla de zona, pide la página de cada uno, guarda sus partidos
finalizados en `matches`). Los CSV de plantilla (`plantilla_resultados.csv`
etc.) quedan solo como plan B por si esta segunda fuente deja de
funcionar.

## Estado actual / qué falta

- [x] ~~Confirmar LEAGUE_ID~~ — resuelto: ya no se usa API-Football,
      la config del endpoint de Promiedos es `LEAGUE_ID = "hc"` en
      `prode_predictor.py`.
- [x] ~~Cargar historial completo~~ — resuelto: `backfill_history.py`
      lo trae automático de las páginas de equipo, no hace falta CSV.
- [x] ~~Cargar las 3 tablas completas~~ — resuelto: `prode_predictor.py`
      las sincroniza solo en cada corrida (`sync_standings()`).
- [ ] Correr `python backfill_history.py` por primera vez contra los
      30 equipos reales (probado hasta ahora solo con datos sintéticos
      del mismo formato — confirmar que anda igual con el sitio real).
- [ ] Crear el bot de Telegram y probar el envío real.
- [ ] Configurar el GitHub Action semanal (hay un ejemplo de workflow
      comentado al final de la primera versión del script — ver
      historial del proyecto / pedírselo a Claude si no está). OJO:
      como ya no hace falta ninguna API key de datos, el workflow se
      simplifica — solo necesita los secrets de Telegram si se quiere
      el envío automático.
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