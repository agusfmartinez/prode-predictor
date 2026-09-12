"""
prode_db.py
===========
Capa de base de datos (SQLite) para el proyecto de pronosticos.

Guarda todo localmente asi no volvemos a pedirle a la API algo que ya
sabemos. La logica es:

  - CARGA INICIAL: la llenas vos a mano con los CSV de plantilla
    (sacando los datos de la tabla oficial una sola vez, sin gastar
    requests de API).
  - CARGA SEMANAL: el script principal (prode_predictor.py) solo le
    pide a la API los resultados de la fecha que se acaba de jugar y
    el fixture de la fecha que viene, y los va sumando a esta base.

TABLAS
------
teams              : equipos (id interno, nombre, zona)
matches             : historial de partidos jugados (para calcular
                       rendimiento de local/visitante)
standings_zone      : tabla de posiciones de la zona (A o B)
standings_promedios : tabla de promedios (la que define el descenso)
standings_anual     : tabla anual (la que define copas internacionales)

Corre este archivo una vez para crear la base:
    python prode_db.py --init
Y para importar tus CSV de la primera carga:
    python prode_db.py --import-csv
"""

import sqlite3
import csv
import argparse
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "prode.db")


# ----------------------- ESQUEMA -----------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    zone TEXT  -- 'A' o 'B'
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    matchday INTEGER,
    zone TEXT,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_goals INTEGER NOT NULL,
    away_goals INTEGER NOT NULL,
    UNIQUE(date, home_team_id, away_team_id)
);

CREATE TABLE IF NOT EXISTS standings_zone (
    team_id INTEGER NOT NULL REFERENCES teams(id),
    zone TEXT NOT NULL,
    position INTEGER,
    points INTEGER,
    played INTEGER,
    goals_for INTEGER,
    goals_against INTEGER,
    updated_at TEXT,
    PRIMARY KEY (team_id)
);

-- Tabla de promedios: la que define el descenso.
-- "promedio" = puntos acumulados en las ultimas temporadas / partidos jugados.
CREATE TABLE IF NOT EXISTS standings_promedios (
    team_id INTEGER NOT NULL REFERENCES teams(id),
    position INTEGER,
    promedio REAL,
    puntos_acumulados INTEGER,
    partidos_computados INTEGER,
    en_zona_descenso INTEGER,  -- 0 o 1
    updated_at TEXT,
    PRIMARY KEY (team_id)
);

-- Tabla anual: la que define copas (Libertadores / Sudamericana).
CREATE TABLE IF NOT EXISTS standings_anual (
    team_id INTEGER NOT NULL REFERENCES teams(id),
    position INTEGER,
    points INTEGER,
    en_zona_copa INTEGER,  -- 0 o 1
    updated_at TEXT,
    PRIMARY KEY (team_id)
);

-- Log de pronosticos: se guarda UNA vez por partido (game_id de
-- Promiedos, asi no se pisa si se re-corre el script antes de que se
-- juegue) y se completa con el resultado real cuando el partido ya
-- termino. Sirve para medir el % de acierto del modelo con el tiempo
-- y decidir con datos (no a ojo) si vale la pena ajustar los pesos de
-- stakes_multiplier, el HOME_ADVANTAGE, etc.
CREATE TABLE IF NOT EXISTS predictions_log (
    game_id TEXT PRIMARY KEY,
    round_name TEXT,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    predicted_at TEXT,
    pred_home_goals INTEGER,
    pred_away_goals INTEGER,
    pct_home INTEGER,
    pct_draw INTEGER,
    pct_away INTEGER,
    pick TEXT,               -- '1' / 'X' / '2'
    actual_home_goals INTEGER,
    actual_away_goals INTEGER,
    actual_result TEXT,      -- '1' / 'X' / '2', NULL hasta que se juegue
    pick_correct INTEGER,    -- 0 / 1, NULL hasta que se juegue
    evaluated_at TEXT
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Base creada/verificada en {DB_PATH}")


# ----------------------- IMPORTAR CSV (CARGA A MANO) -----------------------------

def get_or_create_team(conn, name, zone=None):
    cur = conn.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO teams (name, zone) VALUES (?, ?)", (name, zone))
    return cur.lastrowid


def import_teams_csv(conn, path):
    """CSV: name,zone"""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            get_or_create_team(conn, row["name"].strip(), row.get("zone", "").strip() or None)
    conn.commit()
    print(f"Equipos importados desde {path}")


def import_results_csv(conn, path):
    """CSV: date,matchday,zone,home_team,away_team,home_goals,away_goals"""
    with open(path, encoding="utf-8") as f:
        n = 0
        for row in csv.DictReader(f):
            home_id = get_or_create_team(conn, row["home_team"].strip())
            away_id = get_or_create_team(conn, row["away_team"].strip())
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO matches
                       (date, matchday, zone, home_team_id, away_team_id, home_goals, away_goals)
                       VALUES (?,?,?,?,?,?,?)""",
                    (row["date"].strip(), int(row["matchday"]), row.get("zone", "").strip(),
                     home_id, away_id, int(row["home_goals"]), int(row["away_goals"])),
                )
                n += 1
            except (ValueError, KeyError) as e:
                print(f"  fila salteada ({e}): {row}")
        conn.commit()
    print(f"{n} partidos importados desde {path}")


def import_standings_promedios_csv(conn, path):
    """CSV: team,position,promedio,puntos_acumulados,partidos_computados,en_zona_descenso"""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            team_id = get_or_create_team(conn, row["team"].strip())
            conn.execute(
                """INSERT INTO standings_promedios
                   (team_id, position, promedio, puntos_acumulados, partidos_computados, en_zona_descenso, updated_at)
                   VALUES (?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(team_id) DO UPDATE SET
                     position=excluded.position, promedio=excluded.promedio,
                     puntos_acumulados=excluded.puntos_acumulados,
                     partidos_computados=excluded.partidos_computados,
                     en_zona_descenso=excluded.en_zona_descenso,
                     updated_at=excluded.updated_at""",
                (team_id, int(row["position"]), float(row["promedio"]),
                 int(row["puntos_acumulados"]), int(row["partidos_computados"]),
                 int(row["en_zona_descenso"])),
            )
    conn.commit()
    print(f"Tabla de promedios importada desde {path}")


def import_standings_anual_csv(conn, path):
    """CSV: team,position,points,en_zona_copa"""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            team_id = get_or_create_team(conn, row["team"].strip())
            conn.execute(
                """INSERT INTO standings_anual (team_id, position, points, en_zona_copa, updated_at)
                   VALUES (?,?,?,?, datetime('now'))
                   ON CONFLICT(team_id) DO UPDATE SET
                     position=excluded.position, points=excluded.points,
                     en_zona_copa=excluded.en_zona_copa, updated_at=excluded.updated_at""",
                (team_id, int(row["position"]), int(row["points"]), int(row["en_zona_copa"])),
            )
    conn.commit()
    print(f"Tabla anual importada desde {path}")


def import_standings_zone_csv(conn, path):
    """CSV: team,zone,position,points,played,goals_for,goals_against"""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            team_id = get_or_create_team(conn, row["team"].strip(), row.get("zone", "").strip())
            conn.execute(
                """INSERT INTO standings_zone
                   (team_id, zone, position, points, played, goals_for, goals_against, updated_at)
                   VALUES (?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(team_id) DO UPDATE SET
                     zone=excluded.zone, position=excluded.position, points=excluded.points,
                     played=excluded.played, goals_for=excluded.goals_for,
                     goals_against=excluded.goals_against, updated_at=excluded.updated_at""",
                (team_id, row["zone"].strip(), int(row["position"]), int(row["points"]),
                 int(row["played"]), int(row["goals_for"]), int(row["goals_against"])),
            )
    conn.commit()
    print(f"Tabla de zona importada desde {path}")


# ----------------------- CONSULTAS: RENDIMIENTO LOCAL/VISITANTE -----------------------------

def home_form(conn, team_id, last_n=5):
    """Promedio de goles a favor/en contra de un equipo SOLO cuando jugo de local."""
    rows = conn.execute(
        """SELECT home_goals, away_goals FROM matches
           WHERE home_team_id = ? ORDER BY date DESC LIMIT ?""",
        (team_id, last_n),
    ).fetchall()
    if not rows:
        return None, None
    gf = sum(r[0] for r in rows) / len(rows)
    gc = sum(r[1] for r in rows) / len(rows)
    return gf, gc


def away_form(conn, team_id, last_n=5):
    """Promedio de goles a favor/en contra de un equipo SOLO cuando jugo de visitante."""
    rows = conn.execute(
        """SELECT away_goals, home_goals FROM matches
           WHERE away_team_id = ? ORDER BY date DESC LIMIT ?""",
        (team_id, last_n),
    ).fetchall()
    if not rows:
        return None, None
    gf = sum(r[0] for r in rows) / len(rows)
    gc = sum(r[1] for r in rows) / len(rows)
    return gf, gc


def zone_position(conn, team_id):
    row = conn.execute("SELECT position FROM standings_zone WHERE team_id = ?", (team_id,)).fetchone()
    return row[0] if row else None


# ----------------------- LOG DE PRONOSTICOS -----------------------------

def log_prediction(conn, game_id, round_name, home_team_id, away_team_id, pred):
    """Guarda el pronostico hecho para un partido, UNA sola vez por
    game_id (INSERT OR IGNORE): si el script se re-corre antes de que
    se juegue el partido, no pisa el pronostico ya guardado -- queda
    el primero que se hizo, que es el que tiene sentido evaluar
    despues."""
    conn.execute(
        """INSERT OR IGNORE INTO predictions_log
           (game_id, round_name, home_team_id, away_team_id, predicted_at,
            pred_home_goals, pred_away_goals, pct_home, pct_draw, pct_away, pick)
           VALUES (?,?,?,?, datetime('now'), ?,?,?,?,?,?)""",
        (game_id, round_name, home_team_id, away_team_id,
         pred["score"][0], pred["score"][1],
         pred["pct_home"], pred["pct_draw"], pred["pct_away"], pred["pick"]),
    )
    conn.commit()


def evaluate_prediction(conn, game_id, home_goals, away_goals):
    """Si hay un pronostico pendiente guardado para ese game_id, lo
    completa con el resultado real y marca si acerto o no. No hace
    nada si ese partido no tiene un pronostico guardado (por ejemplo,
    partidos que ya estaban jugados la primera vez que se corrio el
    predictor, o partidos cargados solo por backfill_history.py)."""
    if home_goals > away_goals:
        actual = "1"
    elif home_goals < away_goals:
        actual = "2"
    else:
        actual = "X"

    row = conn.execute(
        "SELECT pick, pick_correct FROM predictions_log WHERE game_id = ?", (game_id,)
    ).fetchone()
    if not row or row[1] is not None:
        return  # no hay pronostico guardado, o ya se evaluo antes

    pick, _ = row
    correct = 1 if pick == actual else 0
    conn.execute(
        """UPDATE predictions_log SET
             actual_home_goals=?, actual_away_goals=?, actual_result=?,
             pick_correct=?, evaluated_at=datetime('now')
           WHERE game_id=?""",
        (home_goals, away_goals, actual, correct, game_id),
    )
    conn.commit()


def accuracy_summary(conn):
    """Devuelve (aciertos, total) sobre todos los pronosticos ya
    evaluados (partidos que ya se jugaron)."""
    rows = conn.execute(
        "SELECT pick_correct FROM predictions_log WHERE pick_correct IS NOT NULL"
    ).fetchall()
    total = len(rows)
    correct = sum(r[0] for r in rows)
    return correct, total


# ----------------------- FACTOR "NECESIDAD DE PUNTOS" -----------------------------

def playoff_zone_stakes(conn, team_id, total_matchdays=16, playoff_spots=8):
    """
    Mide cuanto se juega un equipo en la clasificacion a playoffs DE SU
    ZONA (los 8 mejores de cada zona de 15 avanzan a octavos).
    Devuelve un valor entre -1 y 1:
      +1  -> esta en la pelea directa por el ultimo cupo (cerca del corte)
       0  -> no hay dato suficiente, o esta comodo mid-table sin nada urgente
      -1  -> matematicamente eliminado (no llega ni haciendo puntos perfectos)
    """
    row = conn.execute(
        "SELECT zone, position, points, played FROM standings_zone WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not row or not row[0]:
        return 0.0
    zone, position, points, played = row

    zone_rows = conn.execute(
        "SELECT points FROM standings_zone WHERE zone = ? ORDER BY position",
        (zone,),
    ).fetchall()
    if len(zone_rows) < playoff_spots:
        return 0.0

    cutoff_points = zone_rows[playoff_spots - 1][0]  # puntos del 8vo
    remaining = max(total_matchdays - played, 0)
    diff = points - cutoff_points  # positivo = por encima del corte

    if remaining == 0:
        return 0.0  # ya se termino la fase de zonas, no aplica

    if diff < -3 * remaining:
        return -1.0  # eliminado matematicamente de playoffs
    if abs(diff) <= 6:
        return 1.0  # pelea directa por el cupo
    return 0.0


def stakes_multiplier(conn, team_id, max_boost=0.08):
    """
    Devuelve un multiplicador (ej: 1.10 o 0.96) para aplicar al ataque
    esperado del equipo, segun cuanto le urgen los puntos. Combina tres
    factores, cada uno con su propio peso, y despues limita el total:
      - Descenso (tabla de promedios): el mas fuerte, un equipo que se
        juega la categoria siempre aprieta mas.
      - Playoffs de zona (top 8 de 15): pelea directa por el cupo suma;
        eliminado matematicamente resta (dead rubber, ya no importa
        tanto el resultado, puede haber rotacion de titulares).
      - Copa internacional (tabla anual): pelea por el ultimo cupo suma
        un poco.
    El tope total es +/- 1.5 * max_boost para que ningun partido quede
    dominado solo por la motivacion y siga pesando el nivel futbolistico.
    """
    boost = 0.0

    prom = conn.execute(
        "SELECT en_zona_descenso FROM standings_promedios WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if prom and prom[0]:
        boost += max_boost

    boost += playoff_zone_stakes(conn, team_id) * max_boost * 0.7

    anual = conn.execute(
        "SELECT position FROM standings_anual WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if anual and anual[0] and 5 <= anual[0] <= 9:
        boost += max_boost * 0.6

    cap = max_boost * 1.5
    boost = max(min(boost, cap), -cap)
    return 1.0 + boost


# ----------------------- CLI -----------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Administra la base de datos del prode")
    parser.add_argument("--init", action="store_true", help="crea las tablas")
    parser.add_argument("--import-csv", action="store_true", help="importa los CSV de plantilla")
    parser.add_argument("--dir", default=".", help="carpeta donde estan los CSV")
    args = parser.parse_args()

    if args.init:
        init_db()

    if args.import_csv:
        conn = get_conn()
        for fname, fn in [
            ("plantilla_equipos.csv", import_teams_csv),
            ("plantilla_resultados.csv", import_results_csv),
            ("plantilla_tabla_zona.csv", import_standings_zone_csv),
            ("plantilla_tabla_promedios.csv", import_standings_promedios_csv),
            ("plantilla_tabla_anual.csv", import_standings_anual_csv),
        ]:
            path = os.path.join(args.dir, fname)
            if os.path.exists(path):
                fn(conn, path)
            else:
                print(f"(no encontre {path}, lo salteo)")
        conn.close()