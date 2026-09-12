"""
backfill_history.py
=====================
Carga masiva del historial completo de la temporada, UNA SOLA VEZ (o
cuando quieras refrescar todo desde cero), usando las paginas de
equipo de Promiedos en vez de CSV a mano.

Como funciona:
  Cada pagina de equipo (promiedos.com.ar/team/{url_name}/{id}) trae
  en su HTML un bloque __NEXT_DATA__ con TODOS los partidos jugados en
  la temporada (no solo los ultimos 5 que se ven en pantalla). Este
  script recorre todos los equipos de la Liga Profesional (los saca
  de la tabla de zona, que ya tenemos en la base) y por cada uno pide
  su pagina, extrae el historial, y lo guarda en `matches`.

Uso:
    python backfill_history.py

Es un script para correr manualmente de vez en cuando (no hace falta
correrlo cada semana -- `prode_predictor.py` ya va sumando los
partidos de la fecha actual solo). Tiene una pausa entre pedido y
pedido para no bombardear el sitio con 30 requests seguidos.
"""

import time
import prode_db as db
import promiedos_client as pc

LEAGUE_ID = "hc"
STAGE_NAME = "Clausura"  # cambiar aca si la etapa actual tiene otro nombre
PAUSE_BETWEEN_REQUESTS = 1.0  # segundos, por prudencia


def store_games(conn, games, valid_team_ids=None):
    """Guarda los partidos FINALIZADOS de una lista en `matches`.
    Los partidos por jugar (`finished=False`) se ignoran aca -- esos
    los trae `prode_predictor.py` en cada corrida semanal.

    Si se pasa `valid_team_ids` (el set de IDs de los 30 equipos de
    la Liga Profesional), se descartan los partidos de otras copas
    (ej: Copa Argentina contra un equipo de categorias menores) para
    no ensuciar el calculo de forma reciente con rivales que no son
    de la misma liga."""
    added = skipped_other_competition = 0
    for g in games:
        if not g["finished"]:
            continue
        if valid_team_ids is not None and (
            g["home_team_id"] not in valid_team_ids
            or g["away_team_id"] not in valid_team_ids
        ):
            skipped_other_competition += 1
            continue
        home_id = db.get_or_create_team(conn, g["home_team_name"])
        away_id = db.get_or_create_team(conn, g["away_team_name"])
        try:
            from datetime import datetime
            date = datetime.strptime(g["start_time"], "%d-%m-%Y %H:%M").date().isoformat()
        except (ValueError, TypeError):
            date = g["start_time"] or ""
        cur = conn.execute(
            """INSERT OR IGNORE INTO matches
               (date, matchday, zone, home_team_id, away_team_id, home_goals, away_goals)
               VALUES (?,?,?,?,?,?,?)""",
            (date, 0, g.get("round_name") or "", home_id, away_id,
             g["home_goals"], g["away_goals"]),
        )
        if cur.rowcount:
            added += 1
    return added, skipped_other_competition


def get_all_teams(data):
    """Saca la lista de (url_name, team_id, nombre) de todos los
    equipos que aparecen en las tablas de zona del Clausura."""
    teams = {}
    for rows in pc.get_zone_tables(data, STAGE_NAME).values():
        for r in rows:
            teams[r["team_id"]] = (r["team_url_name"], r["team_id"], r["team_name"])
    return list(teams.values())


def main():
    conn = db.get_conn()

    print("Trayendo la lista de equipos desde la tabla de zona...")
    league_data = pc.fetch_league(LEAGUE_ID)
    print(f"Claves de nivel superior en la respuesta: {list(league_data.keys())}")

    group_names = [g.get("name") for g in league_data.get("tables_groups", [])]
    print(f"Grupos encontrados: {group_names}")

    teams = get_all_teams(league_data)
    print(f"{len(teams)} equipos encontrados.\n")

    if not teams:
        print("No se encontraron equipos. Si las claves de arriba salieron")
        print("vacias ([]), la API sigue devolviendo una respuesta vacia --")
        print("avisale a Claude con esta salida completa.")
        print("Si SI hay claves pero no 'tables_groups' con 'Clausura',")
        print("cambia STAGE_NAME al principio de este archivo.")
        return

    valid_team_ids = {team_id for (_, team_id, _) in teams}

    total_added = total_skipped = 0
    for i, (url_name, team_id, name) in enumerate(teams, start=1):
        print(f"[{i}/{len(teams)}] {name} ({url_name}/{team_id})...", end=" ")
        try:
            team_data = pc.fetch_team_page_data(url_name, team_id)
            games = pc.get_team_games(team_data, kind="last")
            added, skipped = store_games(conn, games, valid_team_ids)
            conn.commit()
            total_added += added
            total_skipped += skipped
            print(f"{len(games)} partidos vistos, {added} nuevos guardados"
                  f"{f', {skipped} descartados (otra copa/categoria)' if skipped else ''}.")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(PAUSE_BETWEEN_REQUESTS)

    print(f"\nListo. {total_added} partidos nuevos agregados al historial "
          f"({total_skipped} descartados por ser de otra competencia).")
    conn.close()


if __name__ == "__main__":
    main()