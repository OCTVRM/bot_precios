# Imagen base ligera de Python para producción
FROM python:3.11-slim

# Evitar escritura de archivos .pyc y buffer en stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente de la aplicación
COPY src/ ./src/

# Usuario sin privilegios por seguridad
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Comando de inicio del daemon
CMD ["python", "-m", "src.main"]
