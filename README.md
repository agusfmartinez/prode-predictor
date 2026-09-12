# Prode Predictor — Torneo Clausura 2026

Sistema de pronósticos con base de datos local (SQLite) para no gastar
requests de API de más.

## Archivos

- `prode_db.py` — crea la base y maneja todas las consultas (rendimiento
  de local/visitante, posición, factor de necesidad de puntos).
- `prode_predictor.py` — el script que corrés cada semana: sincroniza
  resultados nuevos, trae el próximo fixture, calcula y te manda el
  pronóstico.
- `plantilla_equipos.csv`, `plantilla_resultados.csv`,
  `plantilla_tabla_zona.csv`, `plantilla_tabla_promedios.csv`,
  `plantilla_tabla_anual.csv` — para la carga inicial a mano.

## Paso 1 — Carga inicial del historial (automática, un solo comando)

Ya no hace falta completar los CSV a mano. `backfill_history.py` trae
el historial completo de la temporada de cada equipo directo de su
página en Promiedos (toda la temporada, no solo los últimos 5
partidos) y lo guarda en la base:

```
python prode_db.py --init
python backfill_history.py
```

Esto tarda un par de minutos (30 equipos, con una pausa entre pedido y
pedido para no saturar el sitio). Al terminar, `matches` ya tiene el
historial completo y `home_form()`/`away_form()` van a funcionar bien
desde la primera corrida de `prode_predictor.py`.

Los CSV de plantilla (`plantilla_*.csv`) quedan como plan B: si en
algún momento Promiedos cambia su estructura y `backfill_history.py`
deja de andar, todavía se puede cargar el historial a mano con ellos
(ver el detalle de columnas en cada archivo).

## Paso 2 — Configurar Telegram (opcional, ya no hace falta API key de datos)

```
pip install -r requirements.txt
cp .env.example .env
# editá .env y completá TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID si querés el envío automático
```

Los datos de fútbol ya no vienen de una API paga: `promiedos_client.py`
consulta directamente `https://api.promiedos.com.ar/league/tables_and_fixtures/hc`,
que es el mismo endpoint que usa la web de Promiedos (no es oficial ni
está documentado, pero es gratis y no requiere key — ver `CLAUDE.md`
para el detalle de por qué elegimos esta fuente).

## Paso 3 — Correrlo cada semana

```
python prode_predictor.py
```

Esto:
1. Le pide a la API **solo** los partidos jugados en los últimos 5 días
   (los agrega a la base si no estaban).
2. Le pide a la API el fixture de los próximos 7 días.
3. Calcula cada pronóstico usando lo que ya está guardado en `prode.db`
   (no vuelve a pedir historial completo).
4. Te lo manda por Telegram y lo deja en `ultima_fecha_pronosticos.txt`.

## Mantenimiento semanal

Ya no hace falta nada manual — `prode_predictor.py` actualiza solo las
tablas de zona, promedios y anual en cada corrida (vienen en el mismo
JSON que el fixture). Los CSV de plantilla solo se usan si en algún
momento hay que volver al plan B manual.

## Automatizarlo del todo (que corra solo)

Con `prode.db` ya versionado en un repo de GitHub (¡ojo! si vas a subir
resultados reales del torneo no hay problema, pero nunca subas tus keys
al repo — usá Secrets), el workflow de GitHub Actions que dejé de
ejemplo en `prode_predictor.py` (versión anterior) corre esto cada
jueves sin que vos hagas nada, salvo actualizar los 3 CSV de tablas una
vez por semana.

## Sobre el factor de "necesidad de puntos"

`stakes_multiplier()` en `prode_db.py` le suma hasta un 8% al ataque
esperado de un equipo si está en zona de descenso o peleando el último
cupo a copas. Es intencionalmente chico: la motivación importa, pero no
debería tapar la diferencia real de nivel entre dos equipos. Si con el
tiempo ves que el modelo le pifia sistemáticamente a partidos de mitad
de tabla sin nada en juego, podés bajar `max_boost` a 0.05, o subirlo si
ves que el "achique" siempre gana con lo justo.