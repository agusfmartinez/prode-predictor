"""
prode_predictor.py (v2 - con base de datos local)
====================================================
Version que usa prode_db.py como cache. La idea:

  1) Vos ya hiciste la carga inicial a mano (ver plantilla_*.csv y
     "python prode_db.py --init / --import-csv").
  2) Cada semana, este script:
       a) le pide a la API SOLO los resultados de la fecha que se
          acaba de jugar (no todo el historial) y los guarda en
          la base con prode_db.import_results_csv-equivalente,
       b) le pide el fixture de la fecha que viene,
       c) para cada partido, calcula el pronostico usando los datos
          que YA estan en la base (home_form / away_form / stakes),
          sin golpear la API de nuevo para eso.
  3) Vos actualizas las tablas (zona/promedios/anual) a mano una vez
     por semana con los CSV, porque cambian poco y así no gastás
     requests en algo que podés copiar de la tabla oficial en 2 minutos.

Esto baja el consumo de API de "todo cada vez" a "solo lo nuevo".
"""

import os
import math
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

import prode_db as db

load_dotenv()  # lee las variables desde el archivo .env si existe

# ----------------------- CONFIGURACION -----------------------------

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "PEGA_TU_API_KEY_ACA")
API_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

LEAGUE_ID = 128     # confirmalo vos, ver README
SEASON = 2026

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LEAGUE_AVG_GOALS_PER_TEAM = 1.30
HOME_ADVANTAGE = 1.15
LAST_N_GAMES = 5
DAYS_AHEAD = 7
DAYS_BACK = 5   # para traer los resultados de la fecha recien jugada


# ----------------------- PASO 1: TRAER SOLO LO NUEVO DE LA API -----------------------------

def sync_recent_results(conn):
    """Trae de la API los partidos jugados en los ultimos DAYS_BACK dias
    y los guarda en la base (si ya estan, no hace nada, por el
    UNIQUE(date, home_team_id, away_team_id))."""
    today = datetime.utcnow().date()
    start = today - timedelta(days=DAYS_BACK)
    params = {
        "league": LEAGUE_ID, "season": SEASON,
        "from": start.isoformat(), "to": today.isoformat(),
        "status": "FT",  # solo partidos finalizados
    }
    r = requests.get(f"{API_BASE}/fixtures", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    games = r.json().get("response", [])

    nuevos = 0
    for g in games:
        home_name = g["teams"]["home"]["name"]
        away_name = g["teams"]["away"]["name"]
        home_id = db.get_or_create_team(conn, home_name)
        away_id = db.get_or_create_team(conn, away_name)
        date = g["fixture"]["date"][:10]
        matchday = g["league"].get("round", "")
        hg = g["goals"]["home"] or 0
        ag = g["goals"]["away"] or 0
        cur = conn.execute(
            """INSERT OR IGNORE INTO matches
               (date, matchday, zone, home_team_id, away_team_id, home_goals, away_goals)
               VALUES (?,?,?,?,?,?,?)""",
            (date, 0, "", home_id, away_id, hg, ag),
        )
        if cur.rowcount:
            nuevos += 1
    conn.commit()
    print(f"Resultados nuevos agregados a la base: {nuevos}")


def get_upcoming_fixtures():
    """Este si le pega a la API cada vez, porque el fixture de la
    proxima fecha es justamente el dato que no podemos tener guardado
    de antemano (puede reprogramarse)."""
    today = datetime.utcnow().date()
    end = today + timedelta(days=DAYS_AHEAD)
    params = {
        "league": LEAGUE_ID, "season": SEASON,
        "from": today.isoformat(), "to": end.isoformat(),
    }
    r = requests.get(f"{API_BASE}/fixtures", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("response", [])


# ----------------------- PASO 2: MODELO -----------------------------

def poisson_pmf(lam, k):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def predict_match(conn, home_id, away_id, home_name, away_name):
    home_gf, home_gc = db.home_form(conn, home_id, LAST_N_GAMES)
    away_gf, away_gc = db.away_form(conn, away_id, LAST_N_GAMES)

    # si todavia no hay suficiente historial en la base, usamos el
    # promedio de liga como valor neutro en vez de romper
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
    best_p, best_score = 0.0, (0, 0)
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(exp_home, h) * poisson_pmf(exp_away, a)
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
            if p > best_p:
                best_p, best_score = p, (h, a)

    total = p_home + p_draw + p_away
    pct_home = round(p_home / total * 100)
    pct_draw = round(p_draw / total * 100)
    pct_away = 100 - pct_home - pct_draw
    pick = "1" if pct_home >= pct_draw and pct_home >= pct_away else \
           "2" if pct_away >= pct_home and pct_away >= pct_draw else "X"

    return {
        "score": best_score, "pct_home": pct_home, "pct_draw": pct_draw,
        "pct_away": pct_away, "pick": pick,
        "home_form_used": (round(home_gf, 2), round(home_gc, 2)),
        "away_form_used": (round(away_gf, 2), round(away_gc, 2)),
        "stakes": (round(home_stakes, 2), round(away_stakes, 2)),
    }


# ----------------------- PASO 3: REPORTE -----------------------------

def build_report(conn):
    fixtures = get_upcoming_fixtures()
    if not fixtures:
        return "No encontre partidos programados en los proximos dias."

    lines = ["PRONOSTICOS - Torneo Clausura 2026\n"]
    for f in fixtures:
        home_name = f["teams"]["home"]["name"]
        away_name = f["teams"]["away"]["name"]
        home_id = db.get_or_create_team(conn, home_name)
        away_id = db.get_or_create_team(conn, away_name)

        pred = predict_match(conn, home_id, away_id, home_name, away_name)

        lines.append(
            f"{home_name} vs {away_name}\n"
            f"  Marcador probable: {pred['score'][0]}-{pred['score'][1]}\n"
            f"  1: {pred['pct_home']}%  X: {pred['pct_draw']}%  2: {pred['pct_away']}%\n"
            f"  Pronostico: {pred['pick']}\n"
        )
    return "\n".join(lines)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(Telegram no configurado, solo imprimo el resultado)\n")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]})


def main():
    conn = db.get_conn()
    sync_recent_results(conn)     # (a) solo trae lo nuevo
    report = build_report(conn)   # (b)+(c) fixture + calculo con datos locales
    print(report)
    send_telegram(report)
    with open("ultima_fecha_pronosticos.txt", "w", encoding="utf-8") as f:
        f.write(report)
    conn.close()


if __name__ == "__main__":
    main()