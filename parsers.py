# -*- coding: utf-8 -*-
"""
parsers.py
==========
Module de parsing pour l'application d'analyse d'adhérence planning CSA.

Ce module sait lire :
    1. Le Planning Hebdo (théorique)                -> load_planning()
    2. Deux exports Vocalcom (réel) :

       - "Agents activities by dates"  -> parse_activities_by_date()
         Source UNIQUE des heures d'arrivée / départ réelles et de la durée
         de travail (nom de l'agent tronqué : jointure par Login uniquement).

       - "Agents report"               -> parse_agents_report()
         Détail des états par agent : Attente, Traitement, Post-travail,
         Pause... avec leurs sous-catégories (ex. un motif de pause).

    L'ancien export "Agents pause report" n'est PLUS utilisé : s'il est
    déposé, il est reconnu puis ignoré (type "pause_report").

IMPORTANT : le nom de fichier ne correspond pas toujours au contenu réel ;
le type est donc détecté en scannant les premières lignes (detect_report_type).
Les .xls (binaire OLE) sont lus avec xlrd, les .xlsx avec openpyxl.
"""

from __future__ import annotations

import io
import re
import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# Codes de statut "jour non travaillé" reconnus dans le Planning Hebdo
CODES_NON_TRAVAILLE = {"OFF", "CONGE", "CONGÉ", "PERMISSION", "FORMATION", "CM", "MALADIE"}



# --------------------------------------------------------------------------
# Utilitaires génériques
# --------------------------------------------------------------------------

def clean_login(value) -> Optional[str]:
    """Nettoie un identifiant Login_Vocalcom : enlève espaces normaux et
    insécables (\\xa0, utilisés par Crystal Reports comme séparateur de
    milliers, ex: '1\\xa0055' -> '1055'), enlève un éventuel '.0' final
    laissé par une lecture Excel en float, et force en chaîne de caractères.
    """
    if value is None:
        return None
    s = str(value).strip()
    s = s.replace("\xa0", "").replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    return digits if digits else None


def excel_fraction_to_time(value) -> Optional[dt.time]:
    """Convertit une fraction de jour Excel (ex: 0.291666..) en objet time.
    Les plannings Excel stockent souvent les heures comme des fractions de
    24h plutôt que comme des cellules 'Heure' formatées.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0 or f >= 1.5:  # >1 jour improbable pour une heure de shift
        return None
    total_seconds = round(f * 24 * 3600)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    h = h % 24
    try:
        return dt.time(hour=h, minute=m, second=s)
    except ValueError:
        return None


def parse_hhmm_string(s: str) -> Optional[dt.time]:
    """Parse une heure au format '06h51', '06:51' ou '6h51' -> time(6,51)."""
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d{1,2})[h:](\d{2})$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return dt.time(hour=h, minute=mi)


def parse_duration_to_timedelta(s: str) -> Optional[dt.timedelta]:
    """Parse une durée Vocalcom vers un timedelta. Deux formats rencontrés :
       - "Xh MM'SS"  ex: "5h40'24"   -> 5h 40m 24s
       - "MM'SS"     ex: "03'47"     -> 0h 3m 47s (utilisé pour les cellules
         de tranche de 30 min)
    """
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d+)h(\d{1,2})'(\d{1,2})$", s)
    if m:
        h, mi, se = (int(x) for x in m.groups())
        return dt.timedelta(hours=h, minutes=mi, seconds=se)
    m = re.match(r"^(\d{1,2})'(\d{1,2})$", s)
    if m:
        mi, se = (int(x) for x in m.groups())
        return dt.timedelta(minutes=mi, seconds=se)
    return None


def td_to_minutes(td: Optional[dt.timedelta]) -> float:
    if td is None:
        return 0.0
    return round(td.total_seconds() / 60.0, 2)


def date_to_jour_fr(d: dt.date) -> str:
    return JOURS_FR[d.weekday()]


# --------------------------------------------------------------------------
# Chargement bas niveau : grille normalisée (compatible .xls et .xlsx)
# --------------------------------------------------------------------------

def _read_bytes(file_like) -> bytes:
    if hasattr(file_like, "read"):
        data = file_like.read()
        if hasattr(file_like, "seek"):
            file_like.seek(0)
        return data
    if isinstance(file_like, str):
        with open(file_like, "rb") as fh:
            return fh.read()
    return file_like


def load_sheet_grid(file_like, filename: str = "") -> list[list]:
    """Charge la première feuille d'un classeur Excel (.xls ou .xlsx) et la
    retourne sous forme de grille normalisée : liste de lignes, chaque
    ligne étant une liste de valeurs (index 0 = colonne A, 1 = colonne B,
    etc.). Les cellules vides valent None. Toutes les lignes sont
    complétées (padding) à la même largeur pour simplifier les accès par
    index de colonne.

    - .xlsx / .xlsm -> openpyxl
    - .xls (binaire Excel 97-2003 / OLE, format très courant des exports
      Vocalcom/Crystal Reports) -> xlrd
    """
    data = _read_bytes(file_like)
    name_lower = (filename or getattr(file_like, "name", "") or "").lower()

    def _try_openpyxl():
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [[c.value for c in row] for row in ws.iter_rows()]
        return grid

    def _try_xlrd():
        import xlrd
        wb = xlrd.open_workbook(file_contents=data)
        ws = wb.sheet_by_index(0)
        grid = [ws.row_values(r) for r in range(ws.nrows)]
        # xlrd renvoie '' pour les cellules vides -> on normalise en None
        for row in grid:
            for i, v in enumerate(row):
                if v == "":
                    row[i] = None
        return grid

    errors = []
    order = [_try_xlrd, _try_openpyxl] if name_lower.endswith(".xls") else [
        _try_openpyxl,
        _try_xlrd,
    ]
    for fn in order:
        try:
            grid = fn()
            width = max((len(r) for r in grid), default=0)
            return [r + [None] * (width - len(r)) for r in grid]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")

    raise ValueError(
        "Impossible de lire ce fichier Excel (.xls/.xlsx). Formats testés "
        "sans succès (openpyxl et xlrd). Détails : " + " | ".join(errors)
    )


def _iter_cells_first_rows(grid, max_row=100):
    for row in grid[:max_row]:
        for v in row:
            if v is not None and str(v).strip() != "":
                yield v


# --------------------------------------------------------------------------
# 1. PLANNING HEBDO (théorique)
# --------------------------------------------------------------------------

def load_planning(file_like) -> pd.DataFrame:
    """Charge le fichier Planning Hebdo (.xlsx) -> DataFrame brut nettoyé.

    Colonnes en sortie : Matricule_RH, Login, Nom, puis pour chaque jour
    de JOURS_FR : '<Jour>_debut' et '<Jour>_fin', contenant soit un
    datetime.time, soit un code texte (OFF/CONGE/...), soit None.
    """
    df = pd.read_excel(file_like, sheet_name="Planning_Hebdo", engine="openpyxl")

    # Localisation souple des colonnes clés (au cas où l'intitulé varie légèrement)
    col_map = {}
    for col in df.columns:
        cl = str(col).strip().lower()
        if cl in ("login_vocalcom", "login", "id vocalcom"):
            col_map[col] = "Login"
        elif cl in ("matricule_rh", "matricule"):
            col_map[col] = "Matricule_RH"
        elif cl in ("nom_prenom", "nom", "nom_prénom"):
            col_map[col] = "Nom"
    df = df.rename(columns=col_map)

    if "Login" not in df.columns:
        raise ValueError(
            "Colonne Login_Vocalcom introuvable dans le Planning Hebdo. "
            "Vérifiez l'intitulé de la colonne."
        )

    df["Login"] = df["Login"].apply(clean_login)
    df = df.dropna(subset=["Login"]).reset_index(drop=True)

    def _convert(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        if isinstance(v, (int, float)):
            return excel_fraction_to_time(v)
        if isinstance(v, dt.time):
            return v
        s = str(v).strip()
        if s.upper() in CODES_NON_TRAVAILLE:
            return s.upper()
        if s == "":
            return None
        # Shift éclaté "09:00/16:00" -> on garde la chaîne brute, elle
        # sera interprétée par get_shift_for_day().
        return s

    for jour in JOURS_FR:
        col_debut = f"{jour} Heure de début"
        col_fin = f"{jour} Heure de fin"
        df[f"{jour}_debut"] = df[col_debut].apply(_convert) if col_debut in df.columns else None
        df[f"{jour}_fin"] = df[col_fin].apply(_convert) if col_fin in df.columns else None

    keep_cols = ["Matricule_RH", "Login", "Nom"] + [
        f"{j}_{suf}" for j in JOURS_FR for suf in ("debut", "fin")
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    return df[keep_cols].copy()


def _resolve_shift_bound(value, prendre_min: bool):
    """Résout une valeur de début/fin de shift (time, code statut, ou chaîne
    à tranches multiples séparées par '/') vers un objet time unique.
    Pour un shift éclaté, on prend la borne globale (début le plus tôt /
    fin la plus tardive) — voir limitation documentée dans le README.
    """
    if value is None:
        return None
    if isinstance(value, str) and value.upper() in CODES_NON_TRAVAILLE:
        return None
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str) and "/" in value:
        parts = [parse_hhmm_string(p) or excel_fraction_to_time(p) for p in value.split("/")]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        return min(parts) if prendre_min else max(parts)
    if isinstance(value, str):
        return parse_hhmm_string(value)
    return None


def get_shift_for_day(planning_df: pd.DataFrame, jour: str) -> pd.DataFrame:
    """Extrait, pour un jour de la semaine donné, le planning théorique de
    chaque agent -> DataFrame(Login, Matricule_RH, Nom, Debut_prevu,
    Fin_prevu, Statut_planning).

    Statut_planning ∈ {"TRAVAIL", "OFF", "CONGE", "PERMISSION",
    "FORMATION", "CM", "MALADIE", "NON_PLANIFIE"}.
    """
    col_debut = f"{jour}_debut"
    col_fin = f"{jour}_fin"
    rows = []
    for _, r in planning_df.iterrows():
        raw_debut = r.get(col_debut)
        raw_fin = r.get(col_fin)

        statut = "TRAVAIL"
        if raw_debut is None and raw_fin is None:
            statut = "NON_PLANIFIE"
        elif isinstance(raw_debut, str) and raw_debut.upper() in CODES_NON_TRAVAILLE:
            code = raw_debut.upper()
            statut = "CONGE" if code in ("CONGE", "CONGÉ") else code

        debut = _resolve_shift_bound(raw_debut, prendre_min=True) if statut == "TRAVAIL" else None
        fin = _resolve_shift_bound(raw_fin, prendre_min=False) if statut == "TRAVAIL" else None
        if statut == "TRAVAIL" and debut is None and fin is None:
            statut = "NON_PLANIFIE"

        rows.append(
            {
                "Login": r.get("Login"),
                "Matricule_RH": r.get("Matricule_RH"),
                "Nom": r.get("Nom"),
                "Debut_prevu": debut,
                "Fin_prevu": fin,
                "Statut_planning": statut,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Détection automatique du type de rapport Vocalcom
# --------------------------------------------------------------------------

REPORT_TITLES = {
    "Agents pause report": "pause_report",
    "Agents report": "agents_report",
    "Agents activities by dates": "activities_by_date",
}


def detect_report_type(grid) -> str:
    """Scanne les 15 premières lignes pour déterminer le type réel du
    rapport, indépendamment du nom du fichier fourni par l'utilisateur
    (voir note en tête de module)."""
    for val in _iter_cells_first_rows(grid, max_row=15):
        s = str(val).strip()
        for title, key in REPORT_TITLES.items():
            if s == title:
                return key
    for val in _iter_cells_first_rows(grid, max_row=15):
        s = str(val).strip().lower()
        for title, key in REPORT_TITLES.items():
            if title.lower() in s:
                return key
    return "unknown"


def detect_report_date(grid) -> Optional[dt.date]:
    """Cherche un motif DD/MM/YYYY dans les 100 premières lignes."""
    pattern = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
    for val in _iter_cells_first_rows(grid, max_row=100):
        m = pattern.search(str(val))
        if m:
            d, mo, y = (int(x) for x in m.groups())
            try:
                return dt.date(y, mo, d)
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# 2B. AGENTS REPORT (optionnel) -- détail Attente/Traitement/Pause/...
# --------------------------------------------------------------------------

AGENT_REPORT_HEADER_RE = re.compile(r"^Agent\s+(\d[\d\s\xa0]*)\s*:\s*(.+)$")

# Index de colonnes (0 = A) pour ce type de rapport
AR_COL_LABEL = 1     # B
AR_COL_OCC = 4        # E
AR_COL_TOTAL = 6      # G
AR_COL_PCT = 14       # O
AR_COL_SUBPCT = 16    # Q (présent uniquement sur les sous-catégories)


def parse_agents_report(grid) -> pd.DataFrame:
    """Parse un export Vocalcom 'Agents report' (détail des états par agent).

    -> DataFrame(Login, Nom, Temps_presence_min, Categorie, Parent, Niveau,
                 Occurences, Temps_total_min, Pourcent_temps)

    Niveau 1 = catégorie principale (Attente, Traitement, Post-travail,
    Pause...). Niveau 2 = sous-catégorie (repérée par un pourcentage en
    colonne Q), rattachée à sa catégorie principale via 'Parent' : c'est
    là que se trouvent par ex. les motifs de pause.
    """
    rows = []
    current_login = current_nom = None
    current_presence = None
    current_parent = None
    presence_re = re.compile(r"(\d+h\d{1,2}'\d{1,2})")

    for r in grid:
        if all(v is None for v in r):
            continue

        a_val = r[0] if len(r) > 0 else None
        if a_val is not None:
            m = AGENT_REPORT_HEADER_RE.match(str(a_val).strip())
            if m:
                current_login = clean_login(m.group(1))
                current_nom = m.group(2).strip()
                presence_str = str(r[6]) if len(r) > 6 and r[6] is not None else ""
                pm = presence_re.search(presence_str)
                current_presence = td_to_minutes(
                    parse_duration_to_timedelta(pm.group(1)) if pm else None
                )
                current_parent = None
                continue

        label = r[AR_COL_LABEL] if len(r) > AR_COL_LABEL else None
        occ = r[AR_COL_OCC] if len(r) > AR_COL_OCC else None
        temps_total = r[AR_COL_TOTAL] if len(r) > AR_COL_TOTAL else None
        pourcent = r[AR_COL_PCT] if len(r) > AR_COL_PCT else None
        sub_pct = r[AR_COL_SUBPCT] if len(r) > AR_COL_SUBPCT else None

        if (
            current_login is not None
            and isinstance(label, str)
            and isinstance(occ, (int, float))
        ):
            niveau = 2 if sub_pct is not None else 1
            if niveau == 1:
                current_parent = label.strip()
            rows.append(
                {
                    "Login": current_login,
                    "Nom": current_nom,
                    "Temps_presence_min": current_presence,
                    "Categorie": label.strip(),
                    "Parent": current_parent if niveau == 2 else None,
                    "Niveau": niveau,
                    "Occurences": int(occ),
                    "Temps_total_min": td_to_minutes(
                        parse_duration_to_timedelta(str(temps_total))
                    ),
                    "Pourcent_temps": pourcent,
                }
            )

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 2C. AGENTS ACTIVITIES BY DATES (optionnel) -- résumé simplifié
# --------------------------------------------------------------------------

ACTIVITIES_HEADER_RE = re.compile(r"^(\d[\d\s\xa0]*)\s*:\s*(.+)$")


def parse_activities_by_date(grid) -> pd.DataFrame:
    """Parse la grille d'un export Vocalcom de type 'Agents activities by
    dates'.

    ATTENTION : dans cet export, les graphiques à barres empilées
    d'origine (Attente/Supervision/Traitement/Post travail/Pause) ne sont
    PAS récupérables comme données une fois converti en fichier
    tabulaire — seules les informations Arrivée-Départ et Durée de
    travail totale restent exploitables. De plus le nom de l'agent y est
    tronqué (largeur de colonne fixe côté export) : on ne se fie donc
    qu'au Login pour la jointure, jamais au nom.

    -> DataFrame(Login, Nom_tronque, Arrivee, Depart, Duree_travail_min)
    """
    rows = []
    pending_plage = None  # 'HHhMM-HHhMM' vu juste avant la ligne agent
    plage_re = re.compile(r"^\d{2}h\d{2}-\d{2}h\d{2}$")

    for idx, r in enumerate(grid):
        if all(v is None for v in r):
            continue

        for v in r:
            if isinstance(v, str) and plage_re.match(v.strip()):
                pending_plage = v.strip()

        b_val = r[1] if len(r) > 1 else None
        if b_val is not None:
            m = ACTIVITIES_HEADER_RE.match(str(b_val).strip())
            if m:
                login = clean_login(m.group(1))
                nom_tronque = m.group(2).strip()
                duree = None
                if idx + 1 < len(grid):
                    for v in grid[idx + 1]:
                        if v is None:
                            continue
                        td = parse_duration_to_timedelta(str(v))
                        if td is not None:
                            duree = td
                            break
                arrivee = depart = None
                if pending_plage:
                    a, d = pending_plage.split("-")
                    arrivee = parse_hhmm_string(a)
                    depart = parse_hhmm_string(d)
                rows.append(
                    {
                        "Login": login,
                        "Nom_tronque": nom_tronque,
                        "Arrivee": arrivee,
                        "Depart": depart,
                        "Duree_travail_min": td_to_minutes(duree),
                    }
                )
                pending_plage = None

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Point d'entrée unique
# --------------------------------------------------------------------------

def load_any_vocalcom_export(file_like, filename: str = ""):
    """Détecte automatiquement le type d'export Vocalcom puis appelle le
    bon parseur. Retourne (type_detecte, resultat, date_detectee).

    type_detecte ∈ {"agents_report", "activities_by_date", "pause_report"
    (ignoré), "unknown"}.
    """
    grid = load_sheet_grid(file_like, filename=filename)
    rtype = detect_report_type(grid)
    d = detect_report_date(grid)

    if rtype == "agents_report":
        return rtype, parse_agents_report(grid), d
    if rtype == "activities_by_date":
        return rtype, parse_activities_by_date(grid), d
    return rtype, None, d
