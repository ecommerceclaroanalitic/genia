from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import os, json, random

from google.analytics.data_v1beta import BetaAnalyticsDataClient, types
import google.generativeai as genai
from google.cloud import storage
import gspread
from google.oauth2.service_account import Credentials

# ==============================================
# 🔹 CONFIGURACIÓN
# ==============================================

PROPERTY_ID = "337084916"
PATH_CREDENTIALS = "/etc/secrets/credentials.json"
MODEL_NAME = "models/gemini-2.5-flash"

BUCKET_NAME = "speech_cache"
CACHE_FILE = "speech_cache_multicategoria.json"

GOOGLE_API_KEY = os.getenv("API_KEY_GEMINI")
genai.configure(api_key=GOOGLE_API_KEY)

CATEGORIAS = ["celular", "Portatil", "audifonos"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_file(PATH_CREDENTIALS, scopes=SCOPES)

# ==============================================
# 🔹 VARIABLE GLOBAL PARA DATOS DE SHEETS
# ==============================================
records = []  # Se cargará con el scheduler

# ==============================================
# 🔹 APP + CORS
# ==============================================

app = FastAPI(title="MultiCategory GA4 API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================
# 🔹 CLIENTE GA4
# ==============================================

if os.path.exists(PATH_CREDENTIALS):
    client = BetaAnalyticsDataClient.from_service_account_file(PATH_CREDENTIALS)
    print("✅ GA4 conectado")
else:
    client = None
    print("⚠️ GA4 en modo MOCK")

# ==============================================
# 🔹 FUNCIONES AUXILIARES - GCS
# ==============================================

def get_storage_client():
    return storage.Client.from_service_account_json(PATH_CREDENTIALS)

def cargar_cache():
    try:
        client_gcs = get_storage_client()
        bucket = client_gcs.bucket(BUCKET_NAME)
        blob = bucket.blob(CACHE_FILE)
        
        if not blob.exists():
            print("⚠️ No existe cache en el bucket")
            return None
            
        contenido = blob.download_as_text(encoding="utf-8")
        data = json.loads(contenido)
        print(f"✅ Cache cargado desde GCS - Fecha: {data.get('fecha')}")
        return data
    except Exception as e:
        print(f"❌ ERROR cargar_cache: {type(e).__name__} - {str(e)}")
        return None

def guardar_cache_gcs(data):
    try:
        client_gcs = get_storage_client()
        bucket = client_gcs.bucket(BUCKET_NAME)
        blob = bucket.blob(CACHE_FILE)
        
        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json"
        )
        print(f"✅ Cache actualizado en GCS - Productos guardados: {len(data.get('productos', []))}")
    except Exception as e:
        print(f"❌ ERROR guardar_cache: {type(e).__name__} - {str(e)}")

def cache_desactualizado(cache):
    if not cache:
        return True
    fecha_cache = cache.get("fecha")
    fecha_hoy = datetime.today().strftime("%Y-%m-%d")
    return fecha_cache != fecha_hoy

# ==============================================
# 🔹 FUNCIONES AUXILIARES - FORMATEO Y BÚSQUEDA
# ==============================================

def formatear_nombre_usuario(nombre):
    return nombre.split(" ")[0].capitalize() if nombre else ""

def buscar_datos_producto(producto_nombre):
    producto_nombre = producto_nombre.lower()
    for row in records:
        titulo = row.get("título", "").lower()
        if all(palabra in titulo for palabra in producto_nombre.split()):
            imagen = row.get("enlace imagen") or ""
            enlace = row.get("enlace") or ""
            precio = row.get("sale_price") if row.get("sale_price") else row.get("precio")
            return {
                "titulo": row.get("título"),
                "imagen": imagen,
                "enlace": enlace,
                "precio": precio
            }
    return {
        "titulo": producto_nombre,
        "imagen": "https://tienda.claro.com.co/static/images/vector/logo-claro-blanco.svg",
        "enlace": "https://tienda.claro.com.co",
        "precio": None
    }

# ==============================================
# 🔹 CONSULTA GA4
# ==============================================

def obtener_top5_producto_especifico(keyword, dias=1):
    if client is None:
        print(f"⚠️ Usando datos MOCK para categoría: {keyword}")
        return [
            {"producto": f"{keyword.capitalize()} Demo 1", "ingresos": 18000},
            {"producto": f"{keyword.capitalize()} Demo 2", "ingresos": 15000},
            {"producto": f"{keyword.capitalize()} Demo 3", "ingresos": 12000},
            {"producto": f"{keyword.capitalize()} Demo 4", "ingresos": 9000},
            {"producto": f"{keyword.capitalize()} Demo 5", "ingresos": 8000},
        ]

    fin = datetime.today().strftime("%Y-%m-%d")
    inicio = (datetime.today() - timedelta(days=dias)).strftime("%Y-%m-%d")

    request = types.RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[types.Dimension(name="itemName")],
        metrics=[types.Metric(name="itemRevenue")],
        date_ranges=[types.DateRange(start_date=inicio, end_date=fin)],
        dimension_filter=types.FilterExpression(
            and_group=types.FilterExpressionList(
                expressions=[
                    types.FilterExpression(
                        filter=types.Filter(
                            field_name="eventName",
                            string_filter=types.Filter.StringFilter(
                                value="purchase",
                                match_type=types.Filter.StringFilter.MatchType.EXACT
                            )
                        )
                    ),
                    types.FilterExpression(
                        filter=types.Filter(
                            field_name="customEvent:items_purchased",
                            string_filter=types.Filter.StringFilter(
                                value=keyword,
                                match_type=types.Filter.StringFilter.MatchType.CONTAINS
                            )
                        )
                    ),
                ]
            )
        ),
        order_bys=[types.OrderBy(metric=types.OrderBy.MetricOrderBy(metric_name="itemRevenue"), desc=True)],
        limit=5
    )

    try:
        response = client.run_report(request)
        resultados = [
            {"producto": r.dimension_values[0].value, "ingresos": float(r.metric_values[0].value)}
            for r in response.rows
        ]
        print(f"✅ GA4 consultado para '{keyword}': {len(resultados)} productos encontrados")
        return resultados
    except Exception as e:
        print(f"❌ ERROR consultando GA4 para '{keyword}': {str(e)}")
        return []

# ==============================================
# 🔹 GENERAR SPEECH
# ==============================================

def generar_speech_producto(nombre, user_name=None):
    nombre_usuario = formatear_nombre_usuario(user_name)
    saludo = f"Saluda a {nombre_usuario}." if nombre_usuario else ""

    prompt = f"""
    Eres un experto en marketing digital.
    Crea un mensaje para popup: breve, natural, convincente, máximo 15 palabras.
    Debe mencionar que está entre los productos más vendidos y populares.
    No incluyas valores numéricos.
    No incluir palabras de sobrepromesas y que todo mensaje esté relacionado con que es una característica de tienda claro.
    No incluyas palabras como perfecto, lo mejor, para evitar desprestigiar otras marcas.
    No incluyas explicaciones ni comentarios.
    Se debe incluir que estamos ya estamos iniciando el año 2026, que ya casi vamos a iniciar las clases, que genere algún sentimiento de buena vibra.
    Texto final listo para pegar.
    
    Producto: {nombre}
    {saludo}
    
    Lenguaje: español neutro.
    """
    
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ Error generando speech para '{nombre}': {str(e)}")
        return f"¡Descubre lo mejor de {nombre} en tienda Claro! 🌟 {saludo}"

# ==============================================
# 🔹 GENERAR CACHE MULTICATEGORÍA
# ==============================================

def generar_cache_multicategoria(user_name=None):
    print("🔄 Iniciando generación de cache multicategoría...")
    all_data = []
    
    for categoria in CATEGORIAS:
        print(f"\n📊 Procesando categoría: {categoria}")
        top5 = obtener_top5_producto_especifico(categoria)
        
        for item in top5:
            datos_sheet = buscar_datos_producto(item["producto"])
            speech = generar_speech_producto(item["producto"], user_name)
            
            producto_obj = {
                "producto": datos_sheet["titulo"],
                "categoria": categoria,
                "ingresos": item["ingresos"],
                "speech": speech,
                "imagen": datos_sheet["imagen"],
                "url": datos_sheet["enlace"],
                "precio": datos_sheet["precio"]
            }
            all_data.append(producto_obj)
            print(f"  ✓ {producto_obj['producto']}")
    
    cache_data = {
        "fecha": datetime.today().strftime("%Y-%m-%d"),
        "productos": all_data,
        "total_productos": len(all_data),
        "categorias": CATEGORIAS
    }
    
    guardar_cache_gcs(cache_data)
    
    print(f"\n✅ Cache generado exitosamente: {len(all_data)} productos totales")
    return cache_data

# ==============================================
# 🔹 ACTUALIZACIÓN DE CACHE CON SHEETS
# ==============================================

def actualizar_cache_con_sheets():
    """
    Actualiza el cache existente con los datos más recientes de Google Sheets
    SIN volver a consultar GA4 (mantiene los mismos productos y orden)
    """
    try:
        # Cargar cache actual
        cache = cargar_cache()
        if not cache or not cache.get("productos"):
            print("⚠️ No hay cache para actualizar con datos de Sheets")
            return
        
        print(f"🔄 Actualizando cache con datos frescos de Sheets...")
        productos_actualizados = []
        
        for producto in cache["productos"]:
            nombre_producto = producto["producto"]
            
            # Buscar datos actualizados en Sheets
            datos_sheet = buscar_datos_producto(nombre_producto)
            
            # Actualizar solo los campos que vienen de Sheets
            producto["imagen"] = datos_sheet["imagen"]
            producto["url"] = datos_sheet["enlace"]
            producto["precio"] = datos_sheet["precio"]
            producto["titulo"] = datos_sheet["titulo"]
            # Mantener: categoria, ingresos, speech (estos vienen de GA4/Gemini)
            
            productos_actualizados.append(producto)
        
        # Actualizar cache con datos frescos
        cache["productos"] = productos_actualizados
        cache["ultima_actualizacion_sheets"] = datetime.now().isoformat()
        
        guardar_cache_gcs(cache)
        print(f"✅ Cache actualizado con datos de Sheets: {len(productos_actualizados)} productos")
        
    except Exception as e:
        print(f"❌ ERROR actualizando cache con Sheets: {str(e)}")

def cargar_datos_sheets():
    """Función para recargar datos de Google Sheets Y actualizar cache"""
    global records
    try:
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key("1qM-j9LQ4aC8xjd6LOPy6Rlf4H9L01WQKL5iaZTu4Ll4").worksheet("Google Merchant Center feed - Fee Google Sheets")
        records = sheet.get_all_records()
        print(f"✅ Google Sheets actualizado: {len(records)} productos cargados - {datetime.now()}")
        
        # 🔥 ACTUALIZAR CACHE CON DATOS FRESCOS (sin consultar GA4)
        actualizar_cache_con_sheets()
        
    except Exception as e:
        print(f"❌ ERROR cargando Google Sheets: {str(e)}")

# ==============================================
# 🔹 SCHEDULER - TAREAS PROGRAMADAS
# ==============================================

scheduler = BackgroundScheduler(timezone="America/Bogota")

# ✅ Tarea 1: Actualizar cache GA4 una vez al día (1:00 AM)
def tarea_actualizar_cache_ga4():
    print(f"🔄 [SCHEDULER] Actualizando cache GA4 - {datetime.now()}")
    try:
        cargar_datos_sheets()  # Primero cargar Sheets frescos
        generar_cache_multicategoria()  # Luego generar cache completo
        print("✅ [SCHEDULER] Cache GA4 actualizado exitosamente")
    except Exception as e:
        print(f"❌ [SCHEDULER] Error actualizando cache: {str(e)}")

scheduler.add_job(
    tarea_actualizar_cache_ga4,
    CronTrigger(hour=1, minute=0),  # 1:00 AM diario
    id="cache_ga4_diario",
    replace_existing=True
)

# ✅ Tarea 2: Recargar Google Sheets 4 veces al día (y actualizar cache sin GA4)
scheduler.add_job(
    cargar_datos_sheets,
    CronTrigger(hour=10, minute=2),  # 10:02 AM
    id="sheets_10am",
    replace_existing=True
)

scheduler.add_job(
    cargar_datos_sheets,
    CronTrigger(hour=12, minute=2),  # 12:02 PM
    id="sheets_12pm",
    replace_existing=True
)

scheduler.add_job(
    cargar_datos_sheets,
    CronTrigger(hour=14, minute=2),  # 2:02 PM
    id="sheets_2pm",
    replace_existing=True
)

scheduler.add_job(
    cargar_datos_sheets,
    CronTrigger(hour=16, minute=2),  # 4:02 PM
    id="sheets_4pm",
    replace_existing=True
)

# Cargar datos al iniciar la aplicación
cargar_datos_sheets()

# Iniciar el scheduler
scheduler.start()
print("✅ Scheduler iniciado con tareas programadas")

# ==============================================
# 🔹 ENDPOINTS
# ==============================================

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "API MultiCategoría con Cache lista 🚀",
        "categorias": CATEGORIAS,
        "scheduler_activo": scheduler.running,
        "proximas_ejecuciones": [
            {
                "job": job.id,
                "proxima": str(job.next_run_time)
            } for job in scheduler.get_jobs()
        ]
    }

@app.get("/generate-products")
def generate_products(user_name: str = None):
    try:
        cache = cargar_cache()
        
        if cache_desactualizado(cache):
            print("♻️ Cache desactualizado, generando nuevo cache...")
            try:
                cache = generar_cache_multicategoria(user_name)
            except Exception as e:
                print(f"❌ ERROR generando cache: {str(e)}")
                print("⚠️ Intentando usar cache antiguo como fallback...")
                
                if cache and cache.get("productos"):
                    print("✅ Usando cache antiguo como fallback")
                else:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "error": "No se pudo generar cache y no existe uno previo.",
                            "detalle": str(e)
                        }
                    )
        else:
            print("✅ Usando cache existente del día actual")
        
        productos = cache.get("productos", [])
        
        return {
            "fecha_cache": cache.get("fecha"),
            "total_productos": len(productos),
            "categorias": cache.get("categorias", CATEGORIAS),
            "productos": productos
        }
        
    except Exception as e:
        print(f"❌ ERROR en endpoint generate-products: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.get("/update-cache")
def update_cache(user_name: str = None):
    try:
        print("🔄 Regeneración manual de cache solicitada...")
        cache = generar_cache_multicategoria(user_name)
        
        return {
            "status": "ok",
            "message": "Cache actualizado correctamente",
            "fecha": cache.get("fecha"),
            "total_productos": cache.get("total_productos"),
            "categorias": cache.get("categorias")
        }
    except Exception as e:
        print(f"❌ ERROR en update-cache: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.get("/cache-status")
def cache_status():
    try:
        cache = cargar_cache()
        
        if not cache:
            return {
                "existe": False,
                "mensaje": "No existe cache en el bucket"
            }
        
        return {
            "existe": True,
            "fecha_cache": cache.get("fecha"),
            "fecha_actual": datetime.today().strftime("%Y-%m-%d"),
            "desactualizado": cache_desactualizado(cache),
            "total_productos": cache.get("total_productos", 0),
            "categorias": cache.get("categorias", []),
            "total_records_sheets": len(records),
            "ultima_actualizacion_sheets": cache.get("ultima_actualizacion_sheets", "N/A")
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# Endpoint para forzar recarga de Sheets manualmente
@app.get("/refresh-sheets")
def refresh_sheets():
    try:
        cargar_datos_sheets()
        return {
            "status": "ok",
            "message": "Datos de Google Sheets actualizados y cache sincronizado",
            "total_records": len(records),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# Cerrar scheduler al apagar la app
@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
    print("🛑 Scheduler detenido")
