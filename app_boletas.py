import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel
from PIL import Image
import pandas as pd
import io
import time

class Producto(BaseModel):
    nombre: str
    cantidad: int
    precio_unitario: float
    precio_total: float

class Documento(BaseModel):
    tipo_documento: str
    iva_desglosado_aparte: bool
    items: list[Producto]

st.title("Escáner de Boletas y Facturas a Excel")

api_key = st.secrets["GEMINI_API_KEY"].strip()
foto = st.camera_input("Toma una foto de la factura o boleta")

if foto is not None:
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
        
    with st.spinner("Extrayendo productos y calculando totales..."):
        prompt = (
            "Extrae todos los productos con su cantidad, precio unitario y precio total. "
            "Indica el tipo_documento ('Factura' o 'Boleta') y marca iva_desglosado_aparte como true "
            "solo si los precios de los ítems son netos y el IVA se suma al final (como en facturas), "
            "o false si el IVA ya está incluido en los ítems (como en boletas)."
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
            doc = Documento.model_validate_json(respuesta.text)
            
            filas = []
            subtotal = 0
            for p in doc.items:
                filas.append({
                    "Producto": p.nombre,
                    "Cantidad": p.cantidad,
                    "Precio Unitario": p.precio_unitario,
                    "Total": p.precio_total
                })
                subtotal += p.precio_total
                
            if doc.iva_desglosado_aparte:
                neto = subtotal
                iva = round(neto * 0.19)
                total_final = neto + iva
            else:
                total_final = subtotal
                neto = round(total_final / 1.19)
                iva = total_final - neto

            df = pd.DataFrame(filas)
            st.subheader(f"Documento detectado: {doc.tipo_documento}")
            st.dataframe(df, use_container_width=True)
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Monto Neto", f"${neto:,.0f}")
            col2.metric("IVA (19%)", f"${iva:,.0f}")
            col3.metric("Total a Pagar", f"${total_final:,.0f}")
            
            resumen = pd.DataFrame([
                {"Producto": "MONTO NETO", "Cantidad": None, "Precio Unitario": None, "Total": neto},
                {"Producto": "IVA (19%)", "Cantidad": None, "Precio Unitario": None, "Total": iva},
                {"Producto": "TOTAL A PAGAR", "Cantidad": None, "Precio Unitario": None, "Total": total_final},
            ])
            df_excel = pd.concat([df, resumen], ignore_index=True)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df_excel.to_excel(writer, index=False, sheet_name="Detalle")
                
            st.download_button(
                label="Descargar archivo Excel",
                data=buffer.getvalue(),
                file_name="documento_escaneado.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.error(f"Servidores saturados temporalmente. Último detalle: {ultimo_error}")
