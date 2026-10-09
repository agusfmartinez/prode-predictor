"""
prode_predictor.py (v3 - 100% Promiedos, sin API paga)
=========================================================
Ya no usa API-Football. Todo sale de un solo endpoint de Promiedos:

    https://api.promiedos.com.ar/league/tables_and_fixtures/hc

Flujo de cada corrida:
  1) Trae ese JSON (una sola request).
  2) Actualiza en la base: tabla de zona (Clausura Grupo A/B),
     tabla de promedios, tabla anual, y los partidos de la fecha
     actual (jugados y por jugar).
  3) Para cada partido "por jugar" de la fecha actual, calcula el
     pronostico usando el historial ya guardado en la base.
  4) Manda el reporte por Telegram.

Nota sobre el historial completo (para home_form / away_form):
  Este endpoint solo trae los partidos de UNA fecha por request (la
  marcada como "selected", normalmente la actual). Para tener varias
  fechas de historial en la base, hay que:
    a) correr este script cada semana (asi se va acumulando solo,
       fecha a fecha, con el tiempo), o
    b) cargar el historial viejo a mano una vez con los CSV de
       plantilla (ver README), como plan B mientras se acumula.
"""

import os
import math
import time
from datetime import datetime
from dotenv import load_dotenv

import prode_db as db
import promiedos_client as pc

load_dotenv()

LEAGUE_ID = "hc"          # Liga Profesional Argentina
STAGE_NAME = "Clausura"   # etapa actual del torneo

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LEAGUE_AVG_GOALS_PER_TEAM = 1.30
HOME_ADVANTAGE = 1.15
LAST_N_GAMES = 5
# Si a la fecha que marca Promiedos le quedan esta cantidad de partidos
# sin jugar (o menos), tambien se pronostica la fecha siguiente.
MAX_PENDING_TO_FETCH_NEXT = 2


# ----------------------- PASO 1: TRAER Y GUARDAR DATOS -----------------------------

def sync_standings(conn, data):
    """Guarda las tablas de zona, promedios y anual en la base."""
    for table_name, rows in pc.get_zone_tables(data, STAGE_NAME).items():
        zone_letter = table_name.strip()[-1]
        for r in rows:
            team_id = db.get_or_create_team(conn, r["team_name"], zone_letter)
            gf, gc = (r.get("Goals") or "0:0").split(":")
            conn.execute(
                """INSERT INTO standings_zone
                   (team_id, zone, position, points, played, goals_for, goals_against, updated_at)
                   VALUES (?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(team_id) DO UPDATE SET
                     zone=excluded.zone, position=excluded.position, points=excluded.points,
                     played=excluded.played, goals_for=excluded.goals_for,
                     goals_against=excluded.goals_against, updated_at=excluded.updated_at""",
                (team_id, zone_letter, r["position"], int(r["Points"]),
                 int(r["GamePlayed"]), int(gf), int(gc)),
            )

    for r in pc.get_promedios_rows(data):
        team_id = db.get_or_create_team(conn, r["team_name"])
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
            (team_id, r["position"], float(r["Pct"]), int(r["Points"]),
             int(r["GamePlayed"]), r["en_zona_descenso"]),
        )

    for r in pc.get_anual_rows(data):
        team_id = db.get_or_create_team(conn, r["team_name"])
        conn.execute(
            """INSERT INTO standings_anual (team_id, position, points, en_zona_copa, updated_at)
               VALUES (?,?,?,?, datetime('now'))
               ON CONFLICT(team_id) DO UPDATE SET
                 position=excluded.position, points=excluded.points,
                 en_zona_copa=excluded.en_zona_copa, updated_at=excluded.updated_at""",
            (team_id, r["position"], int(r["Points"]), r["en_zona_copa"]),
        )
    conn.commit()


def sync_current_round(conn, data):
    """Guarda los partidos ya finalizados de la fecha actual en
    `matches` (para ir acumulando historial), y devuelve
    (nombre_de_fecha, partidos) para el reporte."""
    round_name, games = pc.get_selected_round_games(data)
    parsed = [pc.parse_game(g) for g in games]

    for g in parsed:
        if not g["finished"]:
            continue
        home_id = db.get_or_create_team(conn, g["home_team_name"])
        away_id = db.get_or_create_team(conn, g["away_team_name"])
        try:
            date = datetime.strptime(g["start_time"], "%d-%m-%Y %H:%M").date().isoformat()
        except (ValueError, TypeError):
            date = g["start_time"] or ""
        conn.execute(
            """INSERT INTO matches
               (date, matchday, round_name, stage, game_id, home_team_id, away_team_id, home_goals, away_goals)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(date, home_team_id, away_team_id) DO UPDATE SET
                 game_id=excluded.game_id""",
            (date, 0, g.get("round_name") or "", db.infer_stage(date), g["id"],
             home_id, away_id, g["home_goals"], g["away_goals"]),
        )
    conn.commit()
    return round_name, parsed


def fetch_next_round(data, round_name):
    """Arma el fixture de la fecha siguiente a `round_name` usando los
    proximos partidos (`games.next`) de las paginas de equipo.

    Hace falta porque la pagina de liga de Promiedos no avanza a la
    fecha nueva mientras a la actual le quede un partido postergado
    (ej. Fecha 11 con Sarmiento vs River pasado a mitad de semana),
    aunque la fecha nueva ya haya empezado. Pide una pagina por
    equipo, salteando los equipos que ya aparecieron como rival, asi
    que son ~15 requests en vez de 30."""
    from backfill_history import get_all_teams, PAUSE_BETWEEN_REQUESTS

    try:
        next_name = f"Fecha {int(round_name.split()[-1]) + 1}"
    except (AttributeError, ValueError):
        return None, []

    games, seen_teams = {}, set()
    for url_name, team_id, name in get_all_teams(data):
        if team_id in seen_teams:
            continue
        try:
            team_data = pc.fetch_team_page_data(url_name, team_id)
        except Exception as e:
            print(f"(No pude traer la pagina de {name}: {e})")
            continue
        for g in pc.get_team_games(team_data, kind="next"):
            if g.get("round_name") == next_name:
                games[g["id"]] = g
                seen_teams.update((g["home_team_id"], g["away_team_id"]))
                break
        time.sleep(PAUSE_BETWEEN_REQUESTS)

    ordered = sorted(games.values(), key=lambda g: _start_key(g["start_time"]))
    return next_name, ordered


def _start_key(start_time):
    try:
        return datetime.strptime(start_time, "%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return datetime.max


# ----------------------- PASO 2: MODELO (igual que antes) -----------------------------

def poisson_pmf(lam, k):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


CURRENT_STAGE = "Clausura"


def predict_match(conn, home_id, away_id):
    # Preferimos forma reciente SOLO del Clausura (torneo actual). Si
    # un equipo todavia no tiene suficiente historial de este torneo
    # (por ejemplo, muy al principio de la temporada), usamos como
    # respaldo el historial general (incluyendo Apertura) en vez de
    # caer directo al promedio neutro de liga.
    home_gf, home_gc = db.home_form(conn, home_id, LAST_N_GAMES, stage=CURRENT_STAGE)
    if home_gf is None:
        home_gf, home_gc = db.home_form(conn, home_id, LAST_N_GAMES)

    away_gf, away_gc = db.away_form(conn, away_id, LAST_N_GAMES, stage=CURRENT_STAGE)
    if away_gf is None:
        away_gf, away_gc = db.away_form(conn, away_id, LAST_N_GAMES)

    home_gf = home_gf if home_gf is not None else LEAGUE_AVG_GOALS_PER_TEAM
    home_gc = home_gc if home_gc is not None else LEAGUE_AVG_GOALS_PER_TEAM
    away_gf = away_gf if away_gf is not None else LEAGUE_AVG_GOALS_PER_TEAM
    away_gc = away_gc if away_gc is not None else LEAGUE_AVG_GOALS_PER_TEAM

    home_pos = db.zone_position(conn, home_id)
    away_pos = db.zone_position(conn, away_id)

    home_stakes = db.stakes_multiplier(conn, home_id)
    away_stakes = db.stakes_multiplier(conn, away_id)

    home_attack = (home_gf / LEAGUE_AVG_GOALS_PER_TEAM) * home_stakes
    home_defense = home_gc / LEAGUE_AVG_GOALS_PER_TEAM
    away_attack = (away_gf / LEAGUE_AVG_GOALS_PER_TEAM) * away_stakes
    away_defense = away_gc / LEAGUE_AVG_GOALS_PER_TEAM

    exp_home = home_attack * away_defense * LEAGUE_AVG_GOALS_PER_TEAM * HOME_ADVANTAGE
    exp_away = away_attack * home_defense * LEAGUE_AVG_GOALS_PER_TEAM

    if home_pos and away_pos:
        nudge = ((away_pos - home_pos) / 30) * 0.15
        exp_home = max(0.2, exp_home + nudge)
        exp_away = max(0.2, exp_away - nudge)

    max_goals = 6
    p_home = p_draw = p_away = 0.0
    # Mejor marcador dentro de cada resultado posible (1 / X / 2)
    best = {"1": (0.0, (1, 0)), "X": (0.0, (0, 0)), "2": (0.0, (0, 1))}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(exp_home, h) * poisson_pmf(exp_away, a)
            if h > a:
                p_home += p
                outcome = "1"
            elif h == a:
                p_draw += p
                outcome = "X"
            else:
                p_away += p
                outcome = "2"
            if p > best[outcome][0]:
                best[outcome] = (p, (h, a))

    total = p_home + p_draw + p_away
    pct_home = round(p_home / total * 100)
    pct_draw = round(p_draw / total * 100)
    pct_away = 100 - pct_home - pct_draw
    pick = "1" if pct_home >= pct_draw and pct_home >= pct_away else \
           "2" if pct_away >= pct_home and pct_away >= pct_draw else "X"

    # El marcador sale del resultado elegido, asi nunca se contradicen
    # (antes podia salir "0-0" con pronostico "1", y en el prode real
    # uno anota el marcador, no el 1/X/2).
    best_score = best[pick][1]

    return {"score": best_score, "pct_home": pct_home, "pct_draw": pct_draw,
            "pct_away": pct_away, "pick": pick}


# ----------------------- PASO 3: REPORTE -----------------------------

def build_report(conn, rounds):
    lines = []
    for round_name, games in rounds:
        lines.append(f"PRONOSTICOS - {round_name} (Torneo Clausura 2026)\n")
        for g in games:
            if g["finished"]:
                db.evaluate_prediction(conn, g["id"], g["home_goals"], g["away_goals"])
                lines.append(f"{g['home_team_name']} {g['home_goals']}-{g['away_goals']} {g['away_team_name']} (Final)\n")
                continue

            home_id = db.get_or_create_team(conn, g["home_team_name"])
            away_id = db.get_or_create_team(conn, g["away_team_name"])
            pred = predict_match(conn, home_id, away_id)
            db.log_prediction(conn, g["id"], round_name, home_id, away_id, pred)

            lines.append(
                f"{g['home_team_name']} vs {g['away_team_name']}\n"
                f"  Marcador probable: {pred['score'][0]}-{pred['score'][1]}\n"
                f"  1: {pred['pct_home']}%  X: {pred['pct_draw']}%  2: {pred['pct_away']}%\n"
                f"  Pronostico: {pred['pick']}\n"
            )
        lines.append("")

    correct, total, points = db.accuracy_summary(conn)
    if total > 0:
        pct = round(correct / total * 100)
        max_points = total * 3
        lines.append(
            f"\n(Historial: {correct}/{total} ganadores acertados ({pct}%) "
            f"-- {points} puntos de prode sobre un maximo posible de {max_points})\n"
        )

    return "\n".join(lines)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(Telegram no configurado, solo imprimo el resultado)\n")
        return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]})


def main():
    conn = db.get_conn()
    data = pc.fetch_league(LEAGUE_ID)
    sync_standings(conn, data)
    round_name, games = sync_current_round(conn, data)
    rounds = [(round_name, games)]

    # Si a la fecha actual le quedan solo partidos sueltos (postergados),
    # Promiedos todavia no avanzo de fecha: traemos la siguiente desde
    # las paginas de equipo para no quedarnos sin pronosticos.
    pending = sum(1 for g in games if not g["finished"])
    if pending <= MAX_PENDING_TO_FETCH_NEXT:
        print(f"({round_name}: quedan {pending} partido(s) sin jugar, "
              f"busco la fecha siguiente en las paginas de equipo...)\n")
        next_name, next_games = fetch_next_round(data, round_name)
        if next_games:
            rounds.append((next_name, next_games))

    # Barre TODOS los pronosticos pendientes (no solo los de la fecha
    # actual) contra lo que haya en `matches`. Cubre el caso de
    # partidos que terminaron despues de que Promiedos ya habia
    # avanzado a la fecha siguiente (ver nota en evaluate_pending_predictions).
    newly_evaluated = db.evaluate_pending_predictions(conn)
    if newly_evaluated:
        print(f"({newly_evaluated} pronosticos de fechas anteriores se evaluaron recien ahora)\n")

    report = build_report(conn, rounds)
    print(report)
    send_telegram(report)
    with open("ultima_fecha_pronosticos.txt", "w", encoding="utf-8") as f:
        f.write(report)
    conn.close()


if __name__ == "__main__":
    main()