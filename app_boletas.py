import streamlit as st
from google import genai
from google.genai import types
from PIL import Image, ImageOps
import pandas as pd
import io
import math
import json
import re
from datetime import datetime

def redondear_precio_venta(precio, multiplo=50):
    return int(math.ceil(precio / multiplo) * multiplo)

def comprimir_para_ocr(imagen_pil, max_lado=800):
    img = ImageOps.exif_transpose(imagen_pil).convert("L")
    img.thumbnail((max_lado, max_lado))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=65, optimize=True)
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")

def limpiar_json(texto):
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio != -1 and fin != -1:
        texto = texto[inicio:fin+1]
    return json.loads(texto)

st.set_page_config(page_title="Precios Minimarket", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        .block-container { padding: 1rem; max-width: 100%; }
        [data-testid="stCameraInput"] video, [data-testid="stCameraInput"] img { max-height: 60vh; object-fit: contain; }
    </style>
""", unsafe_allow_html=True)

st.title("Escáner Rápido — Minimarket")

if "historial" not in st.session_state:
    st.session_state.historial = []
if "doc_actual" not in st.session_state:
    st.session_state.doc_actual = None
if "ultima_foto_id" not in st.session_state:
    st.session_state.ultima_foto_id = None

col_cfg1, col_cfg2, col_cfg3 = st.columns([1, 1, 1.2])
with col_cfg1:
    margen = st.number_input("Margen (%)", min_value=5, max_value=200, value=35, step=5)
with col_cfg2:
    redondeo = st.selectbox("Redondear a:", [10, 50, 100], index=1)
with col_cfg3:
    modo_vista = st.radio("Vista:", ["Horizontal", "Vertical"], horizontal=True)

api_key = st.secrets.get("GEMINI_API_KEY", "").strip()
if not api_key:
    st.error("Falta configurar GEMINI_API_KEY en Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)

tab_cam, tab_subir = st.tabs(["Cámara Directa", "Subir Foto"])
with tab_cam:
    foto_cam = st.camera_input("Toma la foto")
with tab_subir:
    foto_archivo = st.file_uploader("Sube una foto", type=["jpg", "jpeg", "png", "webp"])

foto = foto_cam if foto_cam is not None else foto_archivo

if foto is not None:
    foto_id = f"{foto.name}_{foto.size}"
    if foto_id != st.session_state.ultima_foto_id:
        with st.spinner("Leyendo factura..."):
            parte_imagen = comprimir_para_ocr(Image.open(foto))
            modelos = [
                "gemini-3.8-flash-lite",
                "gemini-3.8-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemma-3-27b-it"
            ]
            prompt = (
                "Analiza esta boleta o factura chilena y responde ÚNICAMENTE con un JSON válido (sin texto extra):\n"
                '{"tipo_documento": "Factura o Boleta", "proveedor": "nombre", "numero_documento": "numero", '
                '"iva_desglosado_aparte": true, "items": [{"nombre": "prod", "cantidad_bultos": 1, "unidades_por_bulto": 1, "precio_total_linea": 1000}]}\n'
                "Reglas: iva_desglosado_aparte=true si los precios de filas son netos y el IVA (19%) se suma al final, false si ya incluye IVA. "
                "unidades_por_bulto: si dice CAJA 12 UNID o PACK 6 pon 12 o 6; si es unitario pon 1. "
                "precio_total_linea: número sin puntos ni signo peso."
            )
            
            respuesta = None
            ultimo_error = ""
            
            for m in modelos:
                try:
                    config_kwargs = {"temperature": 0.0}
                    if "gemini" in m:
                        config_kwargs["response_mime_type"] = "application/json"
                        
                    respuesta = client.models.generate_content(
                        model=m,
                        contents=[parte_imagen, prompt],
                        config=types.GenerateContentConfig(**config_kwargs),
                    )
                    break
                except Exception as e:
                    ultimo_error = f"{m}: {e}"
                
            if respuesta is not None:
                try:
                    st.session_state.doc_actual = limpiar_json(respuesta.text)
                    st.session_state.ultima_foto_id = foto_id
                except Exception as e:
                    st.error(f"Error al leer datos: {e}")
                    st.code(respuesta.text)
            else:
                st.error(f"Cuota agotada o error de API: {ultimo_error}")

if st.session_state.doc_actual is not None:
    doc = st.session_state.doc_actual
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    filas_actuales = []
    for p in doc.get("items", []):
        try:
            bultos = int(float(p.get("cantidad_bultos") or 1)) or 1
            unid_bulto = int(float(p.get("unidades_por_bulto") or 1)) or 1
            total_unidades = bultos * unid_bulto
            total_linea = float(p.get("precio_total_linea
