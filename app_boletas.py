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

class Boleta(BaseModel):
    items: list[Producto]

st.title("Escáner de Boletas a Excel")

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
        
    with st.spinner("Extrayendo productos y precios..."):
        prompt = "Extrae todos los productos de esta boleta o factura con su cantidad, precio unitario y precio total."
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
                            response_schema=Boleta,
                        ),
                    )
                    break
                except Exception as e:
                    ultimo_error = f"{m}: {e}"
            if respuesta is not None:
                break
            time.sleep(2)
            
        if respuesta is not None:
            datos_boleta = Boleta.model_validate_json(respuesta.text)
            
            filas = []
            suma_total = 0
            for p in datos_boleta.items:
                filas.append({
                    "Producto": p.nombre,
                    "Cantidad": p.cantidad,
                    "Precio Unitario": p.precio_unitario,
                    "Total": p.precio_total
                })
                suma_total += p.precio_total
                
            df = pd.DataFrame(filas)
            
            st.dataframe(df, use_container_width=True)
            st.subheader(f"Total calculado: ${suma_total:,.0f}")
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Boleta")
                
            st.download_button(
                label="Descargar archivo Excel",
                data=buffer.getvalue(),
                file_name="boleta_escaneada.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.error(f"Servidores saturados temporalmente. Último detalle: {ultimo_error}")
