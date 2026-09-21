"""
Script de prueba y validación de facturación para Gemini Nano Banana (gemini-2.5-flash-image).
Ejecuta una petición de generación de imagen para confirmar que la cuota 'limit: 0' fue levantada.
"""
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("[ERROR] No se encontro GEMINI_API_KEY en .env")
    sys.exit(1)

print(f"[*] Usando GEMINI_API_KEY: {api_key[:10]}...{api_key[-4:]}")
client = genai.Client(api_key=api_key)

print("[*] Enviando petición a 'gemini-2.5-flash-image'...")
try:
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents="A photorealistic clean red athletic t-shirt on a neutral grey hanger, high detail, studio lighting.",
    )
    
    image_saved = False
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if part.inline_data:
                output_path = "test_output_gemini.png"
                with open(output_path, "wb") as f:
                    f.write(part.inline_data.data)
                print(f"[EXITO] Imagen generada correctamente guardada en: {output_path}")
                print(f"[EXITO] Tamano de imagen: {len(part.inline_data.data)} bytes")
                image_saved = True
            elif part.text:
                print(f"[TEXTO]: {part.text}")
                
    if not image_saved:
        print("[AVISO] No se encontro bloque de imagen inline en la respuesta.")
except Exception as e:
    print("\n[ERROR AL GENERAR]:")
    print(type(e), e)
    print("\nSi el error es '429 RESOURCE_EXHAUSTED limit: 0':")
    print("-> La facturación todavía no impactó en los servidores de Google o la API Key pertenece a otro proyecto sin facturación.")
