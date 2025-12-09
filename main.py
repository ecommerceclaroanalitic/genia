
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import os, json
from google.analytics.data_v1beta import BetaAnalyticsDataClient, types
import google.generativeai as genai
from fastapi.middleware.cors import CORSMiddleware
from gtts import gTTS
from google.cloud import storage
import random

# ==============================================
# 🔹 CONFIGURACIÓN
# ==============================================

BUCKET_NAME = "speech_cache"
CACHE_FILE = "speech_cache.json"  # Nombre dentro del bucket
LOCAL_CACHE = "/tmp/speech_cache.json"  # Temporal en el contenedor

PROPERTY_ID = "337084916"
PATH_CREDENTIALS = "/etc/secrets/credentials.json"
MODEL_NAME = "models/gemini-2.5-flash"
CACHE_FILE = "speech_cache.json"

GOOGLE_API_KEY = os.getenv("API_KEY_GEMINI")
genai.configure(api_key=GOOGLE_API_KEY)

app = FastAPI(title="Daily Speech API", version="1.0")
# ==============================================
# 🔹 CORS (permite solicitudes desde el sitio de Claro)
# ==============================================
# --- ⚙️ Configuración CORS (colócala justo después de crear `app`) ---
origins = [
    "https://tienda.claro.com.co",
    "https://www.tienda.claro.com.co",
    "https://pop-up-tienda-claro.onrender.com",
    "*",  # para pruebas; puedes quitarlo en producción
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ==============================================
# 🔹 CLIENTE GA4 (manejo seguro)
# ==============================================
if os.path.exists(PATH_CREDENTIALS):
    client = BetaAnalyticsDataClient.from_service_account_file(PATH_CREDENTIALS)
else:
    client = None
    print("⚠️ Advertencia: No se encontró el archivo credentials.json, se usará modo sin conexión a GA4.")

# ==============================================
# 🔹 FUNCIONES AUXILIARES
# ==============================================
def obtener_productos_top5():
    """Consulta GA4 por el top 5 productos del día anterior"""
    if client is None:
        return [
            {"producto": "Producto A", "ingresos": 15000.0},
            {"producto": "Producto B", "ingresos": 12000.0},
            {"producto": "Producto C", "ingresos": 9000.0},
            {"producto": "Producto D", "ingresos": 8500.0},
            {"producto": "Producto E", "ingresos": 8000.0},
        ]

    ayer = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    request = types.RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[types.Dimension(name="itemName")],
        metrics=[types.Metric(name="itemRevenue")],
        date_ranges=[types.DateRange(start_date=ayer, end_date=ayer)],
        order_bys=[
            types.OrderBy(
                metric=types.OrderBy.MetricOrderBy(metric_name="itemRevenue"),
                desc=True
            )
        ],
        limit=5
    )

    response = client.run_report(request)
    top5 = []

    for row in response.rows:
        top5.append({
            "producto": row.dimension_values[0].value,
            "ingresos": float(row.metric_values[0].value)
        })

    return top5


def formatear_nombre_usuario(nombre_raw: str) -> str:
    """Devuelve solo el primer nombre, con la primera letra en mayúscula."""
    if not nombre_raw:
        return ""
    nombre = nombre_raw.strip().split(" ")[0]  # toma solo la primera palabra
    return nombre.capitalize()  # primera letra mayúscula, resto minúscula

def generar_speech_producto(nombre, descripcion=None, beneficios=None, user_name=None):
    """Genera un speech para un producto específico"""
    nombre_usuario = formatear_nombre_usuario(user_name)
    saludo = f"Saluda al usuario llamado {nombre_usuario} de forma natural." if nombre_usuario else ""

    prompt = f"""
    Eres un experto en marketing digital.
    Crea un mensaje para popup: breve, natural, convincente, máximo 15 palabras.
    Debe mencionar que esta entre los productos más vendidos y populares.
    No incluyas valores numéricos.
    No incluir palabras de sobrepromesas y que todo mensaje este relacionado con que es una caracteristica de tienda claro.
    No incluyas palbras como perfecto, lo mejor, para evitar desprestigiar otras marcas.
    No incluyas explicaciones ni comentarios.
    Se debe incluir que estamos en la epoca de las fiestas de fin de año y navidad y que genere algun sentimeinto de buena vibra
    Texto final listo para pegar.
    
    Producto: {nombre}
    Descripción: {descripcion or "No disponible"}
    Beneficios: {beneficios or "No especificados"}

    {saludo}

    Lenguaje: español neutro.
    """

    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(prompt)
    return response.text.strip()


def generar_cache_diaria():
    """Genera cache con speech para cada uno de los top 5"""

    top5 = obtener_productos_top5()
    if not top5:
        raise ValueError("No se encontraron productos.")

    speeches = []

    for item in top5:
        nombre = item["producto"]
        ingresos = item["ingresos"]

        descripcion = "Producto destacado del día anterior."
        beneficios = "Muy solicitado por los usuarios."

        speech = generar_speech_producto(nombre, descripcion, beneficios)

        speeches.append({
            "producto": nombre,
            "speech": speech
        })

    data = {
        "fecha": datetime.today().strftime("%Y-%m-%d"),
        "speeches": speeches  # ← ahora guarda varios
    }

    guardar_cache_gcs(data)
    return data


def get_storage_client():
    """Devuelve cliente autenticado de Google Cloud Storage"""
    return storage.Client.from_service_account_json(PATH_CREDENTIALS)


def cargar_cache():
    """Lee cache desde el bucket de Google Cloud Storage"""
    try:
        client = get_storage_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(CACHE_FILE)

        if not blob.exists():
            return None

        contenido = blob.download_as_text(encoding="utf-8")
        return json.loads(contenido)

    except Exception as e:
        print("⚠️ Error cargando cache desde GCS:", e)
        return None


def guardar_cache_gcs(data):
    """Guarda el cache en el bucket"""
    try:
        client = get_storage_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(CACHE_FILE)

        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json"
        )
        print("✅ Cache actualizado en GCS.")
    except Exception as e:
        print("⚠️ Error subiendo cache a GCS:", e)


def cache_desactualizado(cache):
    """Verifica si la cache pertenece a otro día"""
    if not cache:
        return True
    return cache["fecha"] != datetime.today().strftime("%Y-%m-%d")


# ==============================================
# 🔹 ENDPOINTS
# ==============================================
@app.get("/")
def root():
    """Health check para Render"""
    return {"status": "ok", "message": "API de pop-up lista 🚀"}


@app.get("/generate-speech")
def generate_speech_endpoint(user_name: str = None):
    try:
        cache = cargar_cache()

        if cache_desactualizado(cache):
            cache = generar_cache_diaria()

        # Elegir 1 speech random
        speech_item = random.choice(cache["speeches"])

        speech_final = speech_item["speech"]

        # Personalización con nombre
        if user_name:
            nombre = formatear_nombre_usuario(user_name)
            speech_final = f"¡Hola {nombre}! {speech_final}"

        return {
            "producto": speech_item["producto"],
            "speech": speech_final
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/update-cache")
def update_cache():
    """Permite regenerar manualmente la cache"""
    try:
        data = generar_cache_diaria()
        return {"status": "ok", "message": "Cache actualizada correctamente"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==============================================
# 🔹 Ejecución local (para desarrollo)
# ==============================================
# uvicorn main:app --host 0.0.0.0 --port 10000
