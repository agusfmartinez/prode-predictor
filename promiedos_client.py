"""
promiedos_client.py
====================
Cliente para el endpoint real de Promiedos (encontrado con las
herramientas de desarrollador del navegador, no es un endpoint
documentado oficialmente):

    https://api.promiedos.com.ar/league/tables_and_fixtures/{league_id}

Para la Liga Profesional Argentina, league_id = "hc".

Esta unica llamada trae en un solo JSON:
  - tables_groups: las tablas de posiciones. Cada grupo tiene un
    "name" (ej: "Clausura", "Apertura", o "" para las tablas
    especiales) y una lista de "tables" (ej: "Grupo A", "Grupo B",
    "Promedios - Relegation", "Tabla Anual - Tabla Anual").
  - games: el fixture. "games.filters" es la lista de fechas
    disponibles; la fecha marcada con "selected": true trae
    embebida la lista de partidos de esa fecha (jugados y por jugar).

OJO: no hay documentacion oficial de este endpoint. Lo usamos porque
es de bajo volumen (unas pocas requests por semana, uso personal),
pero puede cambiar de forma o de URL sin aviso. Si algo deja de andar,
revisar con las herramientas de desarrollador del navegador de nuevo
(ver instrucciones que te pase antes).
"""

import re
import json
import requests

BASE_URL = "https://api.promiedos.com.ar/league/tables_and_fixtures"  # ya no se usa, ver nota abajo
LEAGUE_PAGE_BASE = "https://www.promiedos.com.ar/league"
TEAM_PAGE_BASE = "https://www.promiedos.com.ar/team"

# Headers que imitan un pedido hecho desde el navegador, apuntando a
# promiedos.com.ar como origen. Sin esto, la API devuelve una
# respuesta vacia ({}) -- parece chequear de donde viene el pedido en
# vez de solo aceptar cualquier cliente HTTP.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Referer": "https://www.promiedos.com.ar/",
    "Origin": "https://www.promiedos.com.ar",
}

# La pagina de equipo (promiedos.com.ar/team/{url_name}/{id}) esta
# armada con Next.js y trae TODOS sus datos incrustados en un
# <script id="__NEXT_DATA__">...</script> en el HTML. Ahi esta el
# historial completo de la temporada (no solo los ultimos 5 partidos
# que se ven en pantalla) y los proximos partidos. Esto se descubrio
# mirando "Ver codigo fuente de la pagina" en el navegador.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

# Colores de "destino" que usa Promiedos para marcar las zonas de la
# tabla anual y de promedios. Los vimos en la respuesta real; si
# Promiedos los cambia, esto hay que actualizarlo.
COLOR_DESCENSO = "#E61034"
COLOR_LIBERTADORES_CAMPEON = "#0CF737"
COLOR_LIBERTADORES = "#F5CB25"
COLOR_SUDAMERICANA = "#23EBE4"
COPA_COLORS = {COLOR_LIBERTADORES_CAMPEON, COLOR_LIBERTADORES, COLOR_SUDAMERICANA}


def fetch_league(league_id="hc", url_name="liga-profesional", timeout=20):
    """Trae los datos de la liga leyendo la pagina publica
    (www.promiedos.com.ar/league/{url_name}/{league_id}) y extrayendo
    el bloque __NEXT_DATA__, en vez de pegarle al subdominio
    api.promiedos.com.ar directo.

    Se cambio de estrategia porque api.promiedos.com.ar devuelve una
    respuesta vacia ({}) a pedidos hechos con requests/Python, incluso
    con headers de navegador -- probablemente por deteccion de bots a
    nivel de infraestructura (fingerprint de la conexion, no solo
    headers). El subdominio www., en cambio, sirve HTML renderizado
    del lado del servidor (SSR) y no tiene ese problema: es la misma
    tecnica que ya usamos para las paginas de equipo."""
    url = f"{LEAGUE_PAGE_BASE}/{url_name}/{league_id}"
    r = requests.get(url, timeout=timeout, headers=_HEADERS)
    r.raise_for_status()
    match = _NEXT_DATA_RE.search(r.text)
    if not match:
        raise ValueError(
            f"No se encontro __NEXT_DATA__ en {url}. "
            "Es posible que Promiedos haya cambiado la estructura de la pagina."
        )
    payload = json.loads(match.group(1))
    return payload["props"]["pageProps"]["data"]


# ----------------------- TABLAS DE POSICIONES -----------------------------

def _row_to_dict(row):
    """Convierte una fila de tabla (values + entity) a un dict plano."""
    values = {v["key"]: v["value"] for v in row.get("values", [])}
    team = row["entity"]["object"]
    out = {
        "position": row["num"],
        "team_id": team["id"],
        "team_name": team["name"],
        "team_short_name": team.get("short_name", team["name"]),
        "team_url_name": team.get("url_name"),
        "destination_color": row.get("destination_color"),
    }
    out.update(values)
    return out


def find_table(data, group_name_contains, table_name_contains):
    """Busca una tabla especifica dentro de tables_groups por texto
    parcial en el nombre del grupo y el nombre de la tabla (sin
    importar mayusculas)."""
    for group in data.get("tables_groups", []):
        if group_name_contains.lower() not in (group.get("name") or "").lower():
            continue
        for table in group.get("tables", []):
            if table_name_contains.lower() in (table.get("name") or "").lower():
                return table
    return None


def get_zone_tables(data, stage_name="Clausura"):
    """Devuelve un dict {nombre_de_tabla: [filas]} para las tablas de
    zona de la etapa pedida (ej: {"Grupo A": [...], "Grupo B": [...]})."""
    out = {}
    for group in data.get("tables_groups", []):
        if (group.get("name") or "").strip().lower() != stage_name.lower():
            continue
        for table in group.get("tables", []):
            rows = [_row_to_dict(r) for r in table["table"]["rows"]]
            out[table["name"]] = rows
    return out


def get_promedios_rows(data):
    table = find_table(data, group_name_contains="", table_name_contains="Promedios")
    if not table:
        return []
    rows = [_row_to_dict(r) for r in table["table"]["rows"]]
    for r in rows:
        r["en_zona_descenso"] = 1 if r.get("destination_color") == COLOR_DESCENSO else 0
    return rows


def get_anual_rows(data):
    table = find_table(data, group_name_contains="", table_name_contains="Tabla Anual")
    if not table:
        return []
    rows = [_row_to_dict(r) for r in table["table"]["rows"]]
    for r in rows:
        r["en_zona_copa"] = 1 if r.get("destination_color") in COPA_COLORS else 0
    return rows


def get_zone_position_map(data, stage_name="Clausura"):
    """Devuelve {team_id: (zona, posicion)} para poder guardar la
    posicion de zona de cada equipo sin importar el nombre exacto de
    la tabla (Grupo A/B o Zona A/B segun la etapa)."""
    out = {}
    for table_name, rows in get_zone_tables(data, stage_name).items():
        zone_letter = table_name.strip()[-1]  # "Grupo A" -> "A"
        for r in rows:
            out[r["team_id"]] = (zone_letter, r["position"])
    return out


# ----------------------- FIXTURE / PARTIDOS -----------------------------

def get_selected_round_games(data):
    """Devuelve (nombre_de_fecha, lista_de_partidos) de la fecha
    marcada como 'selected' dentro de games.filters (la fecha actual
    o mas reciente)."""
    for f in data.get("games", {}).get("filters", []):
        if f.get("selected"):
            return f.get("name"), f.get("games", [])
    return None, []


def fetch_team_page_data(url_name, team_id, timeout=20):
    """Trae la pagina de un equipo (promiedos.com.ar/team/{url_name}/{id})
    y devuelve el bloque de datos incrustado en __NEXT_DATA__.

    Esto es lo mismo que ve el navegador, pero renderizado en el
    servidor (SSR) -- no hace falta JavaScript ni un endpoint de API
    separado, alcanza con un GET normal."""
    url = f"{TEAM_PAGE_BASE}/{url_name}/{team_id}"
    r = requests.get(url, timeout=timeout, headers=_HEADERS)
    r.raise_for_status()
    match = _NEXT_DATA_RE.search(r.text)
    if not match:
        raise ValueError(
            f"No se encontro __NEXT_DATA__ en {url}. "
            "Es posible que Promiedos haya cambiado la estructura de la pagina."
        )
    payload = json.loads(match.group(1))
    return payload["props"]["pageProps"]["data"]


def get_team_games(team_data, kind="last"):
    """Extrae los partidos de la pagina de un equipo ya parseada.
    kind='last' -> resultados (partidos jugados, toda la temporada).
    kind='next' -> proximos partidos programados."""
    rows = team_data.get("games", {}).get(kind, {}).get("rows", [])
    return [parse_game(r["game"]) for r in rows if "game" in r]


def parse_game(game):
    """Convierte un partido del JSON a un dict simple y facil de
    guardar en la base (o de usar directo para calcular)."""
    home, away = game["teams"][0], game["teams"][1]
    finished = game["status"]["enum"] == 3  # 3 = Finalizado
    scores = game.get("scores")
    return {
        "id": game["id"],
        "home_team_id": home["id"],
        "home_team_name": home["name"],
        "away_team_id": away["id"],
        "away_team_name": away["name"],
        "finished": finished,
        "home_goals": int(scores[0]) if finished and scores else None,
        "away_goals": int(scores[1]) if finished and scores else None,
        "start_time": game.get("start_time"),  # "DD-MM-YYYY HH:MM"
        "round_name": game.get("stage_round_name"),
    }