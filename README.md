# 🤖 Bot Rastreador de Precios y Alertas con Enlaces de Afiliado

Sistema modular en **Python 3.11+** para rastrear automáticamente fluctuaciones de precios en tiendas de comercio electrónico (Amazon, Mercado Libre y tiendas genéricas) y publicar alertas de descuento enriquecidas con enlaces de afiliado en canales o grupos de Telegram.

---

## 🏛️ Arquitectura del Sistema

```text
bot_precios/
├── stores.json               # Configuración declarativa de tiendas (Falabella, Paris, Ripley, etc.)
├── monitored_urls.json       # Lista de productos específicos a vigilar (con targets y umbrales)
├── .env.example              # Plantilla de variables de entorno
├── requirements.txt          # Dependencias de producción
├── Dockerfile                # Imagen para despliegue en servidor / Railway / Render
├── docker-compose.yml        # Orquestación de contenedores
├── README.md                 # Documentación técnica
└── src/
    ├── config.py             # Configuración centralizada con Pydantic Settings
    ├── database.py           # Conexión SQLAlchemy Asíncrona (SQLite / Supabase Postgres)
    ├── models.py             # Modelos ORM: Product y PriceHistory
    ├── scrapers/             # Módulo desacoplado de extracción
    │   ├── base.py           # BaseScraper, limpiador de precios y rotación de cabeceras/UA
    │   ├── store_config.py   # Cargador y buscador de reglas de stores.json
    │   ├── configurable.py   # Scraper dinámico multinivel (JSON-LD, Next.js, selectores CSS)
    │   ├── amazon.py         # Scraper de Amazon + inyección de tag de asociado
    │   ├── mercadolibre.py   # Scraper Mercado Libre (JSON-LD Schema.org + CSS)
    │   └── registry.py       # Factory / Dispatcher automático de scrapers
    ├── services/
    │   ├── price_service.py  # Lógica de detección de descuentos, sincronización y anti-spam
    │   └── notifier.py       # Formateo visual HTML para Telegram y botones inline
    ├── scheduler.py          # APScheduler (AsyncIOScheduler) con control de concurrencia
    ├── bot.py                # Comandos de Telegram interactivos (/start, /status, /help)
    ├── cli.py                # CLI para gestión de productos (add, list, sync, check, toggle, delete)
    └── main.py               # Orquestador principal y apagado ordenado (graceful shutdown)
```

---

## 🚀 Inicio Rápido

### 1. Requisitos Previos
- Python 3.11 o superior.
- Token de Bot de Telegram (obtenido en [@BotFather](https://t.me/botfather)).
- Canal o grupo de Telegram con el bot agregado como Administrador.

### 2. Instalación y Entorno Virtual

```bash
# Crear entorno virtual
python -m venv .venv

# Activar entorno virtual
# En Windows (PowerShell):
.venv\Scripts\Activate.ps1
# En Linux / macOS:
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

### 3. Configuración de Variables (`.env`)

Copia el archivo de ejemplo:
```bash
cp .env.example .env
```

Edita `.env` con tus credenciales:
```env
# Base de datos local por defecto:
DATABASE_URL=sqlite+aiosqlite:///./bot_precios.db

# Credenciales de Telegram:
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=-1001234567890

# Parámetros de monitoreo:
CHECK_INTERVAL_MINUTES=30
ALERT_COOLDOWN_HOURS=6
DEFAULT_DISCOUNT_THRESHOLD_PERCENT=10.0

# Etiquetas de Afiliado:
AMAZON_AFFILIATE_TAG=mitag-21
MERCADOLIBRE_AFFILIATE_TAG=mitag_ml
```

---

## ☁️ Integración con Supabase (PostgreSQL)

El sistema utiliza **SQLAlchemy Async** de forma nativa. Para conectar tu base de datos de **Supabase**:

1. En tu proyecto de Supabase, dirígete a **Project Settings -> Database -> Connection string -> URI**.
2. Selecciona el modo **Transaction Pooler** (puerto 6543) o **Session** (puerto 5432).
3. Modifica el protocolo a `postgresql+asyncpg://` en tu archivo `.env`:
   ```env
   DATABASE_URL=postgresql+asyncpg://postgres.[PROJECT-REF]:[TU_PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
   ```
4. ¡Listo! El sistema creará automáticamente las tablas (`products` y `price_history`) en Supabase en el primer arranque.

---

## 🌐 Despliegue: ¿Vercel o Servidor Persistente?

> [!IMPORTANT]
> **Vercel es Serverless**: Las funciones de Vercel se suspenden tras cada petición HTTP (timeout de 10 a 60 segundos). No está diseñado para mantener un proceso daemon en segundo plano (`APScheduler` / bucle `asyncio`) corriendo las 24 horas del día.
> 
> **Recomendación de Producción:**
> - **Para el Worker/Bot:** Despliégalo en **Railway**, **Render (Background Worker)**, **Fly.io** o cualquier **VPS** mediante Docker (`docker compose up -d`).
> - **Para una Interfaz Web:** Si en el futuro deseas crear un panel de control web (ej. Next.js, FastAPI o React), **Vercel es el lugar perfecto para desplegar la web**, conectándose a la misma base de datos de **Supabase** que este bot actualiza constantemente.

---

## 🛠️ Uso de la Línea de Comandos (CLI)

El archivo `src/cli.py` permite gestionar el catálogo de productos fácilmente:

### Sincronizar productos desde monitored_urls.json:
Puedes simplemente agregar o editar URLs en el archivo `monitored_urls.json` y cargarlas ejecutando:
```bash
python -m src.cli sync
```
*(Nota: Al iniciar el daemon con `python -m src.main`, este comando se ejecuta automáticamente).*

### Agregar un producto individual vía CLI:
```bash
python -m src.cli add --url "https://www.falabella.com/falabella-cl/product/16843454" --threshold 10 --target-price 499990
```

### Listar todos los productos monitoreados:
```bash
python -m src.cli list
# Filtrar por categoría:
python -m src.cli list --category celulares
```

### 🏷️ Gestión de Categorías y Top 10 Más Vendidos

El bot incluye un sistema de descubrimiento automático del **Top 10 de productos más vendidos / populares por categoría** en todas las tiendas:

#### Listar categorías configuradas:
```bash
python -m src.cli categories
```

#### Sincronizar Top 10 de categorías en la base de datos:
```bash
# Sincronizar todas las categorías:
python -m src.cli sync-categories

# Sincronizar solo una categoría específica (ej. Celulares o Pokemon TCG):
python -m src.cli sync-categories --category celulares --max 10
```

#### Agregar una nueva categoría:
```bash
python -m src.cli add-category --id consolas --name "Consolas y Videojuegos" --threshold 15 --error-threshold 50
```

#### Asociar una URL de tienda a una categoría:
```bash
python -m src.cli add-category-store --category consolas --store falabella --url "https://www.falabella.com/falabella-cl/category/cat20019/Consolas-y-Videojuegos"
```

---

### Forzar una comprobación inmediata de precios:
```bash
python -m src.cli check
```
*(Opcional: verificar solo un producto específico con `--id 1`)*

### Pausar / Activar el monitoreo de un producto:
```bash
python -m src.cli toggle --id 1
```

### Eliminar un producto:
```bash
python -m src.cli delete --id 1
```

---

## 🚨 Detección de Errores de Precio (Glitches / Bugs) y Umbrales

El sistema implementa una lógica de alertas de tres niveles:
1. **Oferta Estándar (15% - 25%):** Notificación destacada con cálculo de ahorro y precio mínimo histórico.
2. **Super Oferta (30% - 45%):** Descuentos agresivos en liquidaciones de inventario.
3. **🚨 ¡Error de Precio / Bug! (>= 50%):** Si un producto se desploma un 50% o más (ej. omisión de un cero en el precio por la tienda), se emite una alerta prioritaria de urgencia máxima en Telegram:
   ```html
   🚨🚨 ¡POSIBLE ERROR DE PRECIO / BUG EN FALABELLA! 🚨🚨
   ⚡ ¡Descuento anómalo del 75.0%! Revisa y compra de inmediato antes de corrección.
   ```

---

## 🏬 Configuración de Tiendas (`stores.json`)

El archivo `stores.json` permite registrar nuevas tiendas y personalizar selectores sin modificar código Python:
- Ya incluye reglas para: **Falabella, Hites, ABC, Paris, Ripley, Entel, Easy, Sodimac, Amazon y Mercado Libre**.
- Soporta detección multinivel:
  1. Microdatos Schema.org / **JSON-LD** (`@type: Product`).
  2. Datos hidratados de **Next.js** (`__NEXT_DATA__`).
  3. Selectores CSS específicos (`selectors.price`, `selectors.title`).
  4. Metadatos de **OpenGraph** (`og:price:amount`, `product:price:amount`).

---

## 🚦 Iniciar el Servicio en Segundo Plano

Para ejecutar el worker completo con programador de tareas y escucha de Telegram:

```bash
python -m src.main
```

O utilizando Docker:
```bash
docker compose up -d --build
```
