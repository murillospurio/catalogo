from flask import Flask, request, jsonify
import requests
import time

app = Flask(__name__)

# ===== CONFIGURAÇÕES =====
# Número de série da máquina de cartão (único)
NUMERO_SERIE_MAQUINA = "8701372447323147"

# IP do ESP32 correspondente a essa máquina
ESP32_URL = "http://192.168.5.57/dispense"

# ===== FUNÇÃO SIMULADA DE PAGAMENTO =====
def processa_pagamento_maquininha(valor):
    """
    Simula envio do pagamento para a máquina de cartão
    e retorna True se aprovado.
    Substitua por integração real com a maquininha se necessário.
    """
    print(f"Enviando pagamento de {valor} para a máquina {NUMERO_SERIE_MAQUINA}...")
    time.sleep(1)  # simula tempo de processamento
    print("Pagamento aprovado pela máquina!")
    return True  # sempre aprova para teste

# ===== ROTA PARA RECEBER PEDIDO DO CATÁLOGO =====
@app.route("/criar_pedido", methods=["POST"])
def criar_pedido():
    data = request.get_json()
    if not data or "items" not in data:
        return jsonify({"error": "JSON inválido"}), 400

    # Calcula o total do pedido
    total = sum(item["qty"] * item.get("price", 0) for item in data["items"])
    print(f"Total do pedido: {total}")

    # Processa pagamento via maquininha
    aprovado = processa_pagamento_maquininha(total)

    if aprovado:
        # Pedido aprovado → envia para o ESP32
        pedido = {
            "status": "paid",
            "items": data["items"]
        }

        try:
            resp = requests.post(ESP32_URL, json=pedido, timeout=5)
            if resp.status_code == 200:
                print("✅ Pedido enviado para o ESP32 com sucesso!")
                return jsonify({"status": "pedido aprovado e enviado para ESP32"}), 200
            else:
                print("❌ Falha ao enviar pedido para ESP32")
                return jsonify({"error": "Falha ao enviar pedido para ESP32"}), 500
        except requests.exceptions.RequestException as e:
            print("❌ Erro na requisição para ESP32:", e)
            return jsonify({"error": str(e)}), 500
    else:
        print("❌ Pagamento não aprovado")
        return jsonify({"status": "pagamento não aprovado"}), 400

# ===== RODAR APP =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
