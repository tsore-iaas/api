#!/bin/bash

# Arguments : socket_path and port
SOCKET_PATH=$1
PORT=$2

# # Vérifier si un processus socat existe déjà pour ce port
# if pgrep -f "socat.*:$PORT" > /dev/null; then
#     echo "Un processus socat existe déjà pour le port $PORT"
#     exit 0
# fi

# Lancer socat
socat TCP-LISTEN:$PORT,reuseaddr,fork UNIX-CONNECT:$SOCKET_PATH &
SOCAT_PID=$!

# # Attendre que socat soit prêt
# sleep 1

# # Vérifier si le processus est toujours en vie
# if ! kill -0 $SOCAT_PID 2>/dev/null; then
#     echo "Erreur: socat n'a pas pu démarrer correctement"
#     exit 1
# fi

# echo "Socat démarré avec succès (PID: $SOCAT_PID)"