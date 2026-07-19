"""
Red TAMI — Panel de Seguimiento (local)
----------------------------------------
Dashboard local en Streamlit que se conecta a un Google Sheet privado
(vía cuenta de servicio) y visualiza las respuestas de la encuesta de
prevención de cáncer de mama: perfil, tamizaje mamográfico y barreras.

Ejecutar con:
    streamlit run app_dashboardredtami.py

Requiere:
    - Un archivo credentials.json de una cuenta de servicio de Google
      (o las mismas credenciales cargadas en .streamlit/secrets.toml)
    - El Google Sheet compartido con el correo de esa cuenta de servicio
      (permiso de "Lector" es suficiente)

Ver README.md para el detalle de configuración paso a paso.
"""

import re
import json
from datetime import datetime

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
import plotly.express as px

# --------------------------------------------------------------------------
# Configuración de página y tema visual
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Red TAMI — Panel de Seguimiento",
    page_icon="🎗️",
    layout="wide",
)

ROSE = "#9E3A52"
ROSE_SOFT = "#F0D8DC"
TEAL = "#24504C"
TEAL_SOFT = "#DCE8E4"
GOLD = "#B9822F"
GOLD_SOFT = "#F1E1C6"
INK = "#202D2C"
INK_SOFT = "#5B6664"
PALETTE = [ROSE, TEAL, GOLD, "#6B7FA3", "#8C6E9C", "#4C8577"]

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #FBF6F4; }}
    h1, h2, h3 {{ color: {INK}; font-family: 'Georgia', serif; }}
    div[data-testid="stMetricValue"] {{ color: {ROSE}; font-weight: 600; }}
    div[data-testid="stMetricLabel"] {{ color: {INK_SOFT}; }}
    .journey-card {{
        background:#fff; border:1px solid #E6DAD5; border-radius:10px;
        padding:14px 10px; text-align:center; height:100%;
    }}
    .journey-num {{
        font-size:26px; font-weight:700; color:{TEAL}; margin:6px 0 2px;
    }}
    .journey-label {{ font-size:12.5px; font-weight:600; color:{INK}; }}
    .journey-sub {{ font-size:11px; color:{INK_SOFT}; }}
    .comment-box {{
        border-left:3px solid {TEAL}; background:{TEAL_SOFT}; border-radius:0 8px 8px 0;
        padding:10px 14px; margin-bottom:10px; font-size:13.5px; color:{INK};
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CONFIG_PATH = "config.json"


# --------------------------------------------------------------------------
# Persistencia simple de la config (sheet id / worksheet) entre ejecuciones
# --------------------------------------------------------------------------
def load_local_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_local_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # no bloquear la app si no se puede escribir


# --------------------------------------------------------------------------
# Conexión a Google Sheets
# --------------------------------------------------------------------------
@st.cache_resource
def get_gspread_client():
    """
    Creates the gspread client securely.
    Checks Streamlit Cloud secrets first; falls back to local credentials.json.
    """
    # 1. Check if running on Streamlit Cloud with production secrets injected
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=SCOPES
        )
        return gspread.authorize(creds)
        
    # 2. Local Fallback: Use the rock-solid absolute path method we built
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_cred_path = os.path.join(script_dir, "credentials.json")
    
    if not os.path.exists(full_cred_path):
        raise FileNotFoundError(
            "No se encontraron credenciales de producción en Streamlit Cloud, "
            f"ni el archivo local en: {full_cred_path}"
        )
        
    creds = Credentials.from_service_account_file(full_cred_path, scopes=SCOPES)
    return gspread.authorize(creds)


def extract_sheet_id(id_or_url: str) -> str:
    """Permite pegar la URL completa del Sheet o solo el ID."""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", id_or_url)
    return match.group(1) if match else id_or_url.strip()


@st.cache_data(ttl=300, show_spinner="Descargando datos desde Google Sheets…")
def fetch_sheet_df(sheet_id: str, worksheet_name: str, _cache_bust: int = 0) -> pd.DataFrame:
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet_name) if worksheet_name else sh.sheet1
    records = ws.get_all_records()
    return pd.DataFrame(records)


# --------------------------------------------------------------------------
# Normalización de columnas y datos (encuesta Red TAMI)
# --------------------------------------------------------------------------
def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def find_col(columns, *keywords):
    for c in columns:
        low = norm(c).lower()
        if all(k.lower() in low for k in keywords):
            return c
    return None


def is_yes(v) -> bool:
    return norm(v).lower() in ("si", "sí")


def is_no(v) -> bool:
    return norm(v).lower() == "no"


def build_clean_df(raw: pd.DataFrame) -> pd.DataFrame:
    cols = list(raw.columns)
    mapping = {
        "birth_year": find_col(cols, "año de nacimiento"),
        "comuna": find_col(cols, "comuna de residencia"),
        "health_system": find_col(cols, "sistema de salud"),
        "occupation": find_col(cols, "ocupación"),
        "smoked": find_col(cols, "fumado"),
        "family_history": find_col(cols, "familiar directo"),
        "had_mammogram": find_col(cols, "se ha hecho una mamografía"),
        "reason": find_col(cols, "no te has hecho una mamografía"),
        "has_files": find_col(cols, "archivos e informe"),
        "wants_info": find_col(cols, "más información sobre el cuidado"),
        "open_question": find_col(cols, "preguntaría"),
    }

    out = pd.DataFrame()
    for key, col in mapping.items():
        out[key] = raw[col].map(norm) if col else ""

    if mapping["birth_year"]:
        out["birth_year"] = pd.to_numeric(raw[mapping["birth_year"]], errors="coerce")
    else:
        out["birth_year"] = pd.NA

    out["age"] = datetime.now().year - out["birth_year"]

    # descartar filas totalmente vacías
    out = out[
        out.drop(columns=["birth_year", "age"]).apply(lambda r: any(v != "" for v in r), axis=1)
        | out["birth_year"].notna()
    ].reset_index(drop=True)

    return out, mapping


# --------------------------------------------------------------------------
# Sidebar — configuración de conexión
# --------------------------------------------------------------------------
st.sidebar.header("Conexión a Google Sheets")

saved_cfg = load_local_config()

sheet_input = st.sidebar.text_input(
    "URL o ID del Google Sheet",
    value=saved_cfg.get("sheet_id", ""),
    placeholder="https://docs.google.com/spreadsheets/d/…",
)
worksheet_input = st.sidebar.text_input(
    "Nombre de la hoja (worksheet)",
    value=saved_cfg.get("worksheet_name", ""),
    placeholder="Dejar en blanco para usar la primera hoja",
)

col_a, col_b = st.sidebar.columns(2)
connect_clicked = col_a.button("Conectar", type="primary", use_container_width=True)
refresh_clicked = col_b.button("Actualizar", use_container_width=True)

if "cache_bust" not in st.session_state:
    st.session_state["cache_bust"] = 0

if refresh_clicked:
    st.session_state["cache_bust"] += 1
    st.cache_data.clear()

if connect_clicked and sheet_input:
    save_local_config({"sheet_id": extract_sheet_id(sheet_input), "worksheet_name": worksheet_input})

st.sidebar.caption(
    "Los datos se cachean 5 minutos. Usa 'Actualizar' para forzar una nueva lectura."
)

# --------------------------------------------------------------------------
# Encabezado
# --------------------------------------------------------------------------
st.markdown("##### RED TAMI · PREVENCIÓN DE CÁNCER DE MAMA")
st.title("Panel de seguimiento")
st.caption(
    "Respuestas de la encuesta de pacientes: perfil, tamizaje mamográfico y barreras de acceso. "
    "Conectado en vivo a Google Sheets."
)

if not sheet_input:
    st.info(
        "👈 Ingresa la URL o el ID de tu Google Sheet en la barra lateral y presiona **Conectar** "
        "para cargar los datos."
    )
    st.stop()

sheet_id = extract_sheet_id(sheet_input)

try:
    raw_df = fetch_sheet_df(sheet_id, worksheet_input, st.session_state["cache_bust"])
except gspread.exceptions.SpreadsheetNotFound:
    st.error(
        "No se encontró el Sheet. Verifica el ID/URL y que esté compartido con el correo "
        "de la cuenta de servicio (ver README.md)."
    )
    st.stop()
except gspread.exceptions.WorksheetNotFound:
    st.error(f"No existe una hoja llamada '{worksheet_input}' en este Sheet.")
    st.stop()
except FileNotFoundError as e:
    st.error(f"Error de archivo detectado por el script: {e}")
    st.stop()
except Exception as e:  # noqa: BLE001
    st.error(f"Error al conectar con Google Sheets: {e}")
    st.stop()

if raw_df.empty:
    st.warning("El Sheet se conectó correctamente pero no tiene filas de datos todavía.")
    st.stop()

df, colmap = build_clean_df(raw_df)
st.caption(f"Última actualización: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')} · {len(df)} registros")

# --------------------------------------------------------------------------
# KPIs
# --------------------------------------------------------------------------
n = len(df)
screened = df[df["had_mammogram"].map(is_yes)]
not_screened = df[df["had_mammogram"].map(is_no)]
family_yes = df[df["family_history"].map(is_yes)]
smoked_yes = df[df["smoked"].map(is_yes)]
wants_info_yes = df[df["wants_info"].map(is_yes)]

ages = df["age"].dropna()
avg_age = int(round(ages.mean())) if len(ages) else "—"
screen_rate = round(100 * len(screened) / n) if n else 0
family_rate = round(100 * len(family_yes) / n) if n else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Encuestadas", n)
k2.metric("Se ha hecho mamografía", f"{screen_rate}%")
k3.metric("Con antecedente familiar", f"{family_rate}%")
k4.metric("Edad promedio", avg_age)
k5.metric("Quiere más información", f"{len(wants_info_yes)}/{n}")

st.divider()

# --------------------------------------------------------------------------
# 01 — Recorrido de la paciente
# --------------------------------------------------------------------------
st.subheader("01 · El recorrido de la paciente")
st.caption(
    "Cada tarjeta resume, con los datos actuales, cuántas encuestadas llegaron a esa etapa "
    "del cuestionario — y dónde se ramifica el camino según si se hicieron o no una mamografía."
)

has_report = screened[screened["has_files"].map(is_yes)]
reasons_given = not_screened[not_screened["reason"] != ""]

journey_steps = [
    ("Perfil registrado", n, "determinantes de salud"),
    ("Antecedente familiar", len(family_yes), f"de {n} reportan caso en la familia"),
    ("¿Se hizo mamografía?", f"{len(screened)} Sí / {len(not_screened)} No", "se ramifica aquí"),
    ("Con informe de imágenes", len(has_report), f"de {len(screened)} que se hicieron el examen"),
    ("Motivo declarado", len(reasons_given), f"de {len(not_screened)} que no se lo han hecho"),
    ("Interés en más info", len(wants_info_yes), f"de {n} quieren seguir informadas"),
]

jcols = st.columns(len(journey_steps))
for jc, (label, value, sub) in zip(jcols, journey_steps):
    jc.markdown(
        f"""<div class="journey-card">
                <div class="journey-label">{label}</div>
                <div class="journey-num">{value}</div>
                <div class="journey-sub">{sub}</div>
            </div>""",
        unsafe_allow_html=True,
    )

st.divider()

# --------------------------------------------------------------------------
# 02 — Tamizaje y factores clave
# --------------------------------------------------------------------------
st.subheader("02 · Tamizaje y factores clave")
st.caption(
    "Tasa de mamografía realizada, cruzada con los determinantes registrados: "
    "antecedente familiar, hábito tabáquico y sistema de salud."
)

c1, c2 = st.columns([1, 1.4])

with c1:
    fig_overall = go.Figure(
        data=[
            go.Pie(
                labels=["Se hizo mamografía", "No se la ha hecho"],
                values=[len(screened), len(not_screened)],
                hole=0.65,
                marker=dict(colors=[ROSE, ROSE_SOFT]),
                textinfo="value+percent",
            )
        ]
    )
    fig_overall.update_layout(
        height=280, margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", y=-0.15),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_overall, use_container_width=True)

with c2:
    def rate(sub_df):
        s = sub_df[sub_df["had_mammogram"].map(is_yes)]
        return (round(100 * len(s) / len(sub_df)) if len(sub_df) else 0), len(s), len(sub_df)

    factor_groups = [
        ("Con antecedente familiar", family_yes),
        ("Sin antecedente familiar", df[df["family_history"].map(is_no)]),
        ("Fumadoras", smoked_yes),
        ("No fumadoras", df[df["smoked"].map(is_no)]),
    ]
    for hs in sorted(df["health_system"].unique()):
        if hs:
            factor_groups.append((hs, df[df["health_system"] == hs]))

    rows = []
    for label, sub in factor_groups:
        if len(sub) == 0:
            continue
        pct, count, total = rate(sub)
        rows.append({"Factor": label, "Tasa de tamizaje (%)": pct, "detalle": f"{count} de {total}"})

    if rows:
        fdf = pd.DataFrame(rows)
        fig_factors = px.bar(
            fdf, x="Tasa de tamizaje (%)", y="Factor", orientation="h",
            text="detalle", color_discrete_sequence=[ROSE],
        )
        fig_factors.update_traces(textposition="outside")
        fig_factors.update_layout(
            height=280, margin=dict(t=10, b=10, l=10, r=10),
            xaxis_range=[0, 110], paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_factors, use_container_width=True)
    else:
        st.info("Sin datos suficientes para este cruce todavía.")

st.divider()

# --------------------------------------------------------------------------
# 03 — Barreras
# --------------------------------------------------------------------------
st.subheader("03 · Barreras declaradas")
st.caption("Motivo principal entre quienes aún no se han hecho una mamografía.")

reason_counts = reasons_given["reason"].value_counts()
if len(reason_counts):
    fig_reasons = px.bar(
        x=reason_counts.values, y=reason_counts.index, orientation="h",
        labels={"x": "Encuestadas", "y": ""}, color_discrete_sequence=[GOLD],
    )
    fig_reasons.update_layout(
        height=280, margin=dict(t=10, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_reasons, use_container_width=True)
else:
    st.info("No hay motivos registrados en los datos actuales.")

st.divider()

# --------------------------------------------------------------------------
# 04 — Perfil demográfico
# --------------------------------------------------------------------------
st.subheader("04 · Perfil demográfico")

d1, d2 = st.columns(2)

with d1:
    st.markdown("**Distribución etaria**")
    bins = [0, 30, 40, 50, 60, 200]
    labels = ["<30", "30–39", "40–49", "50–59", "60+"]
    age_buckets = pd.cut(ages, bins=bins, labels=labels, right=False).value_counts().reindex(labels).fillna(0)
    fig_age = px.bar(x=age_buckets.index, y=age_buckets.values, color_discrete_sequence=[TEAL])
    fig_age.update_layout(
        height=260, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="", yaxis_title="",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_age, use_container_width=True)

with d2:
    st.markdown("**Comuna de residencia**")
    comuna_counts = df["comuna"].replace("", "No especifica").value_counts()
    fig_comuna = px.bar(x=comuna_counts.index, y=comuna_counts.values, color_discrete_sequence=[ROSE])
    fig_comuna.update_layout(
        height=260, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="", yaxis_title="",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_comuna, use_container_width=True)

d3, d4 = st.columns(2)

with d3:
    st.markdown("**Sistema de salud**")
    hs_counts = df["health_system"].replace("", "No especifica").value_counts()
    fig_hs = go.Figure(data=[go.Pie(labels=hs_counts.index, values=hs_counts.values, hole=0.55,
                                     marker=dict(colors=PALETTE))])
    fig_hs.update_layout(height=260, margin=dict(t=10, b=10, l=10, r=10),
                          legend=dict(orientation="h", y=-0.2), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_hs, use_container_width=True)

with d4:
    st.markdown("**Ocupación**")
    occ_counts = df["occupation"].replace("", "No especifica").value_counts()
    fig_occ = px.bar(x=occ_counts.values, y=occ_counts.index, orientation="h",
                      color_discrete_sequence=[GOLD])
    fig_occ.update_layout(
        height=260, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="", yaxis_title="",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_occ, use_container_width=True)

st.divider()

# --------------------------------------------------------------------------
# 05 — Cierre e interés
# --------------------------------------------------------------------------
st.subheader("05 · Cierre e interés")

e1, e2 = st.columns(2)

with e1:
    st.markdown("**¿Quiere más información?**")
    fig_interest = go.Figure(
        data=[go.Pie(
            labels=["Quiere más información", "No / sin respuesta"],
            values=[len(wants_info_yes), n - len(wants_info_yes)],
            hole=0.65, marker=dict(colors=[TEAL, TEAL_SOFT]),
        )]
    )
    fig_interest.update_layout(height=260, margin=dict(t=10, b=10, l=10, r=10),
                                legend=dict(orientation="h", y=-0.15), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_interest, use_container_width=True)

with e2:
    st.markdown("**Preguntas y comentarios abiertos**")
    comments = [c for c in df["open_question"].tolist() if c]
    if comments:
        for i, c in enumerate(comments, start=1):
            st.markdown(f'<div class="comment-box"><b>Encuestada {i}</b><br>{c}</div>', unsafe_allow_html=True)
    else:
        st.info("No hay comentarios registrados en los datos actuales.")

st.divider()
with st.expander("Ver columnas detectadas / diagnóstico de mapeo"):
    st.json({k: v for k, v in colmap.items()})
    st.caption(
        "Si alguna columna aparece como null, el script no encontró una columna del Sheet "
        "que contenga las palabras clave esperadas. Revisa el encabezado exacto en tu Sheet."
    )
