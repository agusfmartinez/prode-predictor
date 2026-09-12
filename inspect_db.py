"""
inspect_db.py
==============
Vista general de toda la base: cuantos registros hay en cada tabla,
que equipos tienen datos incompletos, y una muestra de cada cosa. Para
ver de un vistazo si algo no se poblo bien, sin tener que ir tabla por
tabla a mano.

Uso:
    python inspect_db.py
"""

import prode_db as db


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    conn = db.get_conn()

    # ----------------------- CONTEOS GENERALES -----------------------------
    section("CONTEOS GENERALES")
    counts = {
        "teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
        "matches": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
        "standings_zone": conn.execute("SELECT COUNT(*) FROM standings_zone").fetchone()[0],
        "standings_promedios": conn.execute("SELECT COUNT(*) FROM standings_promedios").fetchone()[0],
        "standings_anual": conn.execute("SELECT COUNT(*) FROM standings_anual").fetchone()[0],
    }
    for k, v in counts.items():
        print(f"  {k}: {v}")

    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM matches").fetchone()
    print(f"  rango de fechas en matches: {date_range[0]} a {date_range[1]}")

    # ----------------------- EQUIPOS Y SU COBERTURA -----------------------------
    section("EQUIPOS Y SU COBERTURA DE DATOS")
    print(f"{'Equipo':<28} {'Partidos':>9} {'Zona':>6} {'Prom.':>6} {'Anual':>6}")
    teams = conn.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
    incomplete = []
    for team_id, name in teams:
        n_matches = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE home_team_id=? OR away_team_id=?",
            (team_id, team_id),
        ).fetchone()[0]
        has_zone = conn.execute(
            "SELECT 1 FROM standings_zone WHERE team_id=?", (team_id,)
        ).fetchone() is not None
        has_prom = conn.execute(
            "SELECT 1 FROM standings_promedios WHERE team_id=?", (team_id,)
        ).fetchone() is not None
        has_anual = conn.execute(
            "SELECT 1 FROM standings_anual WHERE team_id=?", (team_id,)
        ).fetchone() is not None

        print(f"{name:<28} {n_matches:>9} {'si' if has_zone else '-':>6} "
              f"{'si' if has_prom else '-':>6} {'si' if has_anual else '-':>6}")

        if n_matches < 5 or not (has_zone and has_prom and has_anual):
            incomplete.append((name, n_matches, has_zone, has_prom, has_anual))

    # ----------------------- ALERTAS -----------------------------
    section("POSIBLES PROBLEMAS")
    if not incomplete:
        print("  Ninguno detectado: todos los equipos tienen partidos y las 3 tablas.")
    else:
        for name, n_matches, has_zone, has_prom, has_anual in incomplete:
            faltantes = []
            if n_matches < 5:
                faltantes.append(f"solo {n_matches} partidos (menos de 5)")
            if not has_zone:
                faltantes.append("sin tabla de zona")
            if not has_prom:
                faltantes.append("sin tabla de promedios")
            if not has_anual:
                faltantes.append("sin tabla anual")
            print(f"  {name}: {', '.join(faltantes)}")

    # partidos con datos raros (sin goles siendo que deberian estar finalizados)
    null_goals = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE home_goals IS NULL OR away_goals IS NULL"
    ).fetchone()[0]
    if null_goals:
        print(f"  {null_goals} partidos guardados sin goles cargados (no deberian estar en la tabla)")

    # equipos duplicados por nombre parecido (ej "Racing" y "Racing Club")
    dup_check = conn.execute(
        "SELECT name, COUNT(*) c FROM teams GROUP BY name HAVING c > 1"
    ).fetchall()
    if dup_check:
        print(f"  Nombres de equipo duplicados exactos: {dup_check}")

    # ----------------------- MUESTRA DE PARTIDOS -----------------------------
    section("MUESTRA DE PARTIDOS (10 mas recientes)")
    sample = conn.execute(
        """SELECT m.date, m.zone, t1.name, m.home_goals, m.away_goals, t2.name
           FROM matches m
           JOIN teams t1 ON t1.id = m.home_team_id
           JOIN teams t2 ON t2.id = m.away_team_id
           ORDER BY m.date DESC LIMIT 10"""
    ).fetchall()
    for date, zone_col, home, hg, ag, away in sample:
        print(f"  {date} [{zone_col}]: {home} {hg}-{ag} {away}")
    print("\n  (la columna [zone] en realidad guarda el nombre de la fecha,")
    print("   ej. 'Fecha 11' -- no la zona A/B. Es un nombre de columna")
    print("   heredado que no afecta al modelo, pero es confuso al leerlo.)")

    # ----------------------- TABLA DE ZONA COMPLETA -----------------------------
    section("TABLA DE ZONA (Clausura)")
    rows = conn.execute(
        """SELECT sz.zone, sz.position, t.name, sz.points, sz.played, sz.goals_for, sz.goals_against
           FROM standings_zone sz JOIN teams t ON t.id = sz.team_id
           ORDER BY sz.zone, sz.position"""
    ).fetchall()
    for zone, pos, name, pts, played, gf, gc in rows:
        print(f"  Zona {zone} #{pos:>2}  {name:<28} Pts={pts:>2} PJ={played:>2} GF={gf:>2} GC={gc:>2}")

    conn.close()


if __name__ == "__main__":
    main()