import base64
import json
import psutil
import requests
import urllib3

# Desactivar advertencias de certificados SSL autofirmados locales
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_lcu_credentials():
    # Busca el proceso del cliente de LoL para extraer el puerto y el token de autenticación
    for proc in psutil.process_iter(["name", "cmdline"]):
        if proc.info["name"] == "LeagueClientUx.exe" or (
            proc.info["name"] == "LeagueClient" and proc.info["cmdline"]
        ):
            cmdline = proc.info["cmdline"]
            port = None
            token = None
            for arg in cmdline:
                if arg.startswith("--app-port="):
                    port = arg.split("=")[1]
                elif arg.startswith("--remoting-auth-token="):
                    token = arg.split("=")[1]
            if port and token:
                return port, token
    return None, None


port, token = get_lcu_credentials()

if not port or not token:
    print("Error: No se pudo encontrar el cliente de LoL abierto.")
else:
    # Preparar la autenticación básica para la API local
    credentials = base64.b64encode(f"riot:{token}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {credentials}", "Accept": "application/json"}

    url = f"https://127.0.0.1:{port}/lol-chat/v1/friends"

    response = requests.get(url, headers=headers, verify=False)

    if response.status_code == 200:
        friends_data = response.json()

        # FILTRAR SOLO EL NOMBRE Y EL TAG
        amigos_filtrados = []
        for amigo in friends_data:
            nombre = amigo.get("gameName", "")
            tag = amigo.get("gameTag", "")

            # Si tiene nombre y tag, lo añadimos al nuevo formato
            if nombre and tag:
                amigos_filtrados.append(f"{nombre}#{tag}")
            elif nombre:  # Por si algún amigo antiguo no tiene tag
                amigos_filtrados.append(nombre)

        # Guardar el resultado en un archivo JSON con el formato limpio
        with open("lol_friends.json", "w", encoding="utf-8") as f:
            json.dump(amigos_filtrados, f, indent=4, ensure_ascii=False)

        print(
            "¡Listo! Tus amigos se han exportado correctamente y filtrados en 'lol_friends.json'."
        )
    else:
        print(f"Error al conectar con la API: {response.status_code}")
