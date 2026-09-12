"""
check_team.py
==============
Utilidad de diagnostico para revisar el estado de un equipo en la
base local: cuantos partidos tiene guardados, su forma de local y
visitante, y si aparece en las tablas de zona/promedios/anual.

Uso:
    python check_team.py Racing
    python check_team.py "River Plate"
    python check_team.py Boca

Busca por coincidencia parcial (no hace falta el nombre exacto). Si
encuentra mas de un equipo que matchea, los lista a todos para que
elijas cual mirar en detalle.
"""

import sys
import prode_db as db


def find_teams(conn, query):
    return conn.execute(
        "SELECT id, name, zone FROM teams WHERE name LIKE ?",
        (f"%{query}%",),
    ).fetchall()


def show_team_detail(conn, team_id, name):
    print(f"\n=== {name} (id={team_id}) ===")

    n_matches = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE home_team_id=? OR away_team_id=?",
        (team_id, team_id),
    ).fetchone()[0]
    print(f"Partidos guardados en total: {n_matches}")

    home_gf, home_gc = db.home_form(conn, team_id)
    away_gf, away_gc = db.away_form(conn, team_id)
    if home_gf is not None:
        print(f"Forma de LOCAL (ultimos partidos): GF={home_gf} GC={home_gc}")
    else:
        print("Forma de LOCAL: sin datos todavia")
    if away_gf is not None:
        print(f"Forma de VISITANTE (ultimos partidos): GF={away_gf} GC={away_gc}")
    else:
        print("Forma de VISITANTE: sin datos todavia")

    zone_row = conn.execute(
        "SELECT zone, position, points, played FROM standings_zone WHERE team_id=?",
        (team_id,),
    ).fetchone()
    print(f"Tabla de zona: {zone_row}" if zone_row else "Tabla de zona: sin datos")

    prom_row = conn.execute(
        "SELECT position, promedio, en_zona_descenso FROM standings_promedios WHERE team_id=?",
        (team_id,),
    ).fetchone()
    print(f"Tabla de promedios: {prom_row}" if prom_row else "Tabla de promedios: sin datos")

    anual_row = conn.execute(
        "SELECT position, points, en_zona_copa FROM standings_anual WHERE team_id=?",
        (team_id,),
    ).fetchone()
    print(f"Tabla anual: {anual_row}" if anual_row else "Tabla anual: sin datos")

    print("\nUltimos partidos de LOCAL (los que usa home_form):")
    home_rows = conn.execute(
        """SELECT date, away_team_id, home_goals, away_goals
           FROM matches WHERE home_team_id=? ORDER BY date DESC LIMIT 5""",
        (team_id,),
    ).fetchall()
    for date, away_id, hg, ag in home_rows:
        away_name = conn.execute("SELECT name FROM teams WHERE id=?", (away_id,)).fetchone()[0]
        print(f"  {date}: {name} {hg}-{ag} {away_name}")

    print("\nUltimos partidos de VISITANTE (los que usa away_form):")
    away_rows = conn.execute(
        """SELECT date, home_team_id, home_goals, away_goals
           FROM matches WHERE away_team_id=? ORDER BY date DESC LIMIT 5""",
        (team_id,),
    ).fetchall()
    for date, home_id, hg, ag in away_rows:
        home_name = conn.execute("SELECT name FROM teams WHERE id=?", (home_id,)).fetchone()[0]
        print(f"  {date}: {home_name} {hg}-{ag} {name}")

    print("\nUltimos partidos guardados (mezclados, para contexto general):")
    rows = conn.execute(
        """SELECT date, home_team_id, away_team_id, home_goals, away_goals
           FROM matches WHERE home_team_id=? OR away_team_id=?
           ORDER BY date DESC LIMIT 8""",
        (team_id, team_id),
    ).fetchall()
    for date, home_id, away_id, hg, ag in rows:
        home_name = conn.execute("SELECT name FROM teams WHERE id=?", (home_id,)).fetchone()[0]
        away_name = conn.execute("SELECT name FROM teams WHERE id=?", (away_id,)).fetchone()[0]
        print(f"  {date}: {home_name} {hg}-{ag} {away_name}")


def main():
    if len(sys.argv) < 2:
        print("Uso: python check_team.py <nombre o parte del nombre del equipo>")
        print('Ejemplo: python check_team.py Racing')
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    conn = db.get_conn()
    matches = find_teams(conn, query)

    if not matches:
        print(f"No se encontro ningun equipo que contenga '{query}' en la base.")
        return

    if len(matches) > 1:
        print(f"Encontre {len(matches)} equipos que matchean '{query}':")
        for team_id, name, zone in matches:
            print(f"  - {name} (id={team_id}, zona={zone})")
        print("\nMostrando el detalle de todos:")

    for team_id, name, zone in matches:
        show_team_detail(conn, team_id, name)


if __name__ == "__main__":
    main()