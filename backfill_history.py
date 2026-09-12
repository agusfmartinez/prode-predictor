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
PAUSE_BETWEEN_REQUESTS = 1.0  # segundos, por prudencia


def store_games(conn, games):
    """Guarda los partidos FINALIZADOS de una lista en `matches`.
    Los partidos por jugar (`finished=False`) se ignoran aca -- esos
    los trae `prode_predictor.py` en cada corrida semanal."""
    added = 0
    for g in games:
        if not g["finished"]:
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
    return added


def get_all_teams(data):
    """Saca la lista de (url_name, team_id, nombre) de todos los
    equipos que aparecen en las tablas de zona del Clausura."""
    teams = {}
    for rows in pc.get_zone_tables(data, "Clausura").values():
        for r in rows:
            teams[r["team_id"]] = (r["team_url_name"], r["team_id"], r["team_name"])
    return list(teams.values())


def main():
    conn = db.get_conn()

    print("Trayendo la lista de equipos desde la tabla de zona...")
    league_data = pc.fetch_league(LEAGUE_ID)
    teams = get_all_teams(league_data)
    print(f"{len(teams)} equipos encontrados.\n")

    total_added = 0
    for i, (url_name, team_id, name) in enumerate(teams, start=1):
        print(f"[{i}/{len(teams)}] {name} ({url_name}/{team_id})...", end=" ")
        try:
            team_data = pc.fetch_team_page_data(url_name, team_id)
            games = pc.get_team_games(team_data, kind="last")
            added = store_games(conn, games)
            conn.commit()
            total_added += added
            print(f"{len(games)} partidos vistos, {added} nuevos guardados.")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(PAUSE_BETWEEN_REQUESTS)

    print(f"\nListo. {total_added} partidos nuevos agregados al historial.")
    conn.close()


if __name__ == "__main__":
    main()