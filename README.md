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

## Paso 1 — Carga inicial (una sola vez, a mano, sin gastar API)

1. Abrí los `plantilla_*.csv` y completalos con los datos reales:
   - **`plantilla_resultados.csv`**: cargá todos los partidos ya jugados
     del Clausura 2026 (fecha 1 a la última jugada). Es la parte que
     más tiempo lleva, pero la sacás directa de la tabla de resultados
     de canchallena.com o ligaprofesional.ar, copiando y pegando.
   - **`plantilla_tabla_zona.csv`**: la tabla de posiciones de zona A y B,
     tal cual figura hoy.
   - **`plantilla_tabla_promedios.csv`**: la tabla de promedios (la que
     define el descenso). Marcá `en_zona_descenso=1` para los últimos
     2-3 equipos.
   - **`plantilla_tabla_anual.csv`**: la tabla anual (la que define copas).
     Marcá `en_zona_copa=1` para los que hoy clasificarían.

2. Corré:
   ```
   python prode_db.py --init
   python prode_db.py --import-csv
   ```
   Esto crea `prode.db` y carga todo. De acá en adelante, ya no hace
   falta tocar la API para tener el historial: está guardado.

## Paso 2 — Configurar las keys

```
pip install -r requirements.txt
cp .env.example .env
# editá .env y completá tus valores reales
```

El `.env` nunca se sube al repo (ya está en `.gitignore`). `prode_predictor.py`
lo carga automáticamente al arrancar con `python-dotenv`.

Si preferís no usar `.env` (por ejemplo corriendo en GitHub Actions), las
mismas variables funcionan como variables de entorno / Secrets:
`API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

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

## Mantenimiento semanal manual (2 minutos)

Las tablas de zona, promedios y anual cambian solas cada fecha, pero no
vale la pena automatizarlas todavía: son pocos datos y los sacás rápido
de la tabla oficial. Actualizá los CSV correspondientes y volvé a correr:

```
python prode_db.py --import-csv
```

(los `INSERT ... ON CONFLICT` hacen que se actualice en vez de duplicar)

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