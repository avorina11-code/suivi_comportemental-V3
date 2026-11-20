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

GROUPE_ATTENTE = "Attente"        # attente de RÉCEPTION (l'agent attend un appel)
GROUPE_HOLD = "En attente"        # MISE EN ATTENTE du client, pendant l'appel
GROUPE_TRAITEMENT = "Traitement"
GROUPE_POST = "Post-travail"
GROUPE_PAUSE = "Pause"
GROUPE_AUTRE = "Autre"
GROUPES = [GROUPE_TRAITEMENT, GROUPE_POST, GROUPE_ATTENTE, GROUPE_PAUSE, GROUPE_AUTRE]

# Libellés affichés à l'écran (les clés internes ci-dessus ne changent pas,
# elles servent de noms de colonnes : « Attente_pct », « En attente_pct »...)
LIBELLE_GROUPE = {
    GROUPE_ATTENTE: "Attente (réception)",
    GROUPE_HOLD: "En attente (mise en attente)",
}


def libelle(groupe: str) -> str:
    return LIBELLE_GROUPE.get(groupe, groupe)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def etat_groupe(categorie: str) -> str:
    """Rattache un libellé d'état Vocalcom à l'un des 5 groupes du dashboard."""
    n = _norm(categorie)
    # « En attente » / « Mise en attente » (hold pendant l'appel) doit être testé
    # AVANT « Attente » (réception) : ce sont deux choses différentes.
    if n.startswith("en attente") or "mise en attente" in n or n.startswith("hold"):
        return GROUPE_HOLD
    if n.startswith("attente"):
        return GROUPE_ATTENTE
    if n.startswith("traitement"):
        return GROUPE_TRAITEMENT
    if n.startswith("post"):
        return GROUPE_POST
    if n.startswith("pause"):
        return GROUPE_PAUSE
    return GROUPE_AUTRE


def _groupe_ligne(categorie, parent, niveau) -> str:
    own = etat_groupe(categorie)
    if niveau == 2 and isinstance(parent, str):
        # un sous-état « En attente » reste une mise en attente, même rangé
        # sous « Traitement » ; les autres héritent du groupe de leur parent.
        return GROUPE_HOLD if own == GROUPE_HOLD else etat_groupe(parent)
    return own


def prepare_states(agents_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Ajoute Groupe + Pct (part du temps total des états de premier niveau
    de l'agent, donc le total fait 100 %).

    Ajoute aussi `Hold_propre` : True pour un sous-état « En attente » rangé
    sous un autre état (ex. Traitement) -> sert à ne pas le compter deux fois.
    """
    if agents_df is None or agents_df.empty:
        return None
    df = agents_df.copy()
    df["Groupe"] = [_groupe_ligne(c, p, n)
                    for c, p, n in zip(df["Categorie"], df["Parent"], df["Niveau"])]
    df["Hold_propre"] = [
        n == 2 and etat_groupe(c) == GROUPE_HOLD
        and not (isinstance(p, str) and etat_groupe(p) == GROUPE_HOLD)
        for c, p, n in zip(df["Categorie"], df["Parent"], df["Niveau"])
    ]
    base = (
        df[df["Niveau"] == 1].groupby("Login")["Temps_total_min"].sum().rename("Base_min")
    )
    df = df.merge(base, on="Login", how="left")
    df["Pct"] = (df["Temps_total_min"] / df["Base_min"].where(df["Base_min"] > 0) * 100).fillna(0.0)
    return df


def hold_est_etat_principal(states: Optional[pd.DataFrame]) -> bool:
    """True si « En attente » est un état de premier niveau dans l'export."""
    return states is not None and bool(((states["Niveau"] == 1) & (states["Groupe"] == GROUPE_HOLD)).any())


def hold_detecte(states: Optional[pd.DataFrame]) -> bool:
    """True si au moins un état « En attente » (n'importe quel niveau) existe."""
    return states is not None and bool((states["Groupe"] == GROUPE_HOLD).any())


def hold_rows(states: pd.DataFrame) -> pd.DataFrame:
    """Lignes « mise en attente » sans double comptage (niveau 1, sinon sous-état)."""
    return states[(states["Groupe"] == GROUPE_HOLD)
                  & ((states["Niveau"] == 1) | (states["Hold_propre"]))]


def groupes_affichables(states: Optional[pd.DataFrame], avec_hold_sous_etat: bool = False) -> list:
    """Groupes à proposer dans les graphiques. « En attente » n'est ajouté
    comme groupe séparé que s'il est un état principal (sinon il est déjà
    dans Traitement et ferait dépasser 100 %), sauf pour les comparaisons."""
    g = list(GROUPES)
    if hold_est_etat_principal(states) or (avec_hold_sous_etat and hold_detecte(states)):
        g.insert(g.index(GROUPE_ATTENTE) + 1, GROUPE_HOLD)
    return g


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
    """1 ligne / agent : minutes et % par groupe + taux de travail.

    Les colonnes « Attente_* » = attente de réception ; « En attente_* » =
    mise en attente pendant l'appel (toujours présentes, à 0 si absentes).
    """
    top = states[states["Niveau"] == 1]
    tous = GROUPES + [GROUPE_HOLD]
    mins = top.pivot_table(index=["Login", "Nom"], columns="Groupe",
                           values="Temps_total_min", aggfunc="sum", fill_value=0.0)
    for g in tous:
        if g not in mins.columns:
            mins[g] = 0.0
    mins = mins[tous]
    total = mins.sum(axis=1)
    pct = mins.div(total.replace(0, float("nan")), axis=0).fillna(0.0) * 100
    out = mins.add_suffix("_min").join(pct.add_suffix("_pct")).reset_index()
    out["Total_min"] = total.values
    out["Taux_travail_pct"] = out[f"{GROUPE_TRAITEMENT}_pct"] + out[f"{GROUPE_POST}_pct"]

    if not hold_est_etat_principal(states):
        # mise en attente = sous-état (déjà incluse dans Traitement) :
        # % calculé sur la même base que les autres états.
        hold = (hold_rows(states).groupby("Login")["Temps_total_min"].sum())
        out[f"{GROUPE_HOLD}_min"] = out["Login"].map(hold).fillna(0.0)
        out[f"{GROUPE_HOLD}_pct"] = (
            out[f"{GROUPE_HOLD}_min"] / out["Total_min"].where(out["Total_min"] > 0) * 100
        ).fillna(0.0)
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
        if "Groupe" in agent_states.columns and "Hold_propre" in agent_states.columns:
            h = hold_rows(agent_states)
            h_min = h["Temps_total_min"].sum()
            if h_min > 0:
                base = top["Temps_total_min"].sum()
                h_pct = h_min / base * 100 if base > 0 else 0.0
                lignes.append(f"• Mise en attente (pendant l'appel) : {h_pct:.1f} % du temps, "
                              f"{fmt_hm(h_min)}, {int(h['Occurences'].sum())} fois.")

    lignes += ["", f"Statut global retenu : {row.get('Statut')}.",
               "Ces éléments sont issus des relevés Vocalcom et doivent être présentés "
               "à l'agent à titre d'échange contradictoire avant toute décision RH."]
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Résumé du jour : retards, absences, agents à suivre (post-appel / mise en attente)
# ---------------------------------------------------------------------------

def _hhmm(t) -> str:
    return t.strftime("%H:%M") if hasattr(t, "strftime") else "—"


def resume_jour(matrice: pd.DataFrame) -> dict:
    """Retards (au-delà du seuil déjà appliqué dans la matrice) et absences
    non justifiées du jour. `pas_commence` = agents dont le shift n'a pas
    encore démarré (non comptés comme absents)."""
    ret = matrice[matrice["Retard_min"].fillna(0) > 0].sort_values("Retard_min", ascending=False)
    retards = pd.DataFrame({
        "Nom": ret["Nom"].fillna("—").values, "Login": ret["Login"].values,
        "Début prévu": [_hhmm(t) for t in ret["Debut_prevu"]],
        "Arrivée": [_hhmm(t) for t in ret["Arrivee_reelle"]],
        "Retard (min)": ret["Retard_min"].round(0).astype(int).values,
    })
    ab = matrice[matrice["Statut"] == STATUT_ABSENT]
    absents = pd.DataFrame({
        "Nom": ab["Nom"].fillna("—").values, "Login": ab["Login"].values,
        "Début prévu": [_hhmm(t) for t in ab["Debut_prevu"]],
        "Fin prévue": [_hhmm(t) for t in ab["Fin_prevu"]],
    })
    return {"retards": retards, "absents": absents,
            "pas_commence": int((matrice["Statut"] == STATUT_PAS_COMMENCE).sum())}


PLANCHER_SUIVI_PCT = 2.0   # en mode « équipe », jamais de suivi sous ce % du temps


def suivi_post_hold(states: pd.DataFrame, agent_pct: pd.DataFrame, *, mode: str = "equipe",
                    k: float = 1.0, seuil_post: float = 15.0, seuil_hold: float = 8.0,
                    min_total_min: float = 30.0):
    """Agents qui méritent un suivi aujourd'hui sur le post-appel (Post-travail)
    et la mise en attente (En attente).

    mode « equipe » : seuil = moyenne équipe + k × écart-type (repli sur les
    seuils fixes si moins de 3 agents évaluables). mode « fixe » : seuils en %.
    Les agents observés moins de `min_total_min` minutes ne sont pas évalués.

    Retourne (DataFrame par agent, méta : seuils réellement utilisés, moyennes).
    """
    ap = agent_pct[agent_pct["Total_min"] >= min_total_min].copy()

    post = (states[(states["Niveau"] == 1) & (states["Groupe"] == GROUPE_POST)]
            .groupby("Login").agg(Post_min=("Temps_total_min", "sum"), Post_nb=("Occurences", "sum")))
    ap = ap.merge(post, on="Login", how="left")
    hold_ok = hold_detecte(states)
    if hold_ok:
        hold = hold_rows(states).groupby("Login").agg(Hold_min=("Temps_total_min", "sum"),
                                                       Hold_nb=("Occurences", "sum"))
        ap = ap.merge(hold, on="Login", how="left")
    else:
        ap["Hold_min"] = 0.0
        ap["Hold_nb"] = 0
    for c in ("Post_min", "Post_nb", "Hold_min", "Hold_nb"):
        ap[c] = ap[c].fillna(0)

    ap["Post_pct"] = ap[f"{GROUPE_POST}_pct"]
    ap["Hold_pct"] = ap[f"{GROUPE_HOLD}_pct"] if hold_ok else 0.0
    ap["Post_moy_s"] = (ap["Post_min"] * 60 / ap["Post_nb"].where(ap["Post_nb"] > 0)).round(0)
    ap["Hold_moy_s"] = (ap["Hold_min"] * 60 / ap["Hold_nb"].where(ap["Hold_nb"] > 0)).round(0)

    equipe = mode == "equipe" and len(ap) >= 3

    def _seuil(serie: pd.Series, fixe: float) -> float:
        if equipe:
            return max(float(serie.mean() + k * serie.std(ddof=0)), PLANCHER_SUIVI_PCT)
        return float(fixe)

    s_post, s_hold = _seuil(ap["Post_pct"], seuil_post), _seuil(ap["Hold_pct"], seuil_hold)
    ap["Suivi_post"] = (ap["Post_pct"] >= s_post) & (ap["Post_pct"] > 0)
    ap["Suivi_hold"] = hold_ok & (ap["Hold_pct"] >= s_hold) & (ap["Hold_pct"] > 0)
    meta = {
        "mode": "equipe" if equipe else "fixe",
        "mode_demande": mode, "k": k, "hold_dispo": bool(hold_ok),
        "seuil_post": s_post, "seuil_hold": s_hold,
        "moy_post": float(ap["Post_pct"].mean()) if len(ap) else 0.0,
        "moy_hold": float(ap["Hold_pct"].mean()) if len(ap) else 0.0,
        "agents_evalues": int(len(ap)), "agents_ecartes": int(len(agent_pct) - len(ap)),
    }
    cols = ["Login", "Nom", "Total_min", "Post_pct", "Post_moy_s", "Suivi_post",
            "Hold_pct", "Hold_moy_s", "Hold_nb", "Suivi_hold"]
    return ap[cols].reset_index(drop=True), meta


def resume_json(date_jour: Optional[dt.date], res: dict,
                suivi: Optional[pd.DataFrame], meta: Optional[dict]) -> dict:
    """Version sérialisable du résumé (inscrite dans la sauvegarde JSON, à titre informatif)."""
    out = {
        "date": date_jour.isoformat() if date_jour else None,
        "retards": res["retards"].to_dict("records"),
        "absences": res["absents"].to_dict("records"),
        "suivi_post_appel": [], "suivi_mise_en_attente": [], "criteres_suivi": None,
    }
    if suivi is not None and meta is not None:
        out["suivi_post_appel"] = [
            {"Nom": r.Nom, "Login": r.Login, "pct": round(float(r.Post_pct), 1)}
            for r in suivi[suivi["Suivi_post"]].sort_values("Post_pct", ascending=False).itertuples()]
        out["suivi_mise_en_attente"] = [
            {"Nom": r.Nom, "Login": r.Login, "pct": round(float(r.Hold_pct), 1)}
            for r in suivi[suivi["Suivi_hold"]].sort_values("Hold_pct", ascending=False).itertuples()]
        out["criteres_suivi"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in meta.items()}
    return out


def resume_texte(date_jour: Optional[dt.date], res: dict,
                 suivi: Optional[pd.DataFrame], meta: Optional[dict]) -> str:
    """Message prêt à copier (WhatsApp / mail)."""
    d = date_jour.strftime("%d/%m/%Y") if date_jour else "—"
    L = [f"📋 Résumé du {d}", ""]
    r, a = res["retards"], res["absents"]
    L.append(f"⏱️ Retards : {len(r)}")
    L += [f"  • {x.Nom} ({x.Login}) : +{x._5} min (prévu {x._3}, arrivé {x.Arrivée})"
          for x in r.rename(columns={"Début prévu": "_3", "Retard (min)": "_5"}).itertuples()]
    L += ["", f"🚫 Absences non justifiées : {len(a)}"]
    L += [f"  • {x.Nom} ({x.Login}) : prévu {x._3}–{x._4}"
          for x in a.rename(columns={"Début prévu": "_3", "Fin prévue": "_4"}).itertuples()]
    L += ["", "🎯 À suivre aujourd'hui"]
    if suivi is None or meta is None:
        L.append("  (Agents report non disponible)")
        return "\n".join(L)
    p = suivi[suivi["Suivi_post"]].sort_values("Post_pct", ascending=False)
    L.append(f"  Post-appel ({len(p)}) — seuil ≥ {meta['seuil_post']:.1f} % :")
    L += [f"    • {x.Nom} ({x.Login}) : {x.Post_pct:.1f} %" for x in p.itertuples()] or ["    • personne"]
    if meta["hold_dispo"]:
        h = suivi[suivi["Suivi_hold"]].sort_values("Hold_pct", ascending=False)
        L.append(f"  Mise en attente ({len(h)}) — seuil ≥ {meta['seuil_hold']:.1f} % :")
        L += [f"    • {x.Nom} ({x.Login}) : {x.Hold_pct:.1f} %" for x in h.itertuples()] or ["    • personne"]
    else:
        L.append("  Mise en attente : non détectée dans l'export")
    return "\n".join(L)
