# -*- coding: utf-8 -*-
"""front_data.py — calculs partagés (sans Flask) pour la vue animée."""
import datetime as dt
import json
import os

import analysis as A
import parsers as P

KEYS = {A.STATUT_OK: "ok", A.STATUT_ECART: "ecart", A.STATUT_ABSENT: "absent",
        A.STATUT_PAS_COMMENCE: "attente", A.STATUT_NON_PLANIF: "off"}
COUL = {"teal": "#0E7C86", "amber": "#E08E1D", "ok": "#1F9D6B", "ko": "#D6483D", "acc": "#7B61FF", "pau": "#E5646A"}


def hm(t):
    return t.strftime("%H:%M") if isinstance(t, dt.time) else None


def n1(v):
    return round(A.safe_num(v), 1)


def build(planning, acts, ags, d, seuil=5, rt=False, heure=None, demo=False):
    jour = P.date_to_jour_fr(d)
    shift = P.get_shift_for_day(planning, jour)
    states = A.prepare_states(ags)
    pct = A.agent_group_pct(states) if states is not None else None
    logins = set(states["Login"]) if states is not None else set()
    m = A.build_matrice_comportementale(shift, acts, logins, seuil_retard_min=seuil, export_temps_reel=rt,
                                        heure_extraction=heure, report_date=d)
    warns = []
    if acts is None:
        warns.append("« Agents activities by dates » non importé : arrivées, départs et retards indisponibles.")
    if states is None:
        warns.append("« Agents report » non importé : répartition des états indisponible.")
    vus = (set(acts["Login"]) if acts is not None else set()) | logins
    hp = vus - set(shift["Login"])
    if hp:
        warns.append(f"{len(hp)} login(s) présents dans Vocalcom mais absents du planning du {jour}.")

    pl = m[m["Statut_planning"] == "TRAVAIL"]
    pres = int(pl["Statut"].isin([A.STATUT_OK, A.STATUT_ECART]).sum())
    abs_ = int((m["Statut"] == A.STATUT_ABSENT).sum())
    ret = int((m["Retard_min"].fillna(0) > 0).sum())
    dep = int((m["Depart_anticipe_min"].fillna(0) > 0).sum())
    kp = [{"l": f"Présents sur {len(pl)} planifiés", "v": pres, "c": COUL["ok"]},
          {"l": "Taux de présence", "v": round(pres / len(pl) * 100, 1) if len(pl) else 0, "s": " %", "d": 1, "c": COUL["teal"]},
          {"l": "Absences non justifiées", "v": abs_, "c": COUL["ko"], "al": abs_ > 0},
          {"l": "Retards", "v": ret, "c": COUL["amber"], "al": ret > 0},
          {"l": "Départs anticipés", "v": dep, "c": COUL["acc"]}]
    groupes = moy = None
    if pct is not None:
        tot = pct["Total_min"].sum()
        sh = lambda g: pct[f"{g}_min"].sum() / tot * 100 if tot else 0
        groupes = [{"g": g, "pct": round(sh(g), 2), "txt": A.fmt_hm(pct[f"{g}_min"].sum())} for g in A.GROUPES]
        moy = {g: round(float(pct[f"{g}_pct"].mean()), 1) for g in A.GROUPES}
        kp += [{"l": "Taux de travail", "v": round(sh(A.GROUPE_TRAITEMENT) + sh(A.GROUPE_POST), 1), "s": " %", "d": 1, "c": COUL["teal"]},
               {"l": "En attente", "v": round(sh(A.GROUPE_ATTENTE), 1), "s": " %", "d": 1, "c": COUL["amber"]},
               {"l": "En pause", "v": round(sh(A.GROUPE_PAUSE), 1), "s": " %", "d": 1, "c": COUL["pau"]}]
    pmap = {r["Login"]: {g: round(float(r[f"{g}_pct"]), 1) for g in A.GROUPES} for _, r in pct.iterrows()} if pct is not None else {}
    agents = []
    for _, r in m.iterrows():
        lg = r["Login"]
        ags_s = states[states["Login"] == lg] if states is not None else None
        e = r["Ecart_arrivee_min"]
        agents.append({
            "login": lg, "nom": r["Nom"] or lg, "mat": r["Matricule_RH"] or "", "st": KEYS.get(r["Statut"], "off"),
            "debut": hm(r["Debut_prevu"]), "fin": hm(r["Fin_prevu"]), "arr": hm(r["Arrivee_reelle"]), "dep": hm(r["Depart_reel"]),
            "ecart": None if e is None or e != e else float(e), "retard": n1(r["Retard_min"]), "depa": n1(r["Depart_anticipe_min"]),
            "ign": bool(r["Depart_anticipe_ignore"]), "duree": round(A.safe_num(r["Duree_travail_min"])),
            "pct": pmap.get(lg), "syn": A.synthese_recadrage(r, d, ags_s)})
    return {"date": d.strftime("%d/%m/%Y"), "jour": jour, "demo": demo, "seuil": seuil,
            "temps_reel": hm(heure) if rt and heure else None, "warns": warns, "kpis": kp,
            "groupes": groupes, "moy": moy, "agents": agents}




def render_html(data):
    """Renvoie static/index.html avec les données injectées (mode intégré à Streamlit)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    with open(p, encoding="utf-8") as fh:
        html = fh.read()
    js = json.dumps(data, ensure_ascii=False,
                    default=lambda o: o.item() if hasattr(o, "item") else str(o)).replace("</", "<\\/")
    return html.replace("<body>", "<body><script>window.__DATA__=" + js + ";</script>", 1)
