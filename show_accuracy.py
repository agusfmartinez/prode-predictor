"""
show_accuracy.py
==================
Muestra el historial de pronosticos ya evaluados (partidos que ya se
jugaron), con el detalle de cada uno y el % de acierto acumulado. Para
ver de un vistazo si el modelo esta funcionando o si hay que ajustar
algo.

Uso:
    python show_accuracy.py
"""

import prode_db as db


def main():
    conn = db.get_conn()

    rows = conn.execute(
        """SELECT p.round_name, t1.name, t2.name,
                  p.pred_home_goals, p.pred_away_goals, p.pick,
                  p.actual_home_goals, p.actual_away_goals, p.actual_result,
                  p.pick_correct, p.prode_points
           FROM predictions_log p
           JOIN teams t1 ON t1.id = p.home_team_id
           JOIN teams t2 ON t2.id = p.away_team_id
           WHERE p.pick_correct IS NOT NULL
           ORDER BY p.evaluated_at"""
    ).fetchall()

    if not rows:
        print("Todavia no hay pronosticos evaluados (partidos ya jugados).")
        print("Esto se va llenando solo cada vez que corres backfill_history.py")
        print("o prode_predictor.py, y algun partido que habias pronosticado ya termino.")
        return

    print(f"{'Fecha':<10} {'Partido':<45} {'Pron.':<8} {'Real':<8} {'Puntos':<7} {'Resultado'}")
    print("-" * 100)
    for (round_name, home, away, ph, pa, pick,
         ah, aa, actual, correct, points) in rows:
        partido = f"{home} vs {away}"
        marcador_pred = f"{ph}-{pa} ({pick})"
        marcador_real = f"{ah}-{aa} ({actual})"
        resultado = "MARCADOR EXACTO" if points == 3 else ("ganador OK" if points == 1 else "fallo")
        print(f"{round_name or '':<10} {partido:<45} {marcador_pred:<8} {marcador_real:<8} {points:<7} {resultado}")

    correct, total, points = db.accuracy_summary(conn)
    pct = round(correct / total * 100) if total else 0
    max_points = total * 3
    print("-" * 100)
    print(f"Ganadores acertados: {correct}/{total} ({pct}%)")
    print(f"Puntos de prode: {points} sobre un maximo posible de {max_points} "
          f"({round(points/max_points*100) if max_points else 0}% del maximo)")

    pending = conn.execute(
        "SELECT COUNT(*) FROM predictions_log WHERE pick_correct IS NULL"
    ).fetchone()[0]
    if pending:
        print(f"({pending} pronosticos todavia pendientes de que se jueguen)")


if __name__ == "__main__":
    main()