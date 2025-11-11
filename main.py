from fastapi import FastAPI
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import os, json
from google.analytics.data_v1beta import BetaAnalyticsDataClient, types
import google.generativeai as genai
from fastapi.middleware.cors import CORSMiddleware
from gtts import gTTS

# ==============================================
# 🔹 CONFIGURACIÓN
# ==============================================
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://tienda.claro.com.co"],  # dominio autorizado
    allow_credentials=True,
    allow_methods=["*"],
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

    producto = response.rows[0].dimension_values[0].value
    ingresos = float(response.rows[0].metric_values[0].value)
    return producto, ingresos


def generar_speech_producto(nombre, descripcion=None, beneficios=None):
    """Genera un texto publicitario con Gemini"""
    prompt = f"""
    Eres un experto en marketing digital y narración comercial.
    Crea un mensaje para un popup breve, natural, agradable y convincente, y sit iene emojis deja solo el emoji sin ninguna descricion, el mensaje tal cual para copiar y pegar y solo un opción, pues ese mensaje tiene una integracion directa con mi sitio web, para promocionar el siguiente producto de una tienda online, ademas evoita dejar copmentarios como, claroq eu si aqui esta el speech, y tambien evtia coocar valores pues esa es informaicon interna de la empresa, ademas redactalo de tal manera que se exalte una experiencia para la vida y que este acorde con la epoca del año en colombia

    🛍️ Producto: {nombre}
    📝 Descripción: {descripcion or "No disponible"}
    ✅ Beneficios: {beneficios or "No especificados"}

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

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


def cargar_cache():
    """Lee el archivo de cache si existe"""
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


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
            cache["speech"] = f"¡Hola {user_name}! {cache['speech']}"

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
