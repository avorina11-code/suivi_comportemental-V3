# -*- coding: utf-8 -*-
"""
analysis.py
===========
Logique métier :
  - build_matrice_comportementale : croise le planning du jour avec
    « Agents activities by dates » (arrivée / départ réels) -> retards,
    départs anticipés, absences.
  - états (Attente / Traitement / Post-travail / Pause...) issus de
    « Agents report » : agrégats équipe et par agent.

Plus aucune règle de pause : seul le seuil de retard est paramétrable.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _as_time(v) -> Optional[dt.time]:
    return v if isinstance(v, dt.time) else None


def _minutes_between(t1, t2) -> Optional[float]:
    t1, t2 = _as_time(t1), _as_time(t2)
    if t1 is None or t2 is None:
        return None
    d1 = dt.datetime.combine(dt.date.today(), t1)
    d2 = dt.datetime.combine(dt.date.today(), t2)
    return (d2 - d1).total_seconds() / 60.0


def safe_num(v) -> float:
    """None / NaN -> 0.0 (un NaN est « truthy » : `nan or 0` renvoie nan)."""
    if v is None:
        return 0.0
    try:
        if pd.isna(v):
            return 0.0
    except (TypeError, ValueError):
        pass
    return float(v)


def fmt_hm(minutes: float) -> str:
    """90 -> '1h30'."""
    m = int(round(safe_num(minutes)))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


# ---------------------------------------------------------------------------
# États (Agents report)
# ---------------------------------------------------------------------------

GROUPE_ATTENTE = "Attente"
GROUPE_TRAITEMENT = "Traitement"
GROUPE_POST = "Post-travail"
GROUPE_PAUSE = "Pause"
GROUPE_AUTRE = "Autre"
GROUPES = [GROUPE_TRAITEMENT, GROUPE_POST, GROUPE_ATTENTE, GROUPE_PAUSE, GROUPE_AUTRE]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def etat_groupe(categorie: str) -> str:
    """Rattache un libellé d'état Vocalcom à l'un des 5 groupes du dashboard."""
    n = _norm(categorie)
    if n.startswith("attente"):
        return GROUPE_ATTENTE
    if n.startswith("traitement"):
        return GROUPE_TRAITEMENT
    if n.startswith("post"):
        return GROUPE_POST
    if n.startswith("pause"):
        return GROUPE_PAUSE
    return GROUPE_AUTRE


def prepare_states(agents_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Ajoute Groupe + Pct (part du temps total des états de premier niveau
    de l'agent, donc le total fait 100 %)."""
    if agents_df is None or agents_df.empty:
        return None
    df = agents_df.copy()
    df["Groupe"] = [
        etat_groupe(p if n == 2 and isinstance(p, str) else c)
        for c, p, n in zip(df["Categorie"], df["Parent"], df["Niveau"])
    ]
    base = (
        df[df["Niveau"] == 1].groupby("Login")["Temps_total_min"].sum().rename("Base_min")
    )
    df = df.merge(base, on="Login", how="left")
    df["Pct"] = (df["Temps_total_min"] / df["Base_min"].where(df["Base_min"] > 0) * 100).fillna(0.0)
    return df


def team_state_totals(states: pd.DataFrame, niveau: int = 1) -> pd.DataFrame:
    """Temps cumulé et % équipe par état (niveau 1 = principaux, 2 = détail)."""
    sub = states[states["Niveau"] == niveau]
    out = sub.groupby(["Categorie", "Groupe"], as_index=False).agg(
        Temps_min=("Temps_total_min", "sum"), Occurences=("Occurences", "sum")
    )
    base = states[states["Niveau"] == 1]["Temps_total_min"].sum()
    out["Pct"] = out["Temps_min"] / base * 100 if base > 0 else 0.0
    return out.sort_values("Temps_min", ascending=False).reset_index(drop=True)


def agent_group_pct(states: pd.DataFrame) -> pd.DataFrame:
    """1 ligne / agent : minutes et % par groupe + taux de travail."""
    top = states[states["Niveau"] == 1]
    mins = top.pivot_table(index=["Login", "Nom"], columns="Groupe",
                           values="Temps_total_min", aggfunc="sum", fill_value=0.0)
    for g in GROUPES:
        if g not in mins.columns:
            mins[g] = 0.0
    mins = mins[GROUPES]
    total = mins.sum(axis=1).replace(0, float("nan"))
    pct = mins.div(total, axis=0).fillna(0.0) * 100
    out = mins.add_suffix("_min").join(pct.add_suffix("_pct")).reset_index()
    out["Total_min"] = mins.sum(axis=1).values
    out["Taux_travail_pct"] = out[f"{GROUPE_TRAITEMENT}_pct"] + out[f"{GROUPE_POST}_pct"]
    return out


# ---------------------------------------------------------------------------
# Matrice comportementale (planning × arrivée/départ)
# ---------------------------------------------------------------------------

STATUT_ECART = "🔴 Écart à recadrer"
STATUT_OK = "🟢 Conforme"
STATUT_ABSENT = "🚫 Absence non justifiée"
STATUT_NON_PLANIF = "⚪ Non planifié (OFF/Congé)"
STATUT_PAS_COMMENCE = "⌛ Pas encore commencé"


def build_matrice_comportementale(
    shift_today: pd.DataFrame,
    activities: Optional[pd.DataFrame],
    agents_logins: Optional[set] = None,
    *,
    seuil_retard_min: int = 5,
    export_temps_reel: bool = False,
    heure_extraction: Optional[dt.time] = None,
    report_date: Optional[dt.date] = None,
) -> pd.DataFrame:
    """Une ligne par agent du planning du jour.

    Arrivée / départ réels = UNIQUEMENT « Agents activities by dates ».
    `agents_logins` (facultatif) : logins vus dans « Agents report » ; sert
    seulement à savoir qu'un agent a travaillé quand l'export activities
    ne contient pas ses horaires.

    - Retard = arrivée - début prévu, compté si > seuil_retard_min.
    - Départ anticipé = fin prévue - départ réel ; ignoré si export temps
      réel et fin prévue postérieure à l'heure d'extraction.
    - Planifié sans aucune trace -> Absence non justifiée (sauf si son
      shift n'a pas encore commencé à l'heure de référence).
    """
    agents_logins = agents_logins or set()

    if activities is not None and not activities.empty:
        act = (
            activities.groupby("Login", as_index=False)
            .agg(
                Arrivee_reelle=("Arrivee", lambda s: min([x for x in s if isinstance(x, dt.time)], default=None)),
                Depart_reel=("Depart", lambda s: max([x for x in s if isinstance(x, dt.time)], default=None)),
                Duree_travail_min=("Duree_travail_min", "sum"),
                Nom_Vocalcom=("Nom_tronque", "first"),
            )
        )
    else:
        act = pd.DataFrame(columns=["Login", "Arrivee_reelle", "Depart_reel",
                                    "Duree_travail_min", "Nom_Vocalcom"])
    df = shift_today.merge(act, on="Login", how="left")

    if report_date is not None and report_date == dt.date.today():
        heure_reference = (
            _as_time(heure_extraction)
            if export_temps_reel and heure_extraction is not None
            else dt.datetime.now().time()
        )
    else:
        heure_reference = None
    heure_extraction_t = _as_time(heure_extraction)

    records = []
    for _, r in df.iterrows():
        statut_planning = r["Statut_planning"]
        login = r["Login"]
        a_horaires = isinstance(r.get("Arrivee_reelle"), dt.time)
        a_pointe = a_horaires or pd.notna(r.get("Duree_travail_min")) or login in agents_logins

        ecart_arrivee = None
        retard = None
        depart_anticipe = None
        depart_ignore = False

        if statut_planning != "TRAVAIL":
            statut = STATUT_NON_PLANIF
        elif not a_pointe:
            debut = _as_time(r.get("Debut_prevu"))
            if heure_reference is not None and debut is not None and debut > heure_reference:
                statut = STATUT_PAS_COMMENCE
            else:
                statut = STATUT_ABSENT
        else:
            ecart_arrivee = _minutes_between(r.get("Debut_prevu"), r.get("Arrivee_reelle"))
            retard = round(ecart_arrivee, 1) if ecart_arrivee is not None and ecart_arrivee > seuil_retard_min else 0.0

            fin_prevu = _as_time(r.get("Fin_prevu"))
            if (export_temps_reel and heure_extraction_t is not None
                    and fin_prevu is not None and fin_prevu > heure_extraction_t):
                depart_ignore = True
                depart_anticipe = 0.0
            else:
                ecart_dep = _minutes_between(r.get("Depart_reel"), fin_prevu)
                depart_anticipe = round(ecart_dep, 1) if ecart_dep is not None and ecart_dep > 0 else 0.0

            statut = STATUT_ECART if (safe_num(retard) > 0 or safe_num(depart_anticipe) > 0) else STATUT_OK

        records.append({
            "Matricule_RH": r.get("Matricule_RH"),
            "Login": login,
            "Nom": r.get("Nom") or r.get("Nom_Vocalcom"),
            "Statut_planning": statut_planning,
            "Debut_prevu": r.get("Debut_prevu"),
            "Fin_prevu": r.get("Fin_prevu"),
            "Arrivee_reelle": r.get("Arrivee_reelle"),
            "Depart_reel": r.get("Depart_reel"),
            "Ecart_arrivee_min": None if ecart_arrivee is None else round(ecart_arrivee, 1),
            "Retard_min": retard,
            "Depart_anticipe_min": depart_anticipe,
            "Depart_anticipe_ignore": depart_ignore,
            "Duree_travail_min": r.get("Duree_travail_min"),
            "Statut": statut,
        })
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Synthèse d'entretien
# ---------------------------------------------------------------------------

def synthese_recadrage(row: pd.Series, date_jour: dt.date,
                       agent_states: Optional[pd.DataFrame] = None) -> str:
    nom = row.get("Nom") or "Agent"
    date_str = date_jour.strftime("%d/%m/%Y") if date_jour else "date inconnue"
    lignes = [f"Entretien de recadrage du {date_str} — {nom} (Login {row.get('Login')})", ""]

    retard = safe_num(row.get("Retard_min"))
    dep = safe_num(row.get("Depart_anticipe_min"))

    if retard > 0:
        lignes.append(
            f"• Prise de poste : retard de {retard:.0f} min (prévu {row.get('Debut_prevu')}, "
            f"arrivée réelle {row.get('Arrivee_reelle')}).")
    else:
        lignes.append("• Prise de poste : conforme au planning.")

    if row.get("Depart_anticipe_ignore"):
        lignes.append("• Fin de poste : non évaluée (export en cours de journée).")
    elif dep > 0:
        lignes.append(
            f"• Fin de poste : départ anticipé de {dep:.0f} min (fin prévue {row.get('Fin_prevu')}, "
            f"départ réel {row.get('Depart_reel')}).")
    else:
        lignes.append("• Fin de poste : conforme au planning.")

    if agent_states is not None and not agent_states.empty:
        top = agent_states[agent_states["Niveau"] == 1]
        parts = [f"{c} {p:.0f} %" for c, p in zip(top["Categorie"], top["Pct"])]
        lignes.append("• Répartition du temps : " + ", ".join(parts) + ".")

    lignes += ["", f"Statut global retenu : {row.get('Statut')}.",
               "Ces éléments sont issus des relevés Vocalcom et doivent être présentés "
               "à l'agent à titre d'échange contradictoire avant toute décision RH."]
    return "\n".join(lignes)
