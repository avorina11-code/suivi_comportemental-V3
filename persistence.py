# -*- coding: utf-8 -*-
"""
persistence.py — Sauvegarde / import JSON d'une journée et des préférences.

Aucune dépendance à Streamlit : tout est testable seul.

Format (version 1) :
{
  "format": "suivi_comportemental", "version": 1,
  "type": "journee" | "preferences",
  "exporte_le": "2026-11-20T11:05:00", "date": "2026-11-19", "demo": false,
  "preferences": {...},
  "donnees": {"planning": {"columns": [...], "rows": [[...]]},
              "activites": {...} | null, "agents": {...} | null},
  "resume": {...}          # informatif seulement, recalculé à l'import
}
Les heures sont stockées sous forme {"$t": "07:00:00"} pour être restituées
exactement (les DataFrames du projet contiennent des datetime.time).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

FORMAT = "suivi_comportemental"
VERSION = 1

# --------------------------------------------------------------------------
# Préférences
# --------------------------------------------------------------------------

MODES_SUIVI = {
    "equipe": "Relatif à l'équipe (moyenne + N écarts-types)",
    "fixe": "Seuils fixes (% du temps)",
}

PREFS_DEFAUTS: dict = {
    "seuil_retard": 5,            # min tolérées avant de compter un retard
    "export_temps_reel": False,
    "heure_extraction": "13:00",
    "mode_suivi": "equipe",
    "k_suivi": 1.0,               # N écarts-types au-dessus de la moyenne équipe
    "seuil_post": 15.0,           # % Post-travail (mode fixe)
    "seuil_hold": 8.0,            # % mise en attente (mode fixe)
    "min_total_min": 30,          # temps observé minimal pour évaluer un agent
}

PREF_PREFIX = "pref_"


def _parse_hhmm(v) -> Optional[dt.time]:
    if isinstance(v, dt.time):
        return v.replace(microsecond=0, tzinfo=None)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", v)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h < 24 and 0 <= mi < 60:
                return dt.time(h, mi)
    return None


def _num(v, lo, hi, cast):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return cast(min(max(v, lo), hi))


def sanitize_prefs(raw: Any) -> dict:
    """Ne garde que les préférences connues, valides et bornées."""
    out: dict = {}
    if not isinstance(raw, Mapping):
        return out
    for key, (lo, hi, cast) in {
        "seuil_retard": (0, 60, int),
        "k_suivi": (0.0, 5.0, float),
        "seuil_post": (0.0, 100.0, float),
        "seuil_hold": (0.0, 100.0, float),
        "min_total_min": (0, 600, int),
    }.items():
        if key in raw:
            val = _num(raw[key], lo, hi, cast)
            if val is not None:
                out[key] = val
    if isinstance(raw.get("export_temps_reel"), bool):
        out["export_temps_reel"] = raw["export_temps_reel"]
    t = _parse_hhmm(raw.get("heure_extraction"))
    if t is not None:
        out["heure_extraction"] = t.strftime("%H:%M")
    if raw.get("mode_suivi") in MODES_SUIVI:
        out["mode_suivi"] = raw["mode_suivi"]
    return out


def prefs_to_state(prefs: Mapping) -> dict:
    """Préférences -> valeurs prêtes pour st.session_state (clés « pref_… »)."""
    merged = {**PREFS_DEFAUTS, **sanitize_prefs(prefs)}
    state = {PREF_PREFIX + k: v for k, v in merged.items()}
    state[PREF_PREFIX + "heure_extraction"] = _parse_hhmm(merged["heure_extraction"]) or dt.time(13, 0)
    return state


def state_to_prefs(state: Mapping) -> dict:
    """st.session_state -> dictionnaire de préférences sérialisable."""
    raw = {k: state.get(PREF_PREFIX + k, v) for k, v in PREFS_DEFAUTS.items()}
    t = raw.get("heure_extraction")
    if isinstance(t, dt.time):
        raw["heure_extraction"] = t.strftime("%H:%M")
    return {**PREFS_DEFAUTS, **sanitize_prefs(raw)}


# --------------------------------------------------------------------------
# DataFrame <-> JSON
# --------------------------------------------------------------------------

def _isna(v) -> bool:
    try:
        r = pd.isna(v)
        return bool(r) if not hasattr(r, "__len__") else False
    except (TypeError, ValueError):
        return False


def _enc(v):
    if v is None or _isna(v):
        return None
    if isinstance(v, dt.datetime):            # avant dt.date (sous-classe)
        return {"$dt": v.isoformat()}
    if isinstance(v, dt.date):
        return {"$d": v.isoformat()}
    if isinstance(v, dt.time):
        return {"$t": v.isoformat()}
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):   # types numpy
        try:
            return _enc(v.item())
        except (ValueError, TypeError):
            pass
    if isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    return str(v)


def _dec(v):
    if isinstance(v, dict) and len(v) == 1:
        (k, val), = v.items()
        try:
            if k == "$t":
                return dt.time.fromisoformat(val)
            if k == "$d":
                return dt.date.fromisoformat(val)
            if k == "$dt":
                return dt.datetime.fromisoformat(val)
        except (TypeError, ValueError):
            return None
    return v


def df_to_json_obj(df: Optional[pd.DataFrame]) -> Optional[dict]:
    if df is None:
        return None
    return {
        "columns": [str(c) for c in df.columns],
        "rows": [[_enc(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


def json_obj_to_df(obj: Any) -> Optional[pd.DataFrame]:
    if obj is None:
        return None
    if not (isinstance(obj, dict) and isinstance(obj.get("columns"), list)
            and isinstance(obj.get("rows"), list)):
        raise ValueError("Tableau de données invalide dans la sauvegarde.")
    cols = obj["columns"]
    rows = []
    for r in obj["rows"]:
        if not isinstance(r, list) or len(r) != len(cols):
            raise ValueError("Ligne de données incohérente dans la sauvegarde.")
        rows.append([_dec(v) for v in r])
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------
# Construction / lecture d'une sauvegarde
# --------------------------------------------------------------------------

def build_journee(report_date: Optional[dt.date], planning_df, activities_df, agents_df,
                  prefs: Mapping, resume: Optional[dict] = None, is_demo: bool = False) -> dict:
    return {
        "format": FORMAT, "version": VERSION, "type": "journee",
        "exporte_le": dt.datetime.now().replace(microsecond=0).isoformat(),
        "date": report_date.isoformat() if report_date else None,
        "demo": bool(is_demo),
        "preferences": sanitize_prefs(prefs),
        "donnees": {
            "planning": df_to_json_obj(planning_df),
            "activites": df_to_json_obj(activities_df),
            "agents": df_to_json_obj(agents_df),
        },
        "resume": resume,
    }


def build_preferences(prefs: Mapping) -> dict:
    return {
        "format": FORMAT, "version": VERSION, "type": "preferences",
        "exporte_le": dt.datetime.now().replace(microsecond=0).isoformat(),
        "preferences": sanitize_prefs(prefs),
    }


def dumps(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_payload(raw: "bytes | str") -> dict:
    """Valide et décode une sauvegarde. Lève ValueError (message en français)."""
    try:
        data = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Ce fichier n'est pas un JSON valide.") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Ce JSON n'est pas une sauvegarde de cette application.")
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise ValueError("Version de sauvegarde non prise en charge (fichier plus récent ?).")
    kind = data.get("type")
    if kind not in ("journee", "preferences"):
        raise ValueError("Type de sauvegarde inconnu.")

    out = {"type": kind, "exporte_le": data.get("exporte_le"),
           "preferences": sanitize_prefs(data.get("preferences")),
           "date": None, "demo": False,
           "planning": None, "activites": None, "agents": None}
    if kind == "journee":
        d = data.get("date")
        if isinstance(d, str):
            try:
                out["date"] = dt.date.fromisoformat(d)
            except ValueError:
                raise ValueError("Date invalide dans la sauvegarde.") from None
        out["demo"] = bool(data.get("demo"))
        donnees = data.get("donnees")
        if not isinstance(donnees, dict):
            raise ValueError("La sauvegarde ne contient pas de données.")
        out["planning"] = json_obj_to_df(donnees.get("planning"))
        out["activites"] = json_obj_to_df(donnees.get("activites"))
        out["agents"] = json_obj_to_df(donnees.get("agents"))
        if out["planning"] is None or (out["activites"] is None and out["agents"] is None):
            raise ValueError("Sauvegarde incomplète : planning et au moins un export Vocalcom sont requis.")
        for name, cols in (("planning", {"Login"}), ("activites", {"Login", "Arrivee", "Depart"}),
                           ("agents", {"Login", "Categorie", "Niveau", "Temps_total_min", "Occurences"})):
            df = out[name]
            if df is not None and not cols.issubset(df.columns):
                raise ValueError(f"Colonnes manquantes dans « {name} » : {sorted(cols - set(df.columns))}.")
    return out


# --------------------------------------------------------------------------
# Stockage local (dossier « sauvegardes/ »)
# --------------------------------------------------------------------------

DOSSIER = Path(__file__).resolve().parent / "sauvegardes"
_RE_JOURNEE = re.compile(r"^journee_\d{4}-\d{2}-\d{2}\.json$")
FICHIER_PREFS = "preferences.json"


def _ecrire(nom: str, payload: dict) -> Path:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    chemin = DOSSIER / nom
    chemin.write_bytes(dumps(payload))
    return chemin


def save_local_journee(payload: dict) -> Path:
    d = payload.get("date") or dt.date.today().isoformat()
    return _ecrire(f"journee_{d}.json", payload)


def save_local_prefs(payload: dict) -> Path:
    return _ecrire(FICHIER_PREFS, payload)


def list_local_journees() -> list:
    if not DOSSIER.is_dir():
        return []
    return sorted((p.name for p in DOSSIER.iterdir() if _RE_JOURNEE.match(p.name)), reverse=True)


def read_local_journee(nom: str) -> bytes:
    if nom not in list_local_journees():      # pas de chemin libre -> pas de traversée
        raise ValueError("Sauvegarde introuvable.")
    return (DOSSIER / nom).read_bytes()


def load_local_prefs() -> dict:
    """Préférences du disque, {} si absentes ou invalides (jamais d'exception)."""
    try:
        p = DOSSIER / FICHIER_PREFS
        if p.is_file():
            return parse_payload(p.read_bytes())["preferences"]
    except (OSError, ValueError):
        pass
    return {}
