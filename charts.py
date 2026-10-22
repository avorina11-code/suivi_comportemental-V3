# -*- coding: utf-8 -*-
"""charts.py — constructeurs de graphiques Plotly (aucune logique Streamlit)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import analysis as A
import theme as T

COULEURS_GROUPES = {
    A.GROUPE_TRAITEMENT: "#0E7C86",
    A.GROUPE_POST: "#7B61FF",
    A.GROUPE_ATTENTE: "#E08E1D",
    A.GROUPE_PAUSE: "#E5646A",
    A.GROUPE_AUTRE: "#8A97AC",
}
COULEURS_STATUT = {
    A.STATUT_OK: "#1F9D6B",
    A.STATUT_ECART: "#D6483D",
    A.STATUT_ABSENT: "#8B2A21",
    A.STATUT_PAS_COMMENCE: "#0E5D68",
    A.STATUT_NON_PLANIF: "#8A97AC",
}


def _base(fig, height=360, title=None):
    fig.update_layout(template=T.PLOTLY_TEMPLATE_NAME, height=height,
                      title=title if title else fig.layout.title.text)
    return fig


def donut_etats(totals: pd.DataFrame, title="Répartition du temps — équipe") -> go.Figure:
    colors = [COULEURS_GROUPES.get(g, "#8A97AC") for g in totals["Groupe"]]
    fig = go.Figure(go.Pie(
        labels=totals["Categorie"], values=totals["Temps_min"], hole=0.62,
        marker=dict(colors=colors, line=dict(color="#fff", width=2)),
        textinfo="percent", sort=False,
        hovertemplate="<b>%{label}</b><br>%{value:.0f} min · %{percent}<extra></extra>",
    ))
    fig.update_layout(legend=dict(orientation="h", y=-0.1), showlegend=True)
    return _base(fig, 360, title)


def sunburst_etats(states: pd.DataFrame) -> go.Figure:
    df = states.copy()
    df["Parent_"] = df["Parent"].where(df["Niveau"] == 2, "")
    d = df.groupby(["Categorie", "Parent_", "Niveau", "Groupe"], as_index=False)["Temps_total_min"].sum()
    d = d[d["Temps_total_min"] > 0]
    ids = [f"{p}/{c}" if p else c for c, p in zip(d["Categorie"], d["Parent_"])]
    parents = list(d["Parent_"])
    fig = go.Figure(go.Sunburst(
        ids=ids, labels=d["Categorie"], parents=parents, values=d["Temps_total_min"],
        branchvalues="remainder",
        marker=dict(colors=[COULEURS_GROUPES.get(g, "#8A97AC") for g in d["Groupe"]]),
        hovertemplate="<b>%{label}</b><br>%{value:.0f} min<extra></extra>",
    ))
    return _base(fig, 420, "Détail des états (cliquez pour zoomer)")


def gauge(value: float, title: str, color: str, suffix="%") -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=round(value, 1),
        number=dict(suffix=suffix, font=dict(family="Space Grotesk", size=34)),
        title=dict(text=title, font=dict(size=14)),
        gauge=dict(axis=dict(range=[0, 100]), bar=dict(color=color, thickness=0.28),
                   bgcolor="#EEF1F6", borderwidth=0),
    ))
    fig.update_layout(template=T.PLOTLY_TEMPLATE_NAME, height=210, margin=dict(t=50, b=0, l=20, r=20))
    return fig


def stacked_agents(agent_pct: pd.DataFrame, sort_by: str, groupes: list[str]) -> go.Figure:
    d = agent_pct.sort_values(f"{sort_by}_pct", ascending=True)
    fig = go.Figure()
    for g in groupes:
        fig.add_bar(y=d["Nom"].fillna(d["Login"]), x=d[f"{g}_pct"], name=g, orientation="h",
                    marker_color=COULEURS_GROUPES[g],
                    customdata=d[f"{g}_min"],
                    hovertemplate="<b>%{y}</b><br>" + g + " : %{x:.1f} % (%{customdata:.0f} min)<extra></extra>")
    fig.update_layout(barmode="stack", legend=dict(orientation="h", y=1.04),
                      xaxis=dict(title="% du temps", range=[0, 100]))
    return _base(fig, max(380, 22 * len(d) + 120), "Répartition du temps par agent")


def ranking_etat(states: pd.DataFrame, categorie: str, top: int, niveau: int) -> go.Figure:
    d = states[(states["Categorie"] == categorie) & (states["Niveau"] == niveau)]
    d = d.groupby(["Login", "Nom"], as_index=False).agg(Pct=("Pct", "sum"), Min=("Temps_total_min", "sum"),
                                                        Occ=("Occurences", "sum"))
    d = d.sort_values("Pct", ascending=False).head(top).sort_values("Pct")
    g = states.loc[states["Categorie"] == categorie, "Groupe"].iloc[0]
    fig = go.Figure(go.Bar(
        y=d["Nom"].fillna(d["Login"]), x=d["Pct"], orientation="h",
        marker_color=COULEURS_GROUPES.get(g, "#0E7C86"),
        customdata=d[["Min", "Occ"]],
        text=[f"{v:.1f} %" for v in d["Pct"]], textposition="outside",
        hovertemplate="<b>%{y}</b><br>%{x:.1f} % du temps<br>%{customdata[0]:.0f} min · %{customdata[1]} occurrence(s)<extra></extra>"))
    fig.update_layout(xaxis_title="% du temps de l'agent")
    return _base(fig, max(300, 26 * len(d) + 100), f"Top {len(d)} — {categorie}")


def statuts_donut(matrice: pd.DataFrame) -> go.Figure:
    c = matrice["Statut"].value_counts()
    fig = go.Figure(go.Pie(
        labels=c.index, values=c.values, hole=0.62, sort=False,
        marker=dict(colors=[COULEURS_STATUT.get(k, "#8A97AC") for k in c.index],
                    line=dict(color="#fff", width=2)),
        textinfo="value",
        hovertemplate="<b>%{label}</b><br>%{value} agent(s) · %{percent}<extra></extra>"))
    fig.update_layout(legend=dict(orientation="h", y=-0.1))
    return _base(fig, 360, "Statuts du jour")


def arrivees_chart(matrice: pd.DataFrame, seuil: int) -> go.Figure:
    d = matrice.dropna(subset=["Ecart_arrivee_min"]).sort_values("Ecart_arrivee_min")
    col = ["#D6483D" if v > seuil else "#1F9D6B" for v in d["Ecart_arrivee_min"]]
    fig = go.Figure(go.Bar(
        x=d["Nom"].fillna(d["Login"]), y=d["Ecart_arrivee_min"], marker_color=col,
        hovertemplate="<b>%{x}</b><br>%{y:+.0f} min vs prévu<extra></extra>"))
    fig.add_hline(y=seuil, line_dash="dash", line_color="#D97F13",
                  annotation_text=f"Seuil {seuil} min", annotation_position="top left")
    fig.update_layout(yaxis_title="Écart à l'arrivée (min) — négatif = en avance", xaxis_tickangle=-60)
    return _base(fig, 380, "Ponctualité à la prise de poste")


def _dt(t: dt.time):
    return dt.datetime.combine(dt.date(2000, 1, 1), t) if isinstance(t, dt.time) else None


def gantt_agent(row: pd.Series) -> go.Figure | None:
    """Planifié vs réel pour un agent (barres horizontales)."""
    rows = []
    for lib, a, b in (("Prévu", row.get("Debut_prevu"), row.get("Fin_prevu")),
                      ("Réel", row.get("Arrivee_reelle"), row.get("Depart_reel"))):
        if isinstance(a, dt.time) and isinstance(b, dt.time):
            rows.append({"Type": lib, "Début": _dt(a), "Fin": _dt(b)})
    if not rows:
        return None
    fig = px.timeline(pd.DataFrame(rows), x_start="Début", x_end="Fin", y="Type", color="Type",
                      color_discrete_map={"Prévu": "#8A97AC", "Réel": "#0E7C86"})
    fig.update_xaxes(tickformat="%H:%M")
    fig.update_layout(showlegend=False, yaxis_title=None)
    return _base(fig, 200, "Planning vs réel")


def agent_vs_team(agent_pct: pd.DataFrame, login: str) -> go.Figure:
    a = agent_pct[agent_pct["Login"] == login].iloc[0]
    gs = [g for g in A.GROUPES if g != A.GROUPE_AUTRE or a[f"{g}_pct"] > 0]
    moy = [agent_pct[f"{g}_pct"].mean() for g in gs]
    fig = go.Figure()
    fig.add_bar(x=gs, y=[a[f"{g}_pct"] for g in gs], name="Agent",
                marker_color=[COULEURS_GROUPES[g] for g in gs],
                text=[f"{a[f'{g}_pct']:.1f} %" for g in gs], textposition="outside")
    fig.add_bar(x=gs, y=moy, name="Moyenne équipe", marker_color="#C9D1DE",
                text=[f"{v:.1f} %" for v in moy], textposition="outside")
    fig.update_layout(barmode="group", yaxis_title="% du temps", legend=dict(orientation="h", y=1.1))
    return _base(fig, 340, "Agent vs moyenne équipe")
