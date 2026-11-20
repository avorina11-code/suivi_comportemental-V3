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
import persistence as S
import theme as T

st.set_page_config(page_title="Adhérence Planning CSA", page_icon="📞", layout="wide")
T.inject_global_style()

for k, v in {"planning_df": None, "activities_df": None, "agents_df": None,
             "detections": [], "export_date": None, "is_demo": False,
             "source_json": None}.items():
    st.session_state.setdefault(k, v)

# Préférences : valeurs par défaut, complétées par le fichier local « sauvegardes/preferences.json »
if not st.session_state.get("_prefs_init"):
    for _k, _v in S.prefs_to_state(S.load_local_prefs()).items():
        st.session_state.setdefault(_k, _v)
    st.session_state["_prefs_init"] = True

# Import JSON demandé au run précédent : appliqué ici, avant la création des widgets.
_pending = st.session_state.pop("_pending_import", None)
if _pending:
    _parsed, _use_prefs, _use_data = _pending
    _msgs = []
    if _use_prefs and _parsed["preferences"]:
        st.session_state.update(S.prefs_to_state({**S.state_to_prefs(st.session_state),
                                                  **_parsed["preferences"]}))
        _msgs.append("préférences appliquées")
    if _use_data and _parsed["planning"] is not None:
        st.session_state.update(planning_df=_parsed["planning"], activities_df=_parsed["activites"],
                               agents_df=_parsed["agents"], export_date=_parsed["date"],
                               is_demo=_parsed["demo"], source_json=_parsed["exporte_le"] or "JSON",
                               detections=[("Sauvegarde JSON", "💾 Journée restaurée")])
        _msgs.append(f"journée du {_parsed['date'].strftime('%d/%m/%Y')} chargée" if _parsed["date"]
                     else "journée chargée")
    st.session_state["_flash"] = "✅ Import réussi : " + " · ".join(_msgs) if _msgs else None

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
export_temps_reel = st.sidebar.checkbox("Export en cours de journée (Temps Réel)", key="pref_export_temps_reel",
    help="Les pourcentages couvrent la période jusqu'à la fin de l'export. Désactive l'alerte de "
         "départ anticipé pour les agents dont la fin de shift est après l'heure d'extraction.")
_h = st.sidebar.time_input("Heure d'extraction", key="pref_heure_extraction", disabled=not export_temps_reel)
heure_extraction = _h if export_temps_reel else None
seuil_retard = st.sidebar.number_input("Seuil de retard toléré (min)", min_value=0, max_value=60, step=1,
                                       key="pref_seuil_retard")
with st.sidebar.expander("🎯 Critères de suivi (post-appel / mise en attente)"):
    st.selectbox("Méthode", list(S.MODES_SUIVI), format_func=S.MODES_SUIVI.get, key="pref_mode_suivi",
                 help="« Relatif à l'équipe » repère les agents nettement au-dessus de la moyenne du jour ; "
                      "« Seuils fixes » utilise les % ci-dessous.")
    _equipe = st.session_state.get("pref_mode_suivi") == "equipe"
    st.number_input("N écarts-types au-dessus de la moyenne", min_value=0.0, max_value=5.0, step=0.5,
                    key="pref_k_suivi", disabled=not _equipe)
    st.number_input("Seuil fixe Post-travail (% du temps)", min_value=0.0, max_value=100.0, step=1.0,
                    key="pref_seuil_post", disabled=_equipe)
    st.number_input("Seuil fixe mise en attente (% du temps)", min_value=0.0, max_value=100.0, step=1.0,
                    key="pref_seuil_hold", disabled=_equipe)
    st.number_input("Temps observé minimum (min)", min_value=0, max_value=600, step=5,
                    key="pref_min_total_min",
                    help="Les agents observés moins longtemps ne sont pas évalués (évite les faux positifs).")
date_manuelle = st.sidebar.date_input("Date de l'export (si non détectée)", value=dt.date.today(), format="DD/MM/YYYY")

st.sidebar.markdown("---")
c1, c2 = st.sidebar.columns(2)
process_btn = c1.button("🔄 Analyser", type="primary", use_container_width=True)
demo_btn = c2.button("🧪 Démo", use_container_width=True, help="Charge des données fictives pour tester le dashboard.")


def _process_uploads():
    det = []
    st.session_state.is_demo = False
    st.session_state.source_json = None
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
                            is_demo=True, source_json=None,
                            detections=[("Données fictives", "🧪 Mode démo")])

if st.session_state.detections:
    with st.sidebar.expander("🔍 Détection automatique", expanded=True):
        for fname, label in st.session_state.detections:
            st.write(f"**{fname}**")
            st.caption(label)

# ==========================================================================
# SAUVEGARDE / IMPORT JSON
# ==========================================================================
def _apercu_import(parsed: dict, key: str) -> None:
    """Aperçu d'une sauvegarde lue + bouton d'import (appliqué au run suivant)."""
    quand = (parsed["exporte_le"] or "")[:16].replace("T", " ")
    if parsed["type"] == "journee":
        nb = len(parsed["planning"]) if parsed["planning"] is not None else 0
        jour = parsed["date"].strftime("%d/%m/%Y") if parsed["date"] else "date inconnue"
        st.info(f"**Journée du {jour}** · {nb} agent(s) au planning"
                + (" · 🧪 données fictives" if parsed["demo"] else "")
                + (f" · exportée le {quand}" if quand else ""))
    else:
        st.info("**Préférences seules**" + (f" · exportées le {quand}" if quand else ""))
    use_data = parsed["type"] == "journee" and st.checkbox(
        "Charger les données de la journée", value=True, key=f"imp_data_{key}")
    use_prefs = bool(parsed["preferences"]) and st.checkbox(
        "Appliquer les préférences (seuils, critères de suivi…)", value=True, key=f"imp_prefs_{key}")
    if st.button("📥 Importer", type="primary", key=f"imp_btn_{key}", disabled=not (use_data or use_prefs)):
        st.session_state["_pending_import"] = (parsed, use_prefs, use_data)
        st.rerun()


def render_sauvegarde(ctx: dict | None) -> None:
    """ctx = None : seulement l'import (aucune donnée chargée pour l'instant)."""
    prefs = S.state_to_prefs(st.session_state)

    st.markdown("#### 📥 Importer une sauvegarde")
    up = st.file_uploader("Fichier JSON (journée ou préférences)", type=["json"], key="json_import_file")
    if up is not None:
        try:
            _apercu_import(S.parse_payload(up.getvalue()), "fichier")
        except ValueError as exc:
            st.error(str(exc))

    if ctx is not None:
        st.markdown("#### 📤 Exporter")
        e1, e2 = st.columns(2)
        with e1:
            payload = S.build_journee(ctx["report_date"], ctx["planning_df"], ctx["activities_df"],
                                      ctx["agents_df"], prefs, ctx["resume"], ctx["is_demo"])
            st.download_button("⬇️ Journée complète (données + préférences + résumé)", data=S.dumps(payload),
                               file_name=f"journee_{ctx['report_date'].isoformat()}.json",
                               mime="application/json", use_container_width=True, type="primary")
            st.caption("Permet de rouvrir la journée plus tard sans réimporter les fichiers Excel.")
        with e2:
            st.download_button("⬇️ Préférences seules", data=S.dumps(S.build_preferences(prefs)),
                               file_name="preferences.json", mime="application/json", use_container_width=True)
            st.caption("Seuil de retard, mode temps réel, critères de suivi.")

    st.markdown("#### 💽 Sauvegardes sur ce disque")
    if ctx is not None:
        d1, d2 = st.columns(2)
        if d1.button("💾 Enregistrer cette journée", use_container_width=True):
            try:
                chemin = S.save_local_journee(payload)
                st.success(f"Journée enregistrée : `{chemin.name}`")
            except OSError as exc:
                st.error(f"Écriture impossible : {exc}")
        if d2.button("⭐ Enregistrer mes préférences", use_container_width=True,
                     help="Rechargées automatiquement au prochain lancement."):
            try:
                S.save_local_prefs(S.build_preferences(prefs))
                st.success("Préférences enregistrées (rechargées au prochain lancement).")
            except OSError as exc:
                st.error(f"Écriture impossible : {exc}")
    saved = S.list_local_journees()
    if saved:
        choix = st.selectbox("Journées enregistrées", saved, key="local_choice")
        try:
            _apercu_import(S.parse_payload(S.read_local_journee(choix)), "disque")
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    else:
        st.caption("Aucune journée enregistrée sur ce disque pour l'instant.")
    st.caption("ℹ️ Sur un hébergement type Streamlit Cloud, le disque est temporaire : utilisez plutôt "
               "les exports JSON ci-dessus pour conserver vos données.")


# ==========================================================================
# GARDE-FOUS
# ==========================================================================
T.hero_banner("📞 Dashboard Adhérence & Suivi Comportemental — CSA",
              "Ponctualité (Agents activities by dates) · Répartition des états (Agents report)")

if st.session_state.get("_flash"):
    st.success(st.session_state.pop("_flash"))
st.session_state.pop("_flash", None)

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
    with st.expander("💾 Ou rouvrir une sauvegarde JSON (journée / préférences)"):
        render_sauvegarde(None)
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

if activities_df is None:
    st.warning("« Agents activities by dates » non importé : arrivées, départs et retards indisponibles.")
if states is None:
    st.warning("« Agents report » non importé : la répartition des états (Attente, Post-travail, Pause…) est indisponible.")
if hors_planning:
    # Noms : "Agents report" (nom complet) en priorité, sinon nom tronqué de "Agents activities"
    noms_hp = {}
    if activities_df is not None and "Nom_tronque" in activities_df.columns:
        noms_hp.update(activities_df.dropna(subset=["Nom_tronque"]).drop_duplicates("Login")
                       .set_index("Login")["Nom_tronque"].to_dict())
    if states is not None and "Nom" in states.columns:
        noms_hp.update(states.dropna(subset=["Nom"]).drop_duplicates("Login")
                       .set_index("Login")["Nom"].to_dict())
    st.warning(f"⚠️ {len(hors_planning)} login(s) dans Vocalcom mais absents du planning du {jour_semaine}")
    st.dataframe(
        pd.DataFrame({"Login": sorted(hors_planning),
                      "Nom": [noms_hp.get(lg, "—") for lg in sorted(hors_planning)]}),
        hide_index=True, use_container_width=True,
        height=min(38 * (len(hors_planning) + 1), 300))

prefs = S.state_to_prefs(st.session_state)
resume = A.resume_jour(matrice)
if agent_pct is not None:
    suivi, suivi_meta = A.suivi_post_hold(
        states, agent_pct, mode=prefs["mode_suivi"], k=prefs["k_suivi"], seuil_post=prefs["seuil_post"],
        seuil_hold=prefs["seuil_hold"], min_total_min=prefs["min_total_min"])
else:
    suivi = suivi_meta = None

if st.session_state.source_json:
    st.caption(f"💾 Données restaurées depuis une sauvegarde JSON ({str(st.session_state.source_json)[:16].replace('T', ' ')}).")

tab1, tab_res, tab2, tab3, tab4, tab_save = st.tabs(
    ["🏠 Dashboard", "📝 Résumé du jour", "📊 États par agent", "📋 Matrice", "🧑 Fiche individuelle",
     "💾 Sauvegarde"])

# ==========================================================================
# 1. DASHBOARD
# ==========================================================================
with tab1:
    planifies = matrice[matrice["Statut_planning"] == "TRAVAIL"]
    presents = int(planifies["Statut"].isin([A.STATUT_OK, A.STATUT_ECART]).sum())
    absents = int((matrice["Statut"] == A.STATUT_ABSENT).sum())
    retards = int((matrice["Retard_min"].fillna(0) > 0).sum())
    deps = int((matrice["Depart_anticipe_min"].fillna(0) > 0).sum())
    taux_pres = presents / len(planifies) * 100 if len(planifies) else 0

    kpis = [
        {"label": f"Présents / {len(planifies)} planifiés", "value": presents, "icon": "🟢", "color": T.SUCCESS},
        {"label": "Taux de présence", "value": round(taux_pres, 1), "icon": "📈", "color": T.PRIMARY, "suffix": " %"},
        {"label": "Absences non justifiées", "value": absents, "icon": "🚫", "color": T.DANGER, "alert": absents > 0},
        {"label": "Retards", "value": retards, "icon": "⏱️", "color": T.WARNING, "alert": retards > 0},
        {"label": "Départs anticipés", "value": deps, "icon": "🚪", "color": T.ACCENT},
    ]
    if agent_pct is not None:
        tot = agent_pct["Total_min"].sum()
        def share(g):
            return agent_pct[f"{g}_min"].sum() / tot * 100 if tot else 0
        kpis += [
            {"label": "Taux de travail (Traitement + Post-trav.)",
             "value": round(share(A.GROUPE_TRAITEMENT) + share(A.GROUPE_POST), 1), "icon": "💼",
             "color": "#0E7C86", "suffix": " %"},
            {"label": "Attente (réception d'appel)", "value": round(share(A.GROUPE_ATTENTE), 1), "icon": "📞",
             "color": "#E08E1D", "suffix": " %"},
        ]
        if A.hold_detecte(states):
            kpis.append({"label": "En attente (mise en attente en appel)", "value": round(share(A.GROUPE_HOLD), 1),
                         "icon": "⏸️", "color": "#3B82C4", "suffix": " %"})
        kpis.append({"label": "En pause", "value": round(share(A.GROUPE_PAUSE), 1), "icon": "☕",
                     "color": "#E5646A", "suffix": " %"})
    T.render_kpi_row(kpis[:5], height=125)
    if len(kpis) > 5:
        T.render_kpi_row(kpis[5:], height=125)
        st.caption("Pourcentages calculés sur le temps cumulé de tous les agents présents dans « Agents report »"
                   + (" — période écoulée jusqu'à la fin de l'export (temps réel)." if export_temps_reel else "."))
        T.definition_chips([
            ("#E08E1D", "Attente", "l'agent attend de recevoir un appel (réception)"),
            ("#3B82C4", "En attente", "mise en attente du client pendant l'appel"),
        ])
        if A.hold_detecte(states) and not A.hold_est_etat_principal(states):
            st.caption("ℹ️ « En attente » est un sous-état de Traitement dans l'export : il est déjà compris "
                       "dans le % Traitement / Taux de travail, il n'est pas ajouté en plus.")
        elif not A.hold_detecte(states):
            st.caption("ℹ️ Aucun état « En attente » (mise en attente) trouvé dans l'export — "
                       "voir « Correspondance des états » en bas de cet onglet.")

    if states is not None:
        st.markdown("### Répartition du temps")
        left, right = st.columns([1, 1])
        with left:
            vue = st.radio("Niveau de détail", ["États principaux", "Détail (sous-états)"], horizontal=True)
            niv = 1 if vue.startswith("États") else 2
            totals = A.team_state_totals(states, niv)
            if totals.empty:
                st.info("Pas de sous-états dans l'export — affichage des états principaux.")
                totals = A.team_state_totals(states, 1)
            with st.container(border=True):
                st.plotly_chart(C.donut_etats(totals), use_container_width=True)
        with right:
            with st.container(border=True):
                st.plotly_chart(C.sunburst_etats(states), use_container_width=True)

        with st.container(border=True):
            st.markdown("**Détail chiffré**")
            show = totals.copy()
            show["Durée"] = show["Temps_min"].apply(A.fmt_hm)
            show["% du temps"] = show["Pct"].round(1)
            st.dataframe(show[["Categorie", "Groupe", "Durée", "Occurences", "% du temps"]],
                         hide_index=True, use_container_width=True,
                         column_config={"% du temps": st.column_config.ProgressColumn(
                             "% du temps", min_value=0, max_value=100, format="%.1f %%")})

    if states is not None:
        with st.expander("🔎 Correspondance des états (libellé Vocalcom → groupe)"):
            corr = (states.groupby(["Niveau", "Parent", "Categorie", "Groupe"], dropna=False, as_index=False)
                    .agg(Temps_min=("Temps_total_min", "sum"), Occurences=("Occurences", "sum")))
            corr["Groupe"] = corr["Groupe"].map(A.libelle)
            corr["Parent"] = corr["Parent"].fillna("—")
            st.dataframe(corr, hide_index=True, use_container_width=True)
            st.caption("« Attente » = attente de réception · « En attente » = mise en attente pendant l'appel. "
                       "Si un libellé est mal classé, adaptez `analysis.etat_groupe`.")

    st.markdown("### Ponctualité & présence")
    a1, a2 = st.columns(2)
    with a1:
        with st.container(border=True):
            st.plotly_chart(C.statuts_donut(matrice), use_container_width=True)
    with a2:
        with st.container(border=True):
            if matrice["Ecart_arrivee_min"].notna().any():
                st.plotly_chart(C.arrivees_chart(matrice, seuil_retard), use_container_width=True)
            else:
                st.info("Aucune heure d'arrivée disponible (importez « Agents activities by dates »).")

# ==========================================================================
# 2. ÉTATS PAR AGENT
# ==========================================================================
with tab2:
    if states is None:
        st.info("Importez « Agents report » pour voir les états par agent.")
    else:
        st.markdown("### Classement par état")
        f1, f2, f3 = st.columns([2, 1, 1])
        niv2 = f2.radio("Niveau", ["Principal", "Sous-état"], horizontal=True)
        niveau = 1 if niv2 == "Principal" else 2
        cats = sorted(states[states["Niveau"] == niveau]["Categorie"].unique())
        if not cats:
            cats = sorted(states["Categorie"].unique()); niveau = 1
        defaut = next((i for i, c in enumerate(cats) if "post" in c.lower()), 0)
        cat = f1.selectbox("État à analyser", cats, index=defaut)
        top = f3.slider("Nombre d'agents", 3, max(5, states["Login"].nunique()), min(15, states["Login"].nunique()))
        with st.container(border=True):
            st.plotly_chart(C.ranking_etat(states, cat, top, niveau), use_container_width=True)

        st.markdown("### Vue d'ensemble de l'équipe")
        g1, g2 = st.columns([3, 1])
        grp_opts = A.groupes_affichables(states)
        groupes = g1.multiselect("Groupes affichés", grp_opts, format_func=A.libelle,
                                 default=[g for g in grp_opts if g != A.GROUPE_AUTRE or agent_pct[f"{g}_min"].sum() > 0])
        tri = g2.selectbox("Trier par", [g for g in grp_opts if g != A.GROUPE_AUTRE], format_func=A.libelle)
        if groupes:
            with st.container(border=True):
                st.plotly_chart(C.stacked_agents(agent_pct, tri, groupes), use_container_width=True)

# ==========================================================================
# 3. MATRICE
# ==========================================================================
with tab3:
    # --- Filtre par début de shift (sélection vide = tous les agents) ---
    _hh = matrice["Debut_prevu"].apply(lambda t: t.strftime("%H:%M") if hasattr(t, "strftime") else None)
    _cnt = _hh.value_counts().sort_index()
    _opts = {f"🕐 {h}  ·  {n}": h for h, n in _cnt.items()}
    _sans = int(_hh.isna().sum())
    if _sans:
        _opts[f"— Sans horaire  ·  {_sans}"] = None
    st.markdown("**Début de shift**")
    if hasattr(st, "pills"):
        _sel = st.pills("Début de shift", list(_opts), selection_mode="multi",
                        label_visibility="collapsed", key="filtre_debut_shift")
    else:  # anciennes versions de Streamlit
        _sel = st.multiselect("Début de shift", list(_opts), label_visibility="collapsed",
                              key="filtre_debut_shift")
    _sel = _sel or []
    st.caption("Aucune sélection = tous les débuts de shift." if not _sel
               else f"{len(_sel)} horaire(s) sélectionné(s)")

    statuts = matrice["Statut"].unique().tolist()
    filtre = st.multiselect("Filtrer par statut", statuts, default=statuts)
    cols = ["Matricule_RH", "Login", "Nom", "Statut_planning", "Debut_prevu", "Fin_prevu",
            "Arrivee_reelle", "Depart_reel", "Retard_min", "Depart_anticipe_min", "Statut"]
    dfa = matrice[matrice["Statut"].isin(filtre)].copy()
    if _sel:
        _vals = [_opts[k] for k in _sel]
        _mask = _hh.loc[dfa.index].isin([v for v in _vals if v is not None])
        if None in _vals:
            _mask |= _hh.loc[dfa.index].isna()
        dfa = dfa[_mask]
    if agent_pct is not None:
        dfa = dfa.merge(agent_pct[["Login", "Taux_travail_pct", "Post-travail_pct", "Attente_pct",
                                    "En attente_pct", "Pause_pct"]],
                        on="Login", how="left").rename(columns={
            "Taux_travail_pct": "Travail_%", "Post-travail_pct": "Post-travail_%",
            "Attente_pct": "Attente_%", "En attente_pct": "En attente_%", "Pause_pct": "Pause_%"})
        cols += ["Travail_%", "Post-travail_%", "Attente_%"]
        if A.hold_detecte(states):
            cols.append("En attente_%")
        cols.append("Pause_%")
    num = ["Retard_min", "Depart_anticipe_min"] + [c for c in cols if c.endswith("_%")]
    with st.container(border=True):
        st.dataframe(T.style_matrice(dfa[cols], status_col="Statut", numeric_cols=num),
                     use_container_width=True, hide_index=True)
    st.download_button("📥 Télécharger (CSV)", dfa[cols].to_csv(index=False).encode("utf-8-sig"),
                       f"matrice_{report_date.isoformat()}.csv", "text/csv")

# ==========================================================================
# 4. FICHE INDIVIDUELLE
# ==========================================================================
with tab4:
    liste = matrice[["Login", "Nom"]].drop_duplicates()
    liste["label"] = liste["Login"] + " — " + liste["Nom"].fillna("")
    choix = st.selectbox("Sélectionner un agent", liste["label"].tolist())
    login = choix.split(" — ")[0]
    row = matrice[matrice["Login"] == login].iloc[0]
    ag_states = states[states["Login"] == login] if states is not None else None

    st.markdown(f"## {row['Nom']}  \n**Login :** {row['Login']} — **Matricule :** {row['Matricule_RH']}")
    st.markdown(f"**Statut du jour :** {T.status_badge(row['Statut'])}", unsafe_allow_html=True)

    retard = A.safe_num(row["Retard_min"]); dep = A.safe_num(row["Depart_anticipe_min"])
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("#### 🕐 Prise de poste")
            st.write(f"Prévu : {row['Debut_prevu']} · Réel : {row['Arrivee_reelle']}")
            T.render_kpi_row([{"label": "Retard", "value": round(retard), "icon": "⏱️", "suffix": " min",
                               "color": T.DANGER if retard else T.SUCCESS, "alert": retard > 0}], height=110)
        with c2:
            st.markdown("#### 🏁 Fin de poste")
            st.write(f"Prévu : {row['Fin_prevu']} · Réel : {row['Depart_reel']}")
            if row["Depart_anticipe_ignore"]:
                st.info("Non évalué (export temps réel)")
            else:
                T.render_kpi_row([{"label": "Départ anticipé", "value": round(dep), "icon": "🚪", "suffix": " min",
                                   "color": T.DANGER if dep else T.SUCCESS, "alert": dep > 0}], height=110)
        with c3:
            st.markdown("#### ⏳ Temps de travail")
            st.write("Durée Vocalcom (activities)")
            T.render_kpi_row([{"label": "Durée de travail", "value": round(A.safe_num(row["Duree_travail_min"])),
                               "icon": "💼", "suffix": " min", "color": T.PRIMARY}], height=110)

    fig_g = C.gantt_agent(row)
    if fig_g is not None:
        with st.container(border=True):
            st.plotly_chart(fig_g, use_container_width=True)

    if ag_states is None or ag_states.empty:
        st.info("Aucun détail d'états pour cet agent (absent de « Agents report »).")
    else:
        st.markdown("### 📊 Répartition de son temps")
        top1 = ag_states[ag_states["Niveau"] == 1]
        pt = top1[top1["Groupe"] == A.GROUPE_POST]
        post_pct = pt["Pct"].sum(); post_min = pt["Temps_total_min"].sum()
        post_occ = int(pt["Occurences"].sum())
        post_moy = post_min * 60 / post_occ if post_occ else 0
        team_post = agent_pct[f"{A.GROUPE_POST}_pct"].mean()
        gp = {g: top1[top1["Groupe"] == g]["Pct"].sum() for g in A.GROUPES}
        hold_ag = A.hold_rows(ag_states)
        hold_min = hold_ag["Temps_total_min"].sum()
        hold_occ = int(hold_ag["Occurences"].sum())
        hold_pct = hold_min / top1["Temps_total_min"].sum() * 100 if top1["Temps_total_min"].sum() > 0 else 0.0
        hold_moy = hold_min * 60 / hold_occ if hold_occ else 0
        T.render_kpi_row([
            {"label": "Post-travail (% du temps)", "value": round(post_pct, 1), "suffix": " %", "icon": "📝", "color": "#7B61FF"},
            {"label": "Post-travail — durée", "value": round(post_min), "suffix": " min", "icon": "⏲️", "color": "#7B61FF"},
            {"label": "Post-travail — moy. / contact (s)", "value": round(post_moy), "suffix": " s", "icon": "🔁", "color": "#7B61FF"},
            {"label": "Moyenne équipe post-travail", "value": round(team_post, 1), "suffix": " %", "icon": "👥", "color": T.NEUTRAL},
        ], height=125)
        T.render_kpi_row([
            {"label": "Taux de travail", "value": round(gp[A.GROUPE_TRAITEMENT] + gp[A.GROUPE_POST], 1), "suffix": " %", "icon": "💼", "color": "#0E7C86"},
            {"label": "Attente (réception d'appel)", "value": round(gp[A.GROUPE_ATTENTE], 1), "suffix": " %", "icon": "📞", "color": "#E08E1D"},
            {"label": "Pause", "value": round(gp[A.GROUPE_PAUSE], 1), "suffix": " %", "icon": "☕", "color": "#E5646A"},
        ], height=125)
        if A.hold_detecte(states):
            st.markdown("**⏸️ En attente — mise en attente pendant l'appel**")
            T.render_kpi_row([
                {"label": "En attente (% du temps)", "value": round(hold_pct, 1), "suffix": " %", "icon": "⏸️", "color": "#3B82C4"},
                {"label": "En attente — durée", "value": round(hold_min), "suffix": " min", "icon": "⏲️", "color": "#3B82C4"},
                {"label": "Nombre de mises en attente", "value": hold_occ, "icon": "🔢", "color": "#3B82C4"},
                {"label": "Durée moy. / mise en attente (s)", "value": round(hold_moy), "suffix": " s", "icon": "🔁", "color": "#3B82C4"},
            ], height=125)

        d1, d2 = st.columns(2)
        with d1:
            with st.container(border=True):
                niv_a = st.radio("Détail", ["États principaux", "Sous-états"], horizontal=True, key="niv_agent")
                tt = ag_states[ag_states["Niveau"] == (1 if niv_a.startswith("États") else 2)]
                tt = tt.groupby(["Categorie", "Groupe"], as_index=False).agg(Temps_min=("Temps_total_min", "sum"))
                tt = tt[tt["Temps_min"] > 0]
                if tt.empty:
                    st.info("Aucun sous-état disponible.")
                else:
                    st.plotly_chart(C.donut_etats(tt, "Son temps par état"), use_container_width=True)
        with d2:
            with st.container(border=True):
                st.plotly_chart(C.agent_vs_team(agent_pct, login, A.groupes_affichables(states, avec_hold_sous_etat=True)),
                                use_container_width=True)

        with st.expander("📋 Tableau détaillé de ses états", expanded=True):
            t = ag_states[["Categorie", "Parent", "Occurences", "Temps_total_min", "Pct"]].rename(columns={
                "Temps_total_min": "Temps (min)", "Pct": "% du temps"})
            st.dataframe(t, hide_index=True, use_container_width=True,
                         column_config={"% du temps": st.column_config.ProgressColumn(
                             "% du temps", min_value=0, max_value=100, format="%.1f %%")})

    st.markdown("---")
    st.markdown("### 📝 Synthèse d'entretien (copier / coller)")
    texte = A.synthese_recadrage(row, report_date, ag_states)
    st.text_area("Synthèse", texte, height=240, label_visibility="collapsed")
    st.download_button("📥 Télécharger la synthèse (.txt)", texte.encode("utf-8"),
                       f"synthese_{row['Login']}_{report_date.isoformat()}.txt", "text/plain")

# ==========================================================================
# RÉSUMÉ DU JOUR
# ==========================================================================
with tab_res:
    _r_df, _a_df = resume["retards"], resume["absents"]
    _n_post = int(suivi["Suivi_post"].sum()) if suivi is not None else 0
    _n_hold = int(suivi["Suivi_hold"].sum()) if suivi is not None else 0
    st.markdown(f"### 📝 Résumé du {report_date.strftime('%d/%m/%Y')}")
    T.render_kpi_row([
        {"label": "En retard", "value": len(_r_df), "icon": "⏱️", "color": T.WARNING, "alert": len(_r_df) > 0},
        {"label": "Absences non justifiées", "value": len(_a_df), "icon": "🚫", "color": T.DANGER,
         "alert": len(_a_df) > 0},
        {"label": "À suivre — post-appel", "value": _n_post, "icon": "📝", "color": "#7B61FF"},
        {"label": "À suivre — mise en attente", "value": _n_hold, "icon": "⏸️", "color": "#3B82C4"},
    ], height=115)

    _c1, _c2 = st.columns(2)
    with _c1:
        with st.container(border=True):
            st.markdown(f"#### ⏱️ En retard ({len(_r_df)})")
            if _r_df.empty:
                st.success("Aucun retard au-delà du seuil toléré.")
            else:
                st.dataframe(_r_df, hide_index=True, use_container_width=True,
                             height=min(38 * (len(_r_df) + 1) + 3, 380))
            st.caption(f"Retard compté au-delà de {prefs['seuil_retard']} min de tolérance.")
    with _c2:
        with st.container(border=True):
            st.markdown(f"#### 🚫 Absents ({len(_a_df)})")
            if _a_df.empty:
                st.success("Aucune absence non justifiée.")
            else:
                st.dataframe(_a_df, hide_index=True, use_container_width=True,
                             height=min(38 * (len(_a_df) + 1) + 3, 380))
            if resume["pas_commence"]:
                st.caption(f"{resume['pas_commence']} agent(s) dont le shift n'a pas encore commencé "
                           "(non comptés comme absents).")

    st.markdown("### 🎯 Qui mérite un suivi aujourd'hui ?")
    if suivi is None:
        st.info("Importez « Agents report » pour repérer les agents à suivre sur le post-appel "
                "et la mise en attente.")
    else:
        _m = suivi_meta
        if _m["mode_demande"] == "equipe" and _m["mode"] == "fixe":
            st.caption("Moins de 3 agents évaluables : les seuils fixes sont utilisés à la place du repérage "
                       "relatif à l'équipe.")
        if _m["agents_ecartes"]:
            st.caption(f"{_m['agents_ecartes']} agent(s) écarté(s) : temps observé inférieur à "
                       f"{prefs['min_total_min']} min.")
        _p1, _p2 = st.columns(2)
        with _p1:
            with st.container(border=True):
                _p = suivi[suivi["Suivi_post"]].sort_values("Post_pct", ascending=False)
                st.markdown(f"#### 📝 Post-appel — Post-travail ({len(_p)})")
                st.caption(f"Seuil : ≥ {_m['seuil_post']:.1f} % du temps · moyenne équipe {_m['moy_post']:.1f} %"
                           + (f" (moyenne + {_m['k']:g} écart-type)" if _m["mode"] == "equipe" else " (seuil fixe)"))
                if _p.empty:
                    st.success("Personne à suivre sur le post-appel.")
                else:
                    st.dataframe(
                        pd.DataFrame({"Nom": _p["Nom"].fillna("—").values, "Login": _p["Login"].values,
                                      "% du temps": _p["Post_pct"].round(1).values,
                                      "Durée moy. (s)": _p["Post_moy_s"].values}),
                        hide_index=True, use_container_width=True,
                        height=min(38 * (len(_p) + 1) + 3, 380),
                        column_config={"% du temps": st.column_config.NumberColumn(format="%.1f %%")})
        with _p2:
            with st.container(border=True):
                st.markdown(f"#### ⏸️ Mise en attente — En attente ({_n_hold})")
                if not _m["hold_dispo"]:
                    st.info("Aucun état « En attente » (mise en attente) trouvé dans l'export — voir "
                            "« Correspondance des états » dans l'onglet Dashboard.")
                else:
                    _h = suivi[suivi["Suivi_hold"]].sort_values("Hold_pct", ascending=False)
                    st.caption(f"Seuil : ≥ {_m['seuil_hold']:.1f} % du temps · moyenne équipe {_m['moy_hold']:.1f} %"
                               + (f" (moyenne + {_m['k']:g} écart-type)" if _m["mode"] == "equipe"
                                  else " (seuil fixe)"))
                    if _h.empty:
                        st.success("Personne à suivre sur la mise en attente.")
                    else:
                        st.dataframe(
                            pd.DataFrame({"Nom": _h["Nom"].fillna("—").values, "Login": _h["Login"].values,
                                          "% du temps": _h["Hold_pct"].round(1).values,
                                          "Nb": _h["Hold_nb"].astype(int).values,
                                          "Durée moy. (s)": _h["Hold_moy_s"].values}),
                            hide_index=True, use_container_width=True,
                            height=min(38 * (len(_h) + 1) + 3, 380),
                            column_config={"% du temps": st.column_config.NumberColumn(format="%.1f %%")})
        st.caption("Critères modifiables dans la barre latérale (« 🎯 Critères de suivi ») et sauvegardables "
                   "dans l'onglet « 💾 Sauvegarde ».")

    with st.expander("📋 Message à copier (WhatsApp / mail)"):
        st.code(A.resume_texte(report_date, resume, suivi, suivi_meta), language=None)

# ==========================================================================
# SAUVEGARDE
# ==========================================================================
with tab_save:
    st.markdown("### 💾 Sauvegarde & préférences")
    st.caption("Exportez la journée (données + préférences + résumé) en JSON, ou rouvrez-en une plus tard.")
    render_sauvegarde({
        "report_date": report_date, "planning_df": planning_df, "activities_df": activities_df,
        "agents_df": agents_df, "is_demo": st.session_state.is_demo,
        "resume": A.resume_json(report_date, resume, suivi, suivi_meta),
    })
