#!/bin/sh
# Espera a que MySQL esté disponible
echo "Esperando a que MySQL esté disponible..."

until nc -h >/dev/null 2>&1; do
  echo "Instalando netcat..."
  apt-get update && apt-get install -y netcat
done

until nc -z db 3306; do
  echo "Esperando a MySQL en db:3306..."
  sleep 2
done

echo "MySQL está listo. Iniciando Sanic..."
exec python server.py