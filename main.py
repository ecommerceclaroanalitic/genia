
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import os, json
from google.analytics.data_v1beta import BetaAnalyticsDataClient, types
import google.generativeai as genai
from fastapi.middleware.cors import CORSMiddleware
from gtts import gTTS
from google.cloud import storage

# ==============================================
# 🔹 CONFIGURACIÓN
# ==============================================

BUCKET_NAME = "speech_cache"
CACHE_FILE = "speech_cache.json"  # Nombre dentro del bucket
LOCAL_CACHE = "/tmp/speech_cache.json"  # Temporal en el contenedor

PROPERTY_ID = "337084916"
PATH_CREDENTIALS = "credentials.json"
MODEL_NAME = "models/gemini-2.5-flash"
CACHE_FILE = "speech_cache.json"

GOOGLE_API_KEY = "AIzaSyBDkfkuJFnr0YEMzN3fRPt1XldlVsCku-Q"
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
def obtener_producto_top():
    """Consulta GA4 por el producto más vendido del día anterior"""
    if client is None:
        # Si no hay credenciales, usar un producto simulado
        return "Producto de prueba", 15000.0

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
        limit=1
    )

    response = client.run_report(request)

    if not response.rows:
        return None, None

    producto = response.rows[1].dimension_values[0].value
    ingresos = float(response.rows[0].metric_values[0].value)
    return producto, ingresos

def formatear_nombre_usuario(nombre_raw: str) -> str:
    """Devuelve solo el primer nombre, con la primera letra en mayúscula."""
    if not nombre_raw:
        return ""
    nombre = nombre_raw.strip().split(" ")[0]  # toma solo la primera palabra
    return nombre.capitalize()  # primera letra mayúscula, resto minúscula

def generar_speech_producto(nombre, descripcion=None, beneficios=None, user_name=None):

    """Genera un texto publicitario con Gemini, personalizado si se recibe un nombre."""
    nombre_usuario = formatear_nombre_usuario(user_name)

    saludo = f"Saluda al usuario llamado {nombre_usuario} de forma natural en el mensaje." if nombre_usuario else ""

    """Genera un texto publicitario con Gemini"""
    prompt = f"""
    Eres un experto en marketing digital y narración comercial.
    Crea un mensaje para un popup con las siguientes caracterisiticas: breve, natural, agradable, convincente, y si tiene emojis deja solo el emoji sin ninguna descricion, el mensaje tal cual para copiar y pegar y solo un opción pues ese mensaje tiene una integracion directa con mi sitio web, para promocionar el siguiente producto de una tienda online, ademas evoita dejar copmentarios como, claroq eu si aqui esta el speech, y tambien evtia coocar valores pues esa es informaicon interna de la empresa, ademas redactalo de tal manera que se exalte una experiencia para la vida y que este acorde con la epoca del año en colombia, y dentro del mensaje deja resaltado el nombre del producto y en lo posible el mensaje debe estar acorde con el genero del nombre o dejarlo de manera mas generica, y tambien tener en cuenta que estamos cerca a la temporada navideña, mencionar que es el producto mas vendido y deb tenr una longitud maxima de 15 palabras

    🛍️ Producto: {nombre}
    📝 Descripción: {descripcion or "No disponible"}
    ✅ Beneficios: {beneficios or "No especificados"}

     {saludo}

    Lenguaje: español neutro.
    """

    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(prompt)
    return response.text.strip()


def generar_cache_diaria():
    """Consulta GA4 y genera nuevo cache diario"""
    producto, ingresos = obtener_producto_top()
    if not producto:
        raise ValueError("No se encontró producto más vendido.")

    descripcion = f"Producto destacado con ventas de ${ingresos:,.2f} el día anterior."
    beneficios = "Alta demanda y preferido por nuestros clientes."
    speech = generar_speech_producto(producto, descripcion, beneficios)

    data = {
        "fecha": datetime.today().strftime("%Y-%m-%d"),
        "producto": producto,
        "ingresos": ingresos,
        "speech": speech,
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
    """Devuelve el speech del producto más vendido del día (usa cache diaria)"""
    try:
        cache = cargar_cache()

        # Si no existe o está desactualizado → regenerar
        if cache_desactualizado(cache):
            cache = generar_cache_diaria()
           # Personalizar mensaje con el nombre (si lo hay)
        if user_name:
            nombre = formatear_nombre_usuario(user_name)
            cache["speech"] = f"¡Hola {nombre}! {cache['speech']}"

        return cache

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/update-cache")
def update_cache():
    """Permite regenerar manualmente la cache"""
    try:
        data = generar_cache_diaria()
        return {"status": "ok", "message": "Cache actualizada correctamente", "producto": data["producto"]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==============================================
# 🔹 Ejecución local (para desarrollo)
# ==============================================
# uvicorn main:app --host 0.0.0.0 --port 10000
