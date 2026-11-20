# -*- coding: utf-8 -*-
"""
app.py — Dashboard d'adhérence planning & suivi comportemental (CSA).

Sources : Planning Hebdo + « Agents activities by dates » (arrivée/départ)
          + « Agents report » (états : Attente, Traitement, Post-travail, Pause...)
Lancement : streamlit run app.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import analysis as A
import charts as C
import demo as D
import parsers as P
import theme as T

st.set_page_config(page_title="Adhérence Planning CSA", page_icon="📞", layout="wide")
T.inject_global_style()

for k, v in {"planning_df": None, "activities_df": None, "agents_df": None,
             "detections": [], "export_date": None, "is_demo": False}.items():
    st.session_state.setdefault(k, v)

# ==========================================================================
# SIDEBAR
# ==========================================================================
st.sidebar.title("📁 Import des fichiers")
planning_file = st.sidebar.file_uploader("Planning Hebdo (théorique) — .xlsx", type=["xlsx"],
    help="Feuille 'Planning_Hebdo' avec Matricule_RH, Login_Vocalcom, Nom_Prenom et heures par jour.")
vocalcom_files = st.sidebar.file_uploader("Exports Vocalcom — .xls / .xlsx", type=["xls", "xlsx"],
    accept_multiple_files=True,
    help="'Agents activities by dates' (arrivée/départ) et/ou 'Agents report' (états). "
         "Le type est détecté automatiquement d'après le contenu.")

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Paramètres")
export_temps_reel = st.sidebar.checkbox("Export en cours de journée (Temps Réel)", value=False,
    help="Les pourcentages couvrent la période jusqu'à la fin de l'export. Désactive l'alerte de "
         "départ anticipé pour les agents dont la fin de shift est après l'heure d'extraction.")
heure_extraction = st.sidebar.time_input("Heure d'extraction", value=dt.time(13, 0)) if export_temps_reel else None
seuil_retard = st.sidebar.number_input("Seuil de retard toléré (min)", 0, 60, 5, 1)
date_manuelle = st.sidebar.date_input("Date de l'export (si non détectée)", value=dt.date.today(), format="DD/MM/YYYY")

st.sidebar.markdown("---")
c1, c2 = st.sidebar.columns(2)
process_btn = c1.button("🔄 Analyser", type="primary", use_container_width=True)
demo_btn = c2.button("🧪 Démo", use_container_width=True, help="Charge des données fictives pour tester le dashboard.")


def _process_uploads():
    det = []
    st.session_state.is_demo = False
    if planning_file is not None:
        try:
            st.session_state.planning_df = P.load_planning(planning_file)
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Erreur lecture Planning Hebdo : {exc}")
            st.session_state.planning_df = None
    st.session_state.activities_df = None
    st.session_state.agents_df = None
    st.session_state.export_date = None
    labels = {
        "activities_by_date": "✅ Agents activities by dates (arrivée / départ)",
        "agents_report": "✅ Agents report (états)",
        "pause_report": "⏭️ Agents pause report — ignoré (plus utilisé)",
        "unknown": "⚠️ Type non reconnu — fichier ignoré",
    }
    for f in vocalcom_files or []:
        try:
            rtype, result, d = P.load_any_vocalcom_export(f, filename=f.name)
        except Exception as exc:  # noqa: BLE001
            det.append((f.name, f"❌ Erreur : {exc}"))
            continue
        det.append((f.name, labels.get(rtype, rtype)))
        if rtype == "activities_by_date":
            st.session_state.activities_df = result
        elif rtype == "agents_report":
            st.session_state.agents_df = result
        if d and rtype in ("activities_by_date", "agents_report"):
            st.session_state.export_date = d
    st.session_state.detections = det


if process_btn:
    _process_uploads()
if demo_btn:
    jour = dt.date.today() - dt.timedelta(days=1)
    pl, ac, ag = D.build_demo(jour)
    st.session_state.update(planning_df=pl, activities_df=ac, agents_df=ag, export_date=jour,
                            is_demo=True, detections=[("Données fictives", "🧪 Mode démo")])

if st.session_state.detections:
    with st.sidebar.expander("🔍 Détection automatique", expanded=True):
        for fname, label in st.session_state.detections:
            st.write(f"**{fname}**")
            st.caption(label)

# ==========================================================================
# GARDE-FOUS
# ==========================================================================
T.hero_banner("📞 Dashboard Adhérence & Suivi Comportemental — CSA",
              "Ponctualité (Agents activities by dates) · Répartition des états (Agents report)")

planning_df = st.session_state.planning_df
activities_df = st.session_state.activities_df
agents_df = st.session_state.agents_df

if planning_df is None or (activities_df is None and agents_df is None):
    st.info("👈 Importez le **Planning Hebdo** et au moins un export Vocalcom, puis cliquez sur "
            "**Analyser** (ou sur **Démo** pour essayer avec des données fictives).")
    st.markdown("""
| Fichier | Rôle |
|---|---|
| **Planning Hebdo** | Heures théoriques de début/fin par agent et par jour |
| **Agents activities by dates** | Arrivée / départ réels → retards, départs anticipés, absences (jointure par Login) |
| **Agents report** | États : Attente, Traitement, Post-travail, Pause (+ sous-catégories) → dashboard et fiches |
""")
    st.stop()

# ==========================================================================
# CALCULS
# ==========================================================================
report_date = st.session_state.export_date or date_manuelle
jour_semaine = P.date_to_jour_fr(report_date)
st.caption(f"📅 Date de l'export : **{report_date.strftime('%d/%m/%Y')}** → colonne planning : **{jour_semaine}**"
           + (" · 🧪 données fictives" if st.session_state.is_demo else "")
           + (f" · ⏱️ temps réel jusqu'à **{heure_extraction.strftime('%H:%M')}**" if heure_extraction else ""))

shift_today = P.get_shift_for_day(planning_df, jour_semaine)
states = A.prepare_states(agents_df)
agent_pct = A.agent_group_pct(states) if states is not None else None
logins_report = set(states["Login"]) if states is not None else set()

matrice = A.build_matrice_comportementale(
    shift_today, activities_df, logins_report,
    seuil_retard_min=seuil_retard, export_temps_reel=export_temps_reel,
    heure_extraction=heure_extraction, report_date=report_date)

hors_planning = ((set(activities_df["Login"]) if activities_df is not None else set()) | logins_report) \
    - set(shift_today["Login"])

# ==========================================================================
# AFFICHAGE : toute la zone principale = front animé HTML / CSS / JS
# ==========================================================================
import streamlit.components.v1 as components
import front_data as FD

_data = FD.build(planning_df, activities_df, agents_df, report_date, seuil_retard,
                 export_temps_reel, heure_extraction, demo=st.session_state.is_demo)
_html = FD.render_html(_data)
if hasattr(st, "iframe"):  # Streamlit récent : st.components.v1.html est déprécié
    st.iframe(_html, height=1500)
else:
    components.html(_html, height=1500, scrolling=True)
