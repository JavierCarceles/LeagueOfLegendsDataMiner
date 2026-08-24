import time
import json
from pynput import keyboard, mouse

class MacroRecorder:
    def __init__(self):
        self.events = []
        self.is_recording = False
        self.start_time = 0
        self.mouse_listener = None
        self.keyboard_listener = None

    def on_press(self, key):
        if key == keyboard.Key.f1 and not self.is_recording:
            print("\n[+] Grabación INICIADA. Realiza tus acciones y presiona F2 para detener.")
            self.events = []
            self.is_recording = True
            self.start_time = time.time()
            
        elif key == keyboard.Key.f2 and self.is_recording:
            print("\n[-] Grabación DETENIDA.")
            self.is_recording = False
            if self.mouse_listener:
                self.mouse_listener.stop()
            return False  # Detiene el listener del teclado
            
        elif self.is_recording:
            try:
                k = key.char  # Teclas normales (letras, números)
            except AttributeError:
                k = str(key)  # Teclas especiales (Enter, Shift, etc.)
            self.events.append(('k_press', k, time.time() - self.start_time))

    def on_release(self, key):
        if self.is_recording and key != keyboard.Key.f1 and key != keyboard.Key.f2:
            try:
                k = key.char
            except AttributeError:
                k = str(key)
            self.events.append(('k_release', k, time.time() - self.start_time))

    def on_click(self, x, y, button, pressed):
        if self.is_recording:
            self.events.append(('m_click', x, y, str(button), pressed, time.time() - self.start_time))

    def grabar(self):
        print("\nEsperando a que presiones F1 para empezar a grabar...")
        
        # Iniciamos los listeners
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        
        self.mouse_listener.start()
        self.keyboard_listener.start()
        
        # Esperamos hasta que el listener del teclado se detenga (al presionar F2)
        self.keyboard_listener.join()
        
        # Pedimos el nombre para guardar
        nombre = input("Introduce un nombre para guardar la macro (ej. mi_macro): ")
        if not nombre.endswith(".json"):
            nombre += ".json"
            
        with open(nombre, 'w') as f:
            json.dump(self.events, f)
        print(f"[*] Macro guardada exitosamente en '{nombre}'.")

    def _parse_key(self, key_str):
        """Convierte el string de la tecla guardada de vuelta a un objeto Key o string"""
        if key_str and key_str.startswith("Key."):
            key_name = key_str.split(".")[1]
            return getattr(keyboard.Key, key_name)
        return key_str

    def reproducir(self):
        nombre = input("\nIntroduce el nombre de la macro a reproducir (ej. mi_macro): ")
        if not nombre.endswith(".json"):
            nombre += ".json"
            
        try:
            with open(nombre, 'r') as f:
                saved_events = json.load(f)
        except FileNotFoundError:
            print("[!] Error: El archivo no existe.")
            return

        print("Reproduciendo en 3 segundos. No toques el ratón ni el teclado...")
        time.sleep(3)

        k_controller = keyboard.Controller()
        m_controller = mouse.Controller()

        last_time = 0
        for event in saved_events:
            action = event[0]
            current_time = event[-1]
            
            # Esperar el tiempo exacto entre acciones
            time.sleep(current_time - last_time)
            last_time = current_time

            if action == 'k_press':
                key = self._parse_key(event[1])
                if key: k_controller.press(key)
                
            elif action == 'k_release':
                key = self._parse_key(event[1])
                if key: k_controller.release(key)
                
            elif action == 'm_click':
                x, y, btn_str, pressed = event[1], event[2], event[3], event[4]
                m_controller.position = (x, y) # Mover el ratón a la posición
                
                # Determinar qué botón se pulsó
                if 'left' in btn_str: btn = mouse.Button.left
                elif 'right' in btn_str: btn = mouse.Button.right
                else: btn = mouse.Button.middle
                
                if pressed:
                    m_controller.press(btn)
                else:
                    m_controller.release(btn)
                    
        print("[*] Reproducción finalizada.")

def main():
    while True:
        print("\n--- MENÚ DEL GRABADOR ---")
        print("1. Grabar nueva macro")
        print("2. Reproducir macro existente")
        print("3. Salir")
        
        opcion = input("Elige una opción (1/2/3): ")
        
        if opcion == '1':
            grabador = MacroRecorder()
            grabador.grabar()
        elif opcion == '2':
            grabador = MacroRecorder()
            grabador.reproducir()
        elif opcion == '3':
            print("Saliendo...")
            break
        else:
            print("[!] Opción no válida.")

if __name__ == "__main__":
    main()