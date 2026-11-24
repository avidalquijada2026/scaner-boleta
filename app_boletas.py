import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel
from PIL import Image
import pandas as pd
import io
import time
import math
from datetime import datetime

class Producto(BaseModel):
    nombre: str
    cantidad: int
    precio_unitario_documento: float
    precio_total_linea: float

class Documento(BaseModel):
    tipo_documento: str
    proveedor: str
    numero_documento: str
    iva_desglosado_aparte: bool
    items: list[Producto]

def redondear_precio_venta(precio, multiplo=50):
    return int(math.ceil(precio / multiplo) * multiplo)

st.set_page_config(page_title="Precios Minimarket", layout="wide")
st.title("Escáner de Facturas y Calculadora de Precios de Venta")

if "historial" not in st.session_state:
    st.session_state.historial = []
if "doc_actual" not in st.session_state:
    st.session_state.doc_actual = None
if "ultima_foto_id" not in st.session_state:
    st.session_state.ultima_foto_id = None

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1:
    margen = st.number_input("Margen de ganancia (%)", min_value=5, max_value=200, value=35, step=5)
with col_cfg2:
    redondeo = st.selectbox("Redondear precio de venta al múltiplo de:", [10, 50, 100], index=1)

api_key = st.secrets["GEMINI_API_KEY"].strip()
foto = st.camera_input("Toma una foto de la factura o boleta del proveedor")

if foto is not None:
    foto_id = f"{foto.name}_{foto.size}"
    if foto_id != st.session_state.ultima_foto_id:
        imagen = Image.open(foto)
        client = genai.Client(api_key=api_key)
        
        modelos = ["gemini-3.8-flash", "gemini-3.8-flash-lite"]
        try:
            for m in client.models.list():
                nombre = m.name.replace("models/", "")
                if "gemini" in nombre and "flash" in nombre and nombre not in modelos:
                    modelos.append(nombre)
        except Exception:
            pass
            
        with st.spinner("Leyendo factura del proveedor..."):
            prompt = (
                "Extrae el proveedor, número de documento y todos los productos con su cantidad, "
                "precio unitario y total de línea. Indica tipo_documento ('Factura' o 'Boleta') y marca "
                "iva_desglosado_aparte como true solo si los precios de las filas son netos y el IVA (19%) "
                "se suma al final (facturas), o false si el IVA ya está incluido en los ítems (boletas)."
            )
            respuesta = None
            ultimo_error = ""
            
            for intento in range(2):
                for m in modelos:
                    try:
                        respuesta = client.models.generate_content(
                            model=m,
                            contents=[imagen, prompt],
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=Documento,
                            ),
                        )
                        break
                    except Exception as e:
                        ultimo_error = f"{m}: {e}"
                if respuesta is not None:
                    break
                time.sleep(2)
                
            if respuesta is not None:
                st.session_state.doc_actual = Documento.model_validate_json(respuesta.text)
                st.session_state.ultima_foto_id = foto_id
            else:
                st.error(f"Servidores saturados temporalmente. Último detalle: {ultimo_error}")

if st.session_state.doc_actual is not None:
    doc = st.session_state.doc_actual
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    filas_actuales = []
    for p in doc.items:
        cant = p.cantidad if p.cantidad > 0 else 1
        total_linea = p.precio_total_linea if p.precio_total_linea > 0 else (p.precio_unitario_documento * cant)
        
        if doc.iva_desglosado_aparte:
            costo_unit_neto = round(total_linea / cant)
            costo_unit_iva = round(costo_unit_neto * 1.19)
        else:
            costo_unit_iva = round(total_linea / cant)
            costo_unit_neto = round(costo_unit_iva / 1.19)
        
        precio_sin_redondear = costo_unit_iva * (1 + (margen / 100))
        precio_venta = redondear_precio_venta(precio_sin_redondear, redondeo)
        ganancia_unit = precio_venta - costo_unit_iva
        
        filas_actuales.append({
            "Fecha": fecha_hoy,
            "Proveedor": doc.proveedor,
            "N° Factura": doc.numero_documento,
            "Producto": p.nombre,
            "Cant.": cant,
            "Costo Unit. Neto": costo_unit_neto,
            "Costo Unit. c/IVA": costo_unit_iva,
            "Precio Venta Sugerido": precio_venta,
            "Ganancia Unit.": ganancia_unit
        })

    df_actual = pd.DataFrame(filas_actuales)
    st.subheader(f"{doc.tipo_documento} N° {doc.numero_documento} - {doc.proveedor}")
    
    df_editado = st.data_editor(
        df_actual,
        use_container_width=True,
        num_rows="dynamic",
        key=f"editor_{st.session_state.ultima_foto_id}_{margen}_{redondeo}"
    )
    
    if st.button("Guardar en la lista de actualización del día", type="primary"):
        st.session_state.historial.extend(df_editado.to_dict(orient="records"))
        st.success("Productos agregados a la lista del día.")

if len(st.session_state.historial) > 0:
    st.divider()
    st.subheader("Lista Consolidada del Día para Actualizar Caja / Etiquetas")
    df_dia = pd.DataFrame(st.session_state.historial)
    st.dataframe(df_dia, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_dia.to_excel(writer, index=False, sheet_name="Precios_Minimarket")
            
        st.download_button(
            label="Descargar Excel para Actualizar Sistema",
            data=buffer.getvalue(),
            file_name=f"precios_minimarket_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    with col2:
        if st.button("Limpiar lista del día"):
            st.session_state.historial = []
            st.rerun()
