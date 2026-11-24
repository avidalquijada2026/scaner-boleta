import streamlit as st
from google import genai
from google.genai import types
from PIL import Image, ImageOps
import pandas as pd
import io
import math
import json
from datetime import datetime

def redondear_precio_venta(precio, multiplo=50):
    return int(math.ceil(precio / multiplo) * multiplo)

def comprimir_para_ocr(imagen_pil, max_lado=800):
    img = ImageOps.exif_transpose(imagen_pil).convert("L")
    img.thumbnail((max_lado, max_lado))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=65, optimize=True)
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")

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

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"].strip())

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
                "Analiza esta boleta o factura chilena y responde ÚNICAMENTE con un JSON válido con esta estructura exacta:\n"
                '{"tipo_documento": "Factura o Boleta", "proveedor": "nombre", "numero_documento": "numero", '
                '"iva_desglosado_aparte": true, "items": [{"nombre": "prod", "cantidad_bultos": 1, "unidades_por_bulto": 1, "precio_total_linea": 1000}]}\n'
                "Reglas:\n"
                "- iva_desglosado_aparte: true si los precios de las filas son netos y el IVA (19%) se suma al final (facturas), false si ya incluye IVA (boletas).\n"
                "- unidades_por_bulto: si dice CAJA 12 UNID, PACK 6, FARDÓN 6, etc., pon 12, 6, etc.; si es unitario pon 1.\n"
                "- precio_total_linea: número entero sin puntos ni signo peso."
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
                            temperature=0.0,
                        ),
                    )
                    break
                except Exception as e:
                    ultimo_error = f"{m}: {e}"
                
            if respuesta is not None:
                try:
                    st.session_state.doc_actual = json.loads(respuesta.text)
                    st.session_state.ultima_foto_id = foto_id
                except Exception as e:
                    st.error(f"Error al interpretar respuesta: {e}")
            else:
                st.error(f"Error: {ultimo_error}")

if st.session_state.doc_actual is not None:
    doc = st.session_state.doc_actual
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    filas_actuales = []
    for p in doc.get("items", []):
        bultos = int(p.get("cantidad_bultos", 1)) or 1
        unid_bulto = int(p.get("unidades_por_bulto", 1)) or 1
        total_unidades = bultos * unid_bulto
        total_linea = float(p.get("precio_total_linea", 0))
        
        if doc.get("iva_desglosado_aparte", True):
            costo_unit_neto = round(total_linea / total_unidades)
            costo_unit_iva = round(costo_unit_neto * 1.19)
        else:
            costo_unit_iva = round(total_linea / total_unidades)
            costo_unit_neto = round(costo_unit_iva / 1.19)
        
        precio_venta = redondear_precio_venta(costo_unit_iva * (1 + (margen / 100)), redondeo)
        
        filas_actuales.append({
            "Fecha": fecha_hoy,
            "Proveedor": doc.get("proveedor", ""),
            "N° Doc": doc.get("numero_documento", ""),
            "Producto": p.get("nombre", ""),
            "Unid.": total_unidades,
            "Neto Unit.": costo_unit_neto,
            "Costo c/IVA": costo_unit_iva,
            f"Venta ({margen}%)": precio_venta,
            "Ganancia": precio_venta - costo_unit_iva
        })

    df_actual = pd.DataFrame(filas_actuales)
    st.subheader(f"{doc.get('tipo_documento', 'Documento')} N° {doc.get('numero_documento', '')} — {doc.get('proveedor', '')}")
    
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
