import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel
from PIL import Image, ImageOps
import pandas as pd
import io
import math
from datetime import datetime

class Producto(BaseModel):
    nombre: str
    cantidad_bultos: int
    unidades_por_bulto: int
    precio_total_linea: float

class Documento(BaseModel):
    tipo_documento: str
    proveedor: str
    numero_documento: str
    iva_desglosado_aparte: bool
    items: list[Producto]

def redondear_precio_venta(precio, multiplo=50):
    return int(math.ceil(precio / multiplo) * multiplo)

def comprimir_para_ocr(imagen_pil, max_lado=1024):
    img = ImageOps.exif_transpose(imagen_pil).convert("L")
    img.thumbnail((max_lado, max_lado))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=75, optimize=True)
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")

@st.cache_resource
def obtener_cliente(api_key):
    return genai.Client(api_key=api_key, http_options={"timeout": 25.0})

st.set_page_config(page_title="Precios Minimarket", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        .block-container {
            padding: 1rem;
            max-width: 100%;
        }
        [data-testid="stCameraInput"] video, [data-testid="stCameraInput"] img {
            max-height: 60vh;
            object-fit: contain;
        }
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

client = obtener_cliente(st.secrets["GEMINI_API_KEY"].strip())

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
            modelos = ["gemini-3.8-flash-lite", "gemini-3.8-flash"]
            prompt = (
                "Extrae proveedor, numero_documento, tipo_documento ('Factura' o 'Boleta'), "
                "iva_desglosado_aparte (true si filas son netas y el IVA se suma al final, false si ya incluye IVA) "
                "y lista de items: nombre, cantidad_bultos (CANT), unidades_por_bulto (ej: CAJA 12 -> 12, PACK 6 -> 6, suelto -> 1) y precio_total_linea."
            )
            
            respuesta = None
            ultimo_error = ""
            
            for m in modelos:
                try:
                    respuesta = client.models.generate_content(
                        model=m,
                        contents=[parte_imagen, prompt],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=Documento,
                            temperature=0.0,
                            media_resolution="MEDIA_RESOLUTION_LOW",
                        ),
                    )
                    break
                except Exception as e:
                    ultimo_error = f"{m}: {e}"
                
            if respuesta is not None:
                st.session_state.doc_actual = Documento.model_validate_json(respuesta.text)
                st.session_state.ultima_foto_id = foto_id
            else:
                st.error(f"Error: {ultimo_error}")

if st.session_state.doc_actual is not None:
    doc = st.session_state.doc_actual
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    filas_actuales = []
    for p in doc.items:
        bultos = p.cantidad_bultos if p.cantidad_bultos > 0 else 1
        unid_bulto = p.unidades_por_bulto if p.unidades_por_bulto > 0 else 1
        total_unidades = bultos * unid_bulto
        
        if doc.iva_desglosado_aparte:
            costo_unit_neto = round(p.precio_total_linea / total_unidades)
            costo_unit_iva = round(costo_unit_neto * 1.19)
        else:
            costo_unit_iva = round(p.precio_total_linea / total_unidades)
            costo_unit_neto = round(costo_unit_iva / 1.19)
        
        precio_venta = redondear_precio_venta(costo_unit_iva * (1 + (margen / 100)), redondeo)
        
        filas_actuales.append({
            "Fecha": fecha_hoy,
            "Proveedor": doc.proveedor,
            "N° Doc": doc.numero_documento,
            "Producto": p.nombre,
            "Unid.": total_unidades,
            "Neto Unit.": costo_unit_neto,
            "Costo c/IVA": costo_unit_iva,
            f"Venta ({margen}%)": precio_venta,
            "Ganancia": precio_venta - costo_unit_iva
        })

    df_actual = pd.DataFrame(filas_actuales)
    st.subheader(f"{doc.tipo_documento} N° {doc.numero_documento} — {doc.proveedor}")
    
    columnas_visibles = ["Producto", "Unid.", "Costo c/IVA", f"Venta ({margen}%)", "Ganancia"] if modo_vista == "Vertical" else list(df_actual.columns)

    df_editado = st.data_editor(
        df_actual[columnas_visibles],
        use_container_width=True,
        num_rows="dynamic",
        key=f"editor_{st.session_state.ultima_foto_id}_{margen}_{redondeo}_{modo_vista}"
    )
    
    if st.button("Guardar en lista del día", type="primary"):
        if modo_vista == "Vertical":
            df_guardar = df_actual.copy()
            for col in columnas_visibles:
                if col in df_editado.columns:
                    df_guardar[col] = df_editado[col]
            st.session_state.historial.extend(df_guardar.to_dict(orient="records"))
        else:
            st.session_state.historial.extend(df_editado.to_dict(orient="records"))
        st.success("Guardado.")

if len(st.session_state.historial) > 0:
    st.divider()
    st.subheader("Registro del Día")
    df_dia = pd.DataFrame(st.session_state.historial)
    st.dataframe(df_dia, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_dia.to_excel(writer, index=False, sheet_name="Precios_Minimarket")
            
        st.download_button(
            label="Descargar Excel del Día",
            data=buffer.getvalue(),
            file_name=f"precios_minimarket_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    with col2:
        if st.button("Limpiar lista"):
            st.session_state.historial = []
            st.rerun()
